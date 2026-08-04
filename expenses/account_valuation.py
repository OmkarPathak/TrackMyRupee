from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Sum

from .account_types import STRATEGY, strategy_for

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from .models import Account


def _get_account_ledger_balance(account: Account) -> Decimal:
    """Helper to fetch the current ledger balance for an account."""
    from .ledger_read_service import LedgerReadService
    return LedgerReadService.get_account_balance(account)


# ---------------------------------------------------------------------------
# Strategy Stubs (to be populated in Sessions 2-5)
# ---------------------------------------------------------------------------

def get_baseline_balance(account: Account) -> None:
    """BALANCE strategy has no baseline concept."""
    return None


def get_current_balance(account: Account) -> Decimal:
    return _get_account_ledger_balance(account)


def get_baseline_deposit(account: Account) -> Decimal | None:
    # TODO(session-2): see SPEC.md §2.1 for full DEPOSIT / RD baseline logic
    return _get_account_ledger_balance(account)


def get_current_deposit(account: Account) -> Decimal:
    # TODO(session-2): see SPEC.md §2.1 for full DEPOSIT / RD current accrual math
    return _get_account_ledger_balance(account)


def get_baseline_holdings(account: Account) -> Decimal | None:
    # TODO(session-3): see SPEC.md §2.2 for full HOLDINGS baseline logic
    return _get_account_ledger_balance(account)


def get_current_holdings(account: Account) -> Decimal:
    # TODO(session-3): see SPEC.md §2.2 for full HOLDINGS current logic
    return _get_account_ledger_balance(account)


def get_baseline_revolving_credit(account: Account) -> None:
    """REVOLVING_CREDIT strategy has no baseline concept."""
    return None


def get_current_revolving_credit(account: Account) -> Decimal:
    # TODO(session-4): see SPEC.md §2.3 for full REVOLVING_CREDIT logic
    return _get_account_ledger_balance(account)


def get_baseline_loan(account: Account) -> None:
    """LOAN_OUTSTANDING strategy has no baseline concept for gain toggle."""
    return None


def get_current_loan(account: Account) -> Decimal:
    # TODO(session-5): see SPEC.md §2.4 for full LOAN_OUTSTANDING logic
    return _get_account_ledger_balance(account)


def get_baseline_physical_valuation(account: Account) -> Decimal | None:
    # TODO(session-5): see SPEC.md §2.5 for full PHYSICAL_VALUATION baseline logic
    return _get_account_ledger_balance(account)


def get_current_physical_valuation(account: Account) -> Decimal:
    # TODO(session-5): see SPEC.md §2.5 for full PHYSICAL_VALUATION current logic
    return _get_account_ledger_balance(account)


def get_baseline_insurance_surrender(account: Account) -> Decimal | None:
    # TODO(session-5): see SPEC.md §2.6 for full INSURANCE_SURRENDER baseline logic
    return _get_account_ledger_balance(account)


def get_current_insurance_surrender(account: Account) -> Decimal:
    # TODO(session-5): see SPEC.md §2.6 for full INSURANCE_SURRENDER current logic
    return _get_account_ledger_balance(account)


# ---------------------------------------------------------------------------
# Main Unified Interface (SPEC §0)
# ---------------------------------------------------------------------------

def get_baseline(account: Account) -> Decimal | None:
    """
    What was put in, in the account's own currency. None if the concept
    doesn't apply to this account's strategy (e.g. BALANCE, REVOLVING_CREDIT, LOAN_OUTSTANDING) —
    callers must hide the toggle entirely when this is None, never show a
    meaningless 0.
    """
    strategy = strategy_for(account.account_type)
    if strategy == STRATEGY.BALANCE:
        return get_baseline_balance(account)
    elif strategy == STRATEGY.DEPOSIT:
        return get_baseline_deposit(account)
    elif strategy == STRATEGY.HOLDINGS:
        return get_baseline_holdings(account)
    elif strategy == STRATEGY.REVOLVING_CREDIT:
        return get_baseline_revolving_credit(account)
    elif strategy == STRATEGY.LOAN_OUTSTANDING:
        return get_baseline_loan(account)
    elif strategy == STRATEGY.PHYSICAL_VALUATION:
        return get_baseline_physical_valuation(account)
    elif strategy == STRATEGY.INSURANCE_SURRENDER:
        return get_baseline_insurance_surrender(account)
    return None


def get_current(account: Account) -> Decimal:
    """What it's worth now, in the account's own currency. Always defined."""
    strategy = strategy_for(account.account_type)
    if strategy == STRATEGY.BALANCE:
        return get_current_balance(account)
    elif strategy == STRATEGY.DEPOSIT:
        return get_current_deposit(account)
    elif strategy == STRATEGY.HOLDINGS:
        return get_current_holdings(account)
    elif strategy == STRATEGY.REVOLVING_CREDIT:
        return get_current_revolving_credit(account)
    elif strategy == STRATEGY.LOAN_OUTSTANDING:
        return get_current_loan(account)
    elif strategy == STRATEGY.PHYSICAL_VALUATION:
        return get_current_physical_valuation(account)
    elif strategy == STRATEGY.INSURANCE_SURRENDER:
        return get_current_insurance_surrender(account)
    return get_current_balance(account)


def get_display_value(account: Account) -> Decimal:
    """
    What get_net_worth should sum for this account: get_current(account) if
    account.show_accrued_balance else (get_baseline(account) or get_current(account)).
    """
    baseline = get_baseline(account)
    if getattr(account, 'show_accrued_balance', True) or baseline is None:
        return get_current(account)
    return baseline


# ---------------------------------------------------------------------------
# Interest reporting convention (SPEC §3)
# ---------------------------------------------------------------------------

def get_interest_summary(user: User, start_date=None, end_date=None) -> dict[str, Decimal]:
    """
    Compute DB-side interest summary reportable for a user across a date range.
    Returns {'interest_earned': Decimal, 'interest_charged': Decimal}.
    """
    from .models import Expense, Income

    income_qs = Income.objects.filter(user=user)
    expense_qs = Expense.objects.filter(user=user)

    if start_date:
        income_qs = income_qs.filter(date__gte=start_date)
        expense_qs = expense_qs.filter(date__gte=start_date)
    if end_date:
        income_qs = income_qs.filter(date__lte=end_date)
        expense_qs = expense_qs.filter(date__lte=end_date)

    income_interest = income_qs.filter(
        source_fk__is_interest_category=True
    ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')

    if income_interest == Decimal('0.00'):
        income_interest = income_qs.filter(
            source__iexact='Interest Income'
        ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')

    expense_interest = expense_qs.filter(
        category_fk__is_interest_category=True
    ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')

    if expense_interest == Decimal('0.00'):
        expense_interest = expense_qs.filter(
            category__iexact='Interest Charged'
        ).aggregate(total=Sum('base_amount'))['total'] or Decimal('0.00')

    return {
        'interest_earned': income_interest,
        'interest_charged': expense_interest,
    }
