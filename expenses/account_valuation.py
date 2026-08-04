from __future__ import annotations

from datetime import date
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


# ---------------------------------------------------------------------------
# DEPOSIT Valuation Strategy (FD & RD — SPEC §2.1)
# ---------------------------------------------------------------------------

def _calculate_fd_current(
    principal: Decimal,
    rate_pct: Decimal,
    start_date: date,
    compounding: str | None,
    maturity_date: date | None = None,
    closed_date: date | None = None,
    today: date | None = None,
) -> Decimal:
    today = today or date.today()
    if start_date > today:
        return principal

    end_date = today
    if maturity_date and end_date > maturity_date:
        end_date = maturity_date
    if closed_date and end_date > closed_date:
        end_date = closed_date

    if start_date > end_date:
        return principal

    days_elapsed = (end_date - start_date).days
    years = Decimal(str(days_elapsed)) / Decimal('365.25')
    rate = rate_pct / Decimal('100')
    comp_freq = compounding or 'SIMPLE'

    if comp_freq == 'SIMPLE':
        value = principal * (Decimal('1') + rate * years)
    elif comp_freq == 'QUARTERLY':
        n = Decimal('4')
        value = principal * ((Decimal('1') + rate / n) ** (n * years))
    elif comp_freq == 'MONTHLY':
        n = Decimal('12')
        value = principal * ((Decimal('1') + rate / n) ** (n * years))
    else:  # ANNUAL
        value = principal * ((Decimal('1') + rate) ** years)

    return value.quantize(Decimal('0.01'))


def _get_rd_installments(account: Account, end_date: date) -> list[tuple[date, Decimal]]:
    """
    Return list of (installment_date, amount) for an RD account up to end_date.
    Per SPEC §2.1 Edge Case 8:
    - Uses actual posted Transfer entries for months where transfers exist.
    - For prior months before the first recorded transfer (e.g. pre-existing RDs started
      before app tracking began) or when no transfers exist at all, falls back to the
      expected schedule.
    - For months from the first recorded transfer onwards where no transfer was posted,
      treats as skipped.
    """
    import calendar
    from .models import Transfer

    start_date = account.deposit_start_date
    if not start_date:
        return []

    inst_amount = account.rd_installment_amount or Decimal('0.00')
    inst_day = account.rd_installment_day or start_date.day
    if inst_day < 1:
        inst_day = 1
    if inst_day > 31:
        inst_day = 31

    # Fetch actual posted transfers up to end_date
    transfers = list(Transfer.objects.filter(
        to_account=account,
        date__gte=start_date,
        date__lte=end_date,
    ).order_by('date'))

    transfers_by_month: dict[tuple[int, int], list[Transfer]] = {}
    first_tx_month: tuple[int, int] | None = None
    if transfers:
        first_tx = transfers[0]
        first_tx_month = (first_tx.date.year, first_tx.date.month)
        for t in transfers:
            key = (t.date.year, t.date.month)
            if key not in transfers_by_month:
                transfers_by_month[key] = []
            transfers_by_month[key].append(t)

    installments: list[tuple[date, Decimal]] = []
    current_year = start_date.year
    current_month = start_date.month

    while True:
        if date(current_year, current_month, 1) > end_date:
            break

        key = (current_year, current_month)
        if key in transfers_by_month:
            for t in transfers_by_month[key]:
                installments.append((t.date, t.amount))
        else:
            is_before_tracking = (first_tx_month is None) or (key < first_tx_month)
            if is_before_tracking and inst_amount > Decimal('0.00'):
                last_day = calendar.monthrange(current_year, current_month)[1]
                day_i = min(inst_day, last_day)
                d_i = date(current_year, current_month, day_i)
                if start_date <= d_i <= end_date:
                    installments.append((d_i, inst_amount))

        if current_month == 12:
            current_month = 1
            current_year += 1
        else:
            current_month += 1

    return installments


