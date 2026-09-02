from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db.models import F
from django.utils import timezone

from .models import AMFIScheme, FundNAVCache, Holding, Valuation

logger = logging.getLogger(__name__)


class BaseNAVProvider:
    """Abstract base class for NAV providers (SPEC §3a)."""
    name = 'BASE'

    def fetch_latest(self, scheme_code: str) -> tuple[Decimal, date, str | None, str | None] | None:
        """
        Fetches latest NAV for scheme_code.
        Returns tuple (latest_nav, nav_as_of_date, scheme_name, isin) or None if failed.
        """
        raise NotImplementedError


class MFAPIProvider(BaseNAVProvider):
    """Primary provider: MFapi.in (SPEC §3a)."""
    name = 'MFAPI'

    def fetch_latest(self, scheme_code: str) -> tuple[Decimal, date, str | None, str | None] | None:
        scheme_code = str(scheme_code).strip()
        url = f"https://api.mfapi.in/mf/{scheme_code}/latest"
        
        try:
            import requests
            resp = requests.get(url, headers={'User-Agent': 'TrackMyRupee-NAVFetcher/1.0'}, timeout=8)
            if resp.status_code != 200:
                logger.warning("MFapi.in returned HTTP status %s for scheme %s", resp.status_code, scheme_code)
                return None
            payload = resp.json()
        except Exception:
            # Fallback to urllib with unverified SSL context if requests is unavailable or fails
            try:
                import ssl
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(url, headers={'User-Agent': 'TrackMyRupee-NAVFetcher/1.0'})
                with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                    if r.status != 200:
                        return None
                    payload = json.loads(r.read().decode('utf-8'))
            except Exception as e:
                logger.warning("MFapi.in fetch failed for scheme %s: %s", scheme_code, e)
                return None

        try:
            meta = payload.get('meta', {})
            scheme_name = meta.get('scheme_name')
            
            data_list = payload.get('data', [])
            if not data_list:
                logger.warning("MFapi.in returned no data for scheme %s", scheme_code)
                return None
            
            latest_entry = data_list[0]
            nav_str = latest_entry.get('nav')
            date_str = latest_entry.get('date')  # Format: DD-MM-YYYY
            
            if not nav_str or not date_str:
                return None
            
            nav = Decimal(str(nav_str))
            nav_date = datetime.strptime(date_str, '%d-%m-%Y').date()
            return (nav, nav_date, scheme_name, None)
        except Exception as e:
            logger.warning("MFapi.in parsing failed for scheme %s: %s", scheme_code, e)
            return None


class AMFIProvider(BaseNAVProvider):
    """Fallback provider: AMFI raw text feed NAVAll.txt (SPEC §3a)."""
    name = 'AMFI'
    AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

    def fetch_latest(self, scheme_code: str) -> tuple[Decimal, date, str | None, str | None] | None:
        scheme_code = str(scheme_code).strip()
        lines = []
        try:
            import requests
            resp = requests.get(self.AMFI_URL, headers={'User-Agent': 'TrackMyRupee-NAVFetcher/1.0'}, timeout=12)
            if resp.status_code == 200:
                lines = resp.text.splitlines()
        except Exception:
            try:
                import ssl
                ctx = ssl._create_unverified_context()
                req = urllib.request.Request(self.AMFI_URL, headers={'User-Agent': 'TrackMyRupee-NAVFetcher/1.0'})
                with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
                    if r.status == 200:
                        lines = r.read().decode('utf-8', errors='ignore').splitlines()
            except Exception as e:
                logger.warning("AMFI fetch failed for scheme %s: %s", scheme_code, e)
                return None

        if not lines:
            return None

        for line in lines:
            line = line.strip()
            if not line or ';' not in line:
                continue
            parts = line.split(';')
            # Columns: Scheme Code; ISIN Div Payout/ Growth; ISIN Div Reinvestment; Scheme Name; Net Asset Value; Date
            if len(parts) >= 6 and parts[0].strip() == scheme_code:
                scheme_name = parts[3].strip()
                nav_str = parts[4].strip()
                date_str = parts[5].strip()  # Format: 10-Aug-2026
                isin = parts[1].strip() or parts[2].strip() or None
                
                try:
                    nav = Decimal(nav_str)
                    nav_date = datetime.strptime(date_str, '%d-%b-%Y').date()
                    return (nav, nav_date, scheme_name, isin)
                except (InvalidOperation, ValueError) as ve:
                    logger.warning("AMFI parsing error for scheme %s: %s", scheme_code, ve)
                    return None
        return None


