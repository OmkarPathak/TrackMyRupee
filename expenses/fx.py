from __future__ import annotations

"""
expenses/fx.py
==============
Thin FX conversion service.

This module provides a clean interface for currency conversion that:
  1. Uses FXRate DB rows as the source of record (auditable, historical)
  2. Falls back to the live get_exchange_rate() API fetch when no DB row exists
  3. Supports as_of date for historical net-worth snapshots
  4. Single-currency (same currency) → returns Decimal('1.0') with no DB/API call
  5. Records every fetched rate to FXRate via upsert (get_exchange_rate already does this)

Usage:
    from expenses.fx import FXService

    # Convert amount to user's base currency (live rate)
    converted = FXService.to_base(amount=Decimal('100'), from_ccy='$', user=user)

    # Historical rate (for snapshot at a past date)
    converted = FXService.to_base(amount, '$', user, as_of=date(2024, 1, 1))

    # Build FX map for all currencies at once (used in net-worth computation)
    fx_map = FXService.build_rate_map(currencies={'$', '€'}, base_ccy='₹')
    rate = fx_map.get('$', Decimal('1.0'))
"""

import logging
from datetime import date as date_type
from decimal import Decimal

from django.utils import timezone

from .models import FXRate
from .utils import get_exchange_rate

logger = logging.getLogger(__name__)

# Symbol → ISO 4217 code mapping (mirrors utils.get_exchange_rate)
SYMBOL_TO_CODE: dict[str, str] = {
    '₹': 'INR',
    '$': 'USD',
    '€': 'EUR',
    '£': 'GBP',
    '¥': 'JPY',
    'A$': 'AUD',
    'C$': 'CAD',
    'CHF': 'CHF',
    '元': 'CNY',
    '₩': 'KRW',
}


def _to_code(ccy: str) -> str:
    return SYMBOL_TO_CODE.get(ccy, ccy)