def get_baseline_deposit(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal | None:
    """
    Baseline for DEPOSIT strategy:
    For RD: sum of posted installments (from Transfers) or expected installments.
    For FD/lump-sum: account.deposit_principal if set and non-zero, else ledger_balance.
    """
    if ledger_balance is None:
        ledger_balance = _get_account_ledger_balance(account)

    if account.account_type == 'RD':
        if not account.deposit_start_date:
            return ledger_balance
        eval_today = today or date.today()
        end_date = eval_today
        if account.deposit_maturity_date and end_date > account.deposit_maturity_date:
            end_date = account.deposit_maturity_date
        if account.deposit_closed_date and end_date > account.deposit_closed_date:
            end_date = account.deposit_closed_date

        installments = _get_rd_installments(account, end_date)
        if installments:
            return sum((amt for _, amt in installments), Decimal('0.00')).quantize(Decimal('0.01'))
        return ledger_balance

    # FD / lump-sum deposits
    if account.deposit_principal is not None and account.deposit_principal > Decimal('0.00'):
        return account.deposit_principal.quantize(Decimal('0.01'))
    return ledger_balance


def get_current_deposit(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """
    Current accrued value for DEPOSIT strategy:
    For RD: sum of accrued future value for each installment (annuity formula).
    For FD/lump-sum: compound interest capped at maturity_date or closed_date.
    """
    if ledger_balance is None:
        ledger_balance = _get_account_ledger_balance(account)

    if account.deposit_rate is None or account.deposit_start_date is None:
        return ledger_balance

    eval_today = today or date.today()
    end_date = eval_today
    if account.deposit_maturity_date and end_date > account.deposit_maturity_date:
        end_date = account.deposit_maturity_date
    if account.deposit_closed_date and end_date > account.deposit_closed_date:
        end_date = account.deposit_closed_date

    start = account.deposit_start_date
    if start > end_date:
        principal = account.deposit_principal if account.deposit_principal is not None else ledger_balance
        return principal

    if account.account_type == 'RD':
        installments = _get_rd_installments(account, end_date)
        if not installments:
            return ledger_balance

        rate_pct = account.deposit_rate
        comp_freq = account.deposit_compounding or 'QUARTERLY'
        rate = rate_pct / Decimal('100')

        total_val = Decimal('0.00')
        for d_i, amt_i in installments:
            days_i = (end_date - d_i).days
            if days_i < 0:
                days_i = 0
            years_i = Decimal(str(days_i)) / Decimal('365.25')

            if comp_freq == 'SIMPLE':
                val_i = amt_i * (Decimal('1') + rate * years_i)
            elif comp_freq == 'QUARTERLY':
                n = Decimal('4')
                val_i = amt_i * ((Decimal('1') + rate / n) ** (n * years_i))
            elif comp_freq == 'MONTHLY':
                n = Decimal('12')
                val_i = amt_i * ((Decimal('1') + rate / n) ** (n * years_i))
            else:  # ANNUAL
                val_i = amt_i * ((Decimal('1') + rate) ** years_i)

            total_val += val_i

        return total_val.quantize(Decimal('0.01'))

    # FD / lump-sum deposits
    principal = account.deposit_principal if account.deposit_principal is not None else ledger_balance
    if principal is None or principal == Decimal('0.00'):
        return ledger_balance

    return _calculate_fd_current(
        principal=principal,
        rate_pct=account.deposit_rate,
        start_date=start,
        compounding=account.deposit_compounding,
        maturity_date=account.deposit_maturity_date,
        closed_date=account.deposit_closed_date,
        today=eval_today,
    )


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

def get_baseline(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal | None:
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
        return get_baseline_deposit(account, ledger_balance=ledger_balance, today=today)
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


def get_current(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """What it's worth now, in the account's own currency. Always defined."""
    strategy = strategy_for(account.account_type)
    if strategy == STRATEGY.BALANCE:
        return get_current_balance(account)
    elif strategy == STRATEGY.DEPOSIT:
        return get_current_deposit(account, ledger_balance=ledger_balance, today=today)
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


def get_display_value(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """
    What get_net_worth should sum for this account: get_current(account) if
    account.show_accrued_balance else (get_baseline(account) or get_current(account)).
    """
    baseline = get_baseline(account, ledger_balance=ledger_balance, today=today)
    if getattr(account, 'show_accrued_balance', True) or baseline is None:
        return get_current(account, ledger_balance=ledger_balance, today=today)
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