class NAVFetchService:
    """
    Robust fetch service orchestrating two-provider fallback, exponential backoff retries,
    circuit-breaker throttling, cross-user deduplication, and idempotent Valuation upsert (SPEC §3a).
    """

    def __init__(self, primary_provider: BaseNAVProvider = None, fallback_provider: BaseNAVProvider = None):
        self.primary = primary_provider or MFAPIProvider()
        self.fallback = fallback_provider or AMFIProvider()

    def fetch_scheme(self, scheme_code: str, force: bool = False) -> tuple[FundNAVCache | None, bool]:
        """
        Fetches NAV for scheme_code and updates FundNAVCache + Valuation entries for active holdings.
        Returns (FundNAVCache, success_bool).
        """
        scheme_code = str(scheme_code).strip()
        if not scheme_code:
            return (None, False)

        cache, _ = FundNAVCache.objects.get_or_create(scheme_code=scheme_code)
        now = timezone.now()

        # Circuit-breaker check (SPEC §3a point 3)
        # If failure count >= 3 and last attempt was within 3 days, skip unless force=True
        if not force and cache.consecutive_failure_count >= 3 and cache.last_fetch_attempt_at:
            days_since_last_attempt = (now - cache.last_fetch_attempt_at).days
            if days_since_last_attempt < 3:
                logger.info("Circuit breaker active for scheme %s (failures=%s, days_ago=%s). Skipping.",
                            scheme_code, cache.consecutive_failure_count, days_since_last_attempt)
                return (cache, False)

        result = None
        provider_used = None

        # 1. Try primary provider with up to 3 retries (SPEC §3a point 2)
        for attempt in range(3):
            result = self.primary.fetch_latest(scheme_code)
            if result:
                provider_used = self.primary.name
                break
            if attempt < 2:
                time.sleep(0.2 * (2 ** attempt))

        # 2. Try fallback provider if primary failed (SPEC §3a provider fallback)
        if not result:
            logger.info("Primary NAV provider failed for %s. Trying fallback provider.", scheme_code)
            for attempt in range(3):
                result = self.fallback.fetch_latest(scheme_code)
                if result:
                    provider_used = self.fallback.name
                    break
                if attempt < 2:
                    time.sleep(0.2 * (2 ** attempt))

        # 3. Handle success or failure
        if result:
            nav, nav_date, scheme_name, isin = result
            cache.latest_nav = nav
            cache.nav_as_of_date = nav_date
            if scheme_name:
                cache.scheme_name = scheme_name
            if isin:
                cache.isin = isin
            cache.last_fetch_attempt_at = now
            cache.last_fetch_error = None
            cache.consecutive_failure_count = 0
            cache.save()

            # Upsert Valuation rows for all active Holdings of this scheme_code (SPEC §3a point 4)
            active_holdings = Holding.objects.filter(scheme_code=scheme_code, is_active=True)
            for h in active_holdings:
                units = h.units if h.units is not None else Decimal('0')
                val_amount = (units * nav).quantize(Decimal('0.01'))
                
                # Idempotent upsert keyed on (holding, as_of_date, source)
                Valuation.objects.update_or_create(
                    holding=h,
                    as_of_date=nav_date,
                    source='AUTOMATED_NAV',
                    defaults={
                        'value': val_amount,
                        'unit_nav': nav,
                    }
                )
            return (cache, True)
        else:
            # Persistent failure recording
            cache.last_fetch_attempt_at = now
            cache.last_fetch_error = f"Fetch failed on both providers at {now.isoformat()}"
            cache.consecutive_failure_count = F('consecutive_failure_count') + 1
            cache.save()
            cache.refresh_from_db()
            return (cache, False)

    def sync_active_holdings_navs(self, force: bool = False) -> dict:
        """
        Bounded-scope NAV sync (SPEC §3a point 1 & COST_AND_TOKEN_DISCIPLINE §A point 1).
        Only scheme_codes referenced by AT LEAST ONE ACTIVE HOLDING are queried.
        Deduplicated across all users — each distinct scheme_code is fetched ONCE.
        """
        distinct_schemes = list(
            Holding.objects.filter(is_active=True, scheme_code__isnull=False)
            .exclude(scheme_code='')
            .values_list('scheme_code', flat=True)
            .distinct()
        )
        
        results = {'total_schemes': len(distinct_schemes), 'successful': 0, 'failed': 0, 'skipped': 0}
        
        for scheme_code in distinct_schemes:
            cache, success = self.fetch_scheme(scheme_code, force=force)
            if success:
                results['successful'] += 1
            elif cache and cache.consecutive_failure_count >= 3 and not force:
                results['skipped'] += 1
            else:
                results['failed'] += 1
            # Rate-limit conscious delay between calls
            time.sleep(0.1)

        return results

    def sync_amfi_scheme_list(self) -> int:
        """
        Populates/updates AMFIScheme search mirror from AMFI NAVAll.txt feed.
        """
        req = urllib.request.Request(
            AMFIProvider.AMFI_URL,
            headers={'User-Agent': 'TrackMyRupee-SchemeSync/1.0'}
        )
        count = 0
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                lines = resp.read().decode('utf-8', errors='ignore').splitlines()
                
                schemes_to_upsert = []
                for line in lines:
                    line = line.strip()
                    if not line or ';' not in line:
                        continue
                    parts = line.split(';')
                    if len(parts) >= 4:
                        code = parts[0].strip()
                        name = parts[3].strip()
                        isin = parts[1].strip() or parts[2].strip() or None
                        if code and name and code.isdigit():
                            schemes_to_upsert.append(AMFIScheme(
                                scheme_code=code,
                                scheme_name=name[:255],
                                isin=isin[:50] if isin else None
                            ))
                
                if schemes_to_upsert:
                    AMFIScheme.objects.bulk_create(
                        schemes_to_upsert,
                        update_conflicts=True,
                        unique_fields=['scheme_code'],
                        update_fields=['scheme_name', 'isin']
                    )
                    count = len(schemes_to_upsert)
        except Exception as e:
            logger.error("Failed to sync AMFI scheme list: %s", e)
        return count