class FXService:
    """Currency conversion service backed by FXRate model with live-fetch fallback."""

    @classmethod
    def rate(
        cls,
        from_ccy: str,
        to_ccy: str,
        as_of: date_type | None = None,
    ) -> Decimal:
        """
        Return the exchange rate from_ccy → to_ccy.

        Args:
            from_ccy: Source currency (symbol or ISO code).
            to_ccy:   Target currency (symbol or ISO code).
            as_of:    If provided, find the latest FXRate row with as_of_date ≤ this date.
                      If None (default), use the most recently stored rate or a live fetch.

        Returns:
            Decimal rate. Returns 1.0 for same-currency pairs with no DB/API call.
        """
        from_code = _to_code(from_ccy)
        to_code = _to_code(to_ccy)

        if from_code == to_code:
            return Decimal('1.0')

        # Try to find a stored rate in FXRate
        stored = cls._get_stored_rate(from_code, to_code, as_of)
        if stored is not None:
            return stored

        # Fallback: live API fetch (also records the rate to FXRate via upsert)
        try:
            return get_exchange_rate(from_ccy, to_ccy)
        except Exception as exc:
            logger.warning(
                "FXService.rate: live fetch failed for %s→%s (as_of=%s): %s",
                from_code, to_code, as_of, exc,
            )
            raise

    @classmethod
    def _get_stored_rate(
        cls,
        from_code: str,
        to_code: str,
        as_of: date_type | None,
    ) -> Decimal | None:
        """Look up a stored FXRate row. Returns None if not found."""
        qs = FXRate.objects.filter(from_currency=from_code, to_currency=to_code)

        if as_of is not None:
            # Historical: latest row with as_of_date ≤ target date
            qs = qs.filter(as_of_date__lte=as_of)
        # Most recent row
        row = qs.order_by('-as_of_date', '-created_at').first()
        if row is not None:
            return row.rate
        return None

    @classmethod
    def to_base(
        cls,
        amount: Decimal,
        from_ccy: str,
        user,
        as_of: date_type | None = None,
    ) -> Decimal:
        """
        Convert amount from from_ccy into the user's base currency.

        Args:
            amount:   Amount in from_ccy.
            from_ccy: Source currency.
            user:     Django User instance; base_ccy = user.profile.currency.
            as_of:    Optional historical date for snapshot reproduction.

        Returns:
            Decimal amount in base currency, quantized to 2dp.
        """
        try:
            base_ccy = user.profile.currency
        except Exception:
            base_ccy = '₹'

        if _to_code(from_ccy) == _to_code(base_ccy):
            return amount.quantize(Decimal('0.01'))

        fx = cls.rate(from_ccy, base_ccy, as_of=as_of)
        return (amount * fx).quantize(Decimal('0.01'))

    @classmethod
    def build_rate_map(
        cls,
        currencies: set[str],
        base_ccy: str,
        as_of: date_type | None = None,
    ) -> dict[str, Decimal]:
        """
        Build a {currency → rate_to_base} map for all currencies in one or few queries.

        This is the performance-critical path for net-worth computation:
        resolve all FX rates BEFORE the per-account loop, then use dict lookups
        inside the loop (zero DB calls per account/holding/asset).

        Args:
            currencies: Set of currency codes/symbols present in accounts/holdings/assets.
            base_ccy:   The user's base currency.
            as_of:      Optional historical date.

        Returns:
            Dict mapping each currency to its rate_to_base Decimal.
            Same-currency entries are always 1.0.
            Missing currencies (no stored rate) are fetched live.
        """
        base_code = _to_code(base_ccy)
        result: dict[str, Decimal] = {}
        missing: set[str] = set()

        # Map symbols to codes for DB query
        code_to_symbol: dict[str, str] = {}
        source_codes: set[str] = set()
        for ccy in currencies:
            code = _to_code(ccy)
            if code == base_code:
                result[ccy] = Decimal('1.0')
            else:
                source_codes.add(code)
                code_to_symbol[code] = ccy  # store the original symbol

        if not source_codes:
            return result

        # One query: fetch the latest stored rate for each needed currency pair
        qs = FXRate.objects.filter(
            from_currency__in=source_codes,
            to_currency=base_code,
        )
        if as_of is not None:
            qs = qs.filter(as_of_date__lte=as_of)

        # For each from_currency, we want the most recent row.
        # Use DISTINCT ON if available (PostgreSQL), or Python-side dedup.
        # We fetch all rows ordered by (from_currency, -as_of_date) and keep first per currency.
        seen: set[str] = set()
        for row in qs.order_by('from_currency', '-as_of_date', '-created_at'):
            if row.from_currency not in seen:
                seen.add(row.from_currency)
                # Map back to original symbol
                symbol = code_to_symbol.get(row.from_currency, row.from_currency)
                result[symbol] = row.rate

        # Identify which currencies still have no rate
        for code, symbol in code_to_symbol.items():
            if symbol not in result:
                missing.add(symbol)

        # Live-fetch for missing currencies (records to FXRate automatically)
        for ccy in missing:
            try:
                rate = get_exchange_rate(ccy, base_ccy)
                result[ccy] = rate
            except Exception as exc:
                logger.warning(
                    "FXService.build_rate_map: live fetch failed for %s→%s: %s",
                    ccy, base_ccy, exc,
                )
                # Use 1.0 as emergency fallback to avoid crashing net-worth
                result[ccy] = Decimal('1.0')

        return result

    @classmethod
    def convert_using_map(
        cls,
        amount: Decimal,
        from_ccy: str,
        fx_map: dict[str, Decimal],
    ) -> Decimal:
        """
        Convert amount to base currency using a pre-built rate map.
        Zero additional DB queries — pure arithmetic.
        """
        rate = fx_map.get(from_ccy, Decimal('1.0'))
        return (amount * rate).quantize(Decimal('0.01'))
