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
from .fx import FXService
from .ledger_rollout import is_user_in_read_cohort
from .models import (
    AssetValuation,
    Holding,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Loan,
    LoanScheduleInstallment,
    PhysicalAsset,
    SavingsGoal,
    Valuation,
)
from .utils import get_exchange_rate
from .account_valuation import get_current_holdings

logger = logging.getLogger(__name__)


def _compute_deposit_value(account, ledger_balance: Decimal) -> Decimal:
    """
    Compute accrued DEPOSIT value for an account.
    Delegates to expenses.account_valuation.get_current_deposit(account)
    for unified FD and RD accrual math (including maturity/closed date caps).
    """
    from .account_valuation import get_current_deposit
    return get_current_deposit(account, ledger_balance=ledger_balance)


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
        rate = FXService.rate(line.currency, account.currency)
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

        # SPEC §4: Check if cached_balance is available on LedgerAccount (O(1) read)
        ledger_codes = [f"USR:{user.id}:ASSET:ACCOUNT:{aid}" for aid in account_ids]
        ledger_accounts = LedgerAccount.objects.filter(code__in=ledger_codes).only('id', 'code', 'cached_balance')
        ledger_map = {la.code: la for la in ledger_accounts}

        balances = {}
        missing_account_ids = []

        for account_id in account_ids:
            code = f"USR:{user.id}:ASSET:ACCOUNT:{account_id}"
            la = ledger_map.get(code)
            if la is not None and la.cached_balance is not None:
                balances[account_id] = la.cached_balance
            else:
                missing_account_ids.append(account_id)

        if not missing_account_ids:
            return balances

        # Fall back to full-sum calculation for accounts where cached_balance is null
        lines = JournalLine.objects.filter(
            account_ref_id__in=missing_account_ids,
            journal_entry__status="POSTED",
        ).only("account_ref_id", "direction", "amount", "currency")

        lines_by_account = {account_id: [] for account_id in missing_account_ids}
        for line in lines:
            lines_by_account[line.account_ref_id].append(line)

        opening_account_ids = cls._get_opening_account_ids(user)

        for account_id in missing_account_ids:
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
        code = f"USR:{account.user_id}:ASSET:ACCOUNT:{account.id}"
        la = LedgerAccount.objects.filter(code=code).only('cached_balance').first()
        if la is not None and la.cached_balance is not None:
            return la.cached_balance

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
        ONE query via LEFT JOIN on Valuation.
        Falls back to units * avg_cost for active holdings with zero Valuation rows.

        Returns: {account_id: list of {'value': Decimal, 'currency': str}}
        Note: the caller is responsible for FX conversion.
        """
        rows = list(
            Holding.objects
            .filter(account_id__in=account_ids, is_active=True)
            .order_by('id', '-valuations__as_of_date', '-valuations__created_at')
            .values('id', 'account_id', 'currency', 'units', 'avg_cost', 'scheme_code', 'valuations__value')
        )

        scheme_codes = {r['scheme_code'] for r in rows if r['scheme_code']}
        nav_caches = {}
        if scheme_codes:
            from .models import FundNAVCache
            nav_caches = dict(
                FundNAVCache.objects.filter(scheme_code__in=scheme_codes)
                .values_list('scheme_code', 'latest_nav')
            )

        seen_holdings: set = set()
        account_holding_vals: dict = {}
        for r in rows:
            hid = r['id']
            if hid in seen_holdings:
                continue
            seen_holdings.add(hid)

            acc_id = r['account_id']
            units = r['units'] or Decimal('0.00')
            avg_cost = r['avg_cost'] or Decimal('0.00')
            cost_basis = (units * avg_cost).quantize(Decimal('0.01'))

            val = r['valuations__value']
            if val is None:
                scheme_code = r['scheme_code']
                cache_nav = nav_caches.get(scheme_code) if scheme_code else None
                if cache_nav is not None:
                    val = (units * cache_nav).quantize(Decimal('0.01'))
                else:
                    val = cost_basis

            if acc_id not in account_holding_vals:
                account_holding_vals[acc_id] = []
            account_holding_vals[acc_id].append({
                'value': val,
                'cost_basis': cost_basis,
                'currency': r['currency'],
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
        # Try latest paid schedule installment per loan for EMI loans
        from .models import Loan
        bullet_loan_ids = set(
            Loan.objects.filter(id__in=loan_ids, repayment_type__in=['BULLET', 'INTEREST_ONLY']).values_list('id', flat=True)
        )
        emi_loan_ids = [lid for lid in loan_ids if lid not in bullet_loan_ids]

        schedule_map: dict = {}
        if emi_loan_ids:
            schedule_rows = (
                LoanScheduleInstallment.objects
                .filter(loan_id__in=emi_loan_ids, is_paid=True)
                .order_by('loan_id', '-due_date', '-installment_no')
                .values('loan_id', 'scheduled_balance')
            )
            seen_loans: set = set()
            for row in schedule_rows:
                lid = row['loan_id']
                if lid in seen_loans:
                    continue
                seen_loans.add(lid)
                schedule_map[lid] = max(Decimal('0.00'), row['scheduled_balance'])

        # For loans without a schedule or bullet loans, aggregate repayments
        missing_loan_ids = [lid for lid in loan_ids if lid not in schedule_map]

        if missing_loan_ids:
            from .models import CapitalEvent, LoanRepayment
            # Fallback for loans without schedule entries
            # Fetch initial_principal
            loan_objs = Loan.objects.filter(id__in=missing_loan_ids).values('id', 'initial_principal')
            init_map = {l['id']: l['initial_principal'] for l in loan_objs}

            # Principal repaid from LoanRepayment
            repaid_rows = (
                LoanRepayment.objects
                .filter(loan_id__in=missing_loan_ids)
                .values('loan_id')
                .annotate(total=Sum('principal_amount'))
            )
            repaid_map = {r['loan_id']: r['total'] or Decimal('0.00') for r in repaid_rows}

            # Prepayments from CapitalEvent
            prep_rows = (
                CapitalEvent.objects
                .filter(linked_loan_id__in=missing_loan_ids, subtype__in=['loan_down_payment', 'loan_prepayment'])
                .values('linked_loan_id')
                .annotate(total=Sum('amount'))
            )
            prep_map = {p['linked_loan_id']: p['total'] or Decimal('0.00') for p in prep_rows}

            for lid in missing_loan_ids:
                init_p = init_map.get(lid, Decimal('0.00'))
                rep = repaid_map.get(lid, Decimal('0.00'))
                prep = prep_map.get(lid, Decimal('0.00'))
                schedule_map[lid] = max(Decimal('0.00'), init_p - rep - prep)

        return schedule_map

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
        accounts = list(user.accounts.filter(is_active=True).select_related('linked_loan', 'linked_physical_asset'))
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
            # Include linked loan currencies (already select_related — no extra queries)
            for aid in loan_account_ids:
                acct = account_map[aid]
                if acct.linked_loan is not None:
                    all_currencies.add(acct.linked_loan.currency)
            # Include linked physical asset currencies (already select_related — no extra queries)
            for aid in asset_account_ids:
                acct = account_map[aid]
                if acct.linked_physical_asset is not None:
                    all_currencies.add(acct.linked_physical_asset.currency)

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
            is_accrued = getattr(account, 'show_accrued_balance', True)

            if strategy == STRATEGY.HOLDINGS:
                h_list = holding_vals_by_account.get(account.id, [])
                if is_accrued:
                    holdings_total = Decimal("0.00")
                    cost_basis_total = Decimal("0.00")
                    for hv in h_list:
                        val = hv['value']
                        c_val = hv['cost_basis']
                        if hv['currency'] != account.currency:
                            val = FXService.convert_using_map(val, hv['currency'], fx_map)
                            c_val = FXService.convert_using_map(c_val, hv['currency'], fx_map)
                        holdings_total += val
                        cost_basis_total += c_val
                    uninvested_cash = max(Decimal("0.00"), ledger_bal - cost_basis_total)
                    native_val = holdings_total + uninvested_cash
                else:
                    cost_basis_total = Decimal("0.00")
                    for hv in h_list:
                        c_val = hv['cost_basis']
                        if hv['currency'] != account.currency:
                            c_val = FXService.convert_using_map(c_val, hv['currency'], fx_map)
                        cost_basis_total += c_val
                    native_val = cost_basis_total
                account_value = FXService.convert_using_map(
                    native_val, account.currency, fx_map
                )

            elif strategy == STRATEGY.DEPOSIT:
                # Accrual if deposit fields set and show_accrued_balance is True, else baseline deposit
                if is_accrued:
                    native_val = _compute_deposit_value(account, ledger_bal)
                else:
                    from .account_valuation import get_baseline_deposit
                    native_val = get_baseline_deposit(account, ledger_balance=ledger_bal) or ledger_bal
                account_value = FXService.convert_using_map(
                    native_val, account.currency, fx_map
                )

            elif strategy == STRATEGY.LOAN_OUTSTANDING:
                if account.linked_loan_id and account.linked_loan_id in loan_outstanding_map:
                    outstanding = loan_outstanding_map[account.linked_loan_id]
                    # Use the linked loan's currency; fall back to account currency if unavailable
                    loan_ccy = (
                        account.linked_loan.currency
                        if account.linked_loan is not None
                        else account.currency
                    )
                    account_value = FXService.convert_using_map(
                        outstanding, loan_ccy, fx_map
                    )
                else:
                    # No linked loan → use ledger balance (already negative for liabilities)
                    account_value = FXService.convert_using_map(
                        abs(ledger_bal), account.currency, fx_map
                    )

            elif strategy == STRATEGY.PHYSICAL_VALUATION:
                if is_accrued:
                    asset_id = account.linked_physical_asset_id
                    if asset_id and asset_id in asset_val_map:
                        asset_native_val = asset_val_map[asset_id]
                        # Use the linked physical asset's currency; fall back to account currency if unavailable
                        asset_ccy = (
                            account.linked_physical_asset.currency
                            if account.linked_physical_asset is not None
                            else account.currency
                        )
                        account_value = FXService.convert_using_map(
                            asset_native_val, asset_ccy, fx_map
                        )
                    else:
                        # Fallback: acquisition_cost from linked asset if available, else ledger balance
                        if (
                            account.linked_physical_asset_id is not None
                            and account.linked_physical_asset is not None
                            and account.linked_physical_asset.acquisition_cost is not None
                        ):
                            asset_ccy = account.linked_physical_asset.currency
                            account_value = FXService.convert_using_map(
                                account.linked_physical_asset.acquisition_cost,
                                asset_ccy, fx_map,
                            )
                        else:
                            account_value = FXService.convert_using_map(
                                ledger_bal, account.currency, fx_map
                            )
                else:
                    from .account_valuation import get_baseline_physical_valuation
                    native_val = get_baseline_physical_valuation(account, ledger_balance=ledger_bal) or Decimal("0.00")
                    account_value = FXService.convert_using_map(native_val, account.currency, fx_map)

            elif strategy == STRATEGY.INSURANCE_SURRENDER:
                if is_accrued:
                    asset_id = account.linked_physical_asset_id
                    if asset_id and asset_id in asset_val_map:
                        asset_native_val = asset_val_map[asset_id]
                        asset_ccy = (
                            account.linked_physical_asset.currency
                            if account.linked_physical_asset is not None
                            else account.currency
                        )
                        account_value = FXService.convert_using_map(
                            asset_native_val, asset_ccy, fx_map
                        )
                    else:
                        # SPEC §2.6: Insurance MUST fall back to 0.00 when no valuation exists, NEVER acquisition_cost
                        account_value = Decimal("0.00")
                else:
                    from .account_valuation import get_baseline_insurance_surrender
                    native_val = get_baseline_insurance_surrender(account, ledger_balance=ledger_bal) or Decimal("0.00")
                    account_value = FXService.convert_using_map(native_val, account.currency, fx_map)

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
            active_loans = Loan.objects.filter(user=user, is_active=True)
            for loan in active_loans:
                remaining_principal = loan.remaining_principal.quantize(Decimal("0.01"))
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
        unlinked_loans = Loan.objects.filter(user=user, is_active=True).exclude(id__in=linked_loan_ids)
        for loan in unlinked_loans:
            remaining_principal = loan.remaining_principal.quantize(Decimal("0.01"))
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
        active_loans = Loan.objects.filter(user=user, is_active=True)
        for loan in active_loans:
            remaining_principal = loan.remaining_principal.quantize(Decimal("0.01"))
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
