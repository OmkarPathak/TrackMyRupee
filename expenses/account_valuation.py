from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Sum

from .account_types import STRATEGY, strategy_for

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from .models import Account


PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY = {
    'ANNUAL': 'YEARLY',
    'SEMI_ANNUAL': 'SEMIANNUALLY',
    'QUARTERLY': 'QUARTERLY',
    'MONTHLY': 'MONTHLY',
}


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


def get_baseline_holdings(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal | None:
    """
    SPEC §2.2 HOLDINGS Baseline = Σ (units × avg_cost) across active holdings.
    Converted to account currency if holding currency differs.
    """
    from .models import Holding
    from .utils import get_exchange_rate

    active_holdings = Holding.objects.filter(account=account, is_active=True)
    if not active_holdings.exists():
        return Decimal('0.00')

    baseline_total = Decimal('0.00')
    for holding in active_holdings:
        units = holding.units or Decimal('0.00')
        avg_cost = holding.avg_cost or Decimal('0.00')
        val = (units * avg_cost).quantize(Decimal('0.01'))
        if holding.currency and holding.currency != account.currency:
            rate = get_exchange_rate(holding.currency, account.currency)
            val = (val * rate).quantize(Decimal('0.01'))
        baseline_total += val

    return baseline_total.quantize(Decimal('0.01'))


def get_current_holdings(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """
    SPEC §2.2 HOLDINGS Current Value:
    - Σ latest Valuation.value per active holding (falling back to units × avg_cost if zero Valuations exist).
    - Additive-cash fix: includes uninvested ledger_balance sitting in the account.
    """
    from .models import Holding
    from .utils import get_exchange_rate

    ledger_bal = ledger_balance if ledger_balance is not None else _get_account_ledger_balance(account)
    active_holdings = Holding.objects.filter(account=account, is_active=True).prefetch_related('valuations')

    holdings_total = Decimal('0.00')
    cost_basis_total = Decimal('0.00')
    for holding in active_holdings:
        units = holding.units or Decimal('0.00')
        avg_cost = holding.avg_cost or Decimal('0.00')
        cost_val = (units * avg_cost).quantize(Decimal('0.01'))

        latest_val = holding.valuations.order_by('-as_of_date', '-created_at').first()
        if latest_val:
            val = latest_val.value
        else:
            # SPEC §3a point 5: Staleness fallback to FundNAVCache if scheme_code set
            cache_nav = None
            if holding.scheme_code:
                from .models import FundNAVCache
                c = FundNAVCache.objects.filter(scheme_code=holding.scheme_code).first()
                if c and c.latest_nav is not None:
                    cache_nav = c.latest_nav

            if cache_nav is not None:
                val = (units * cache_nav).quantize(Decimal('0.01'))
            else:
                # Fallback to cost basis if no valuation posted yet
                val = cost_val

        if holding.currency and holding.currency != account.currency:
            rate = get_exchange_rate(holding.currency, account.currency)
            val = (val * rate).quantize(Decimal('0.01'))
            cost_val = (cost_val * rate).quantize(Decimal('0.01'))

        holdings_total += val
        cost_basis_total += cost_val

    # Net uninvested cash: max(0, ledger_balance - cost_basis_total)
    # Known limitation: redemptions aren't modeled as ledger-posting events,
    # so fully redeemed holdings return cost basis to uninvested cash approximation.
    uninvested_cash = max(Decimal('0.00'), ledger_bal - cost_basis_total)
    return (holdings_total + uninvested_cash).quantize(Decimal('0.01'))



def get_baseline_revolving_credit(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> None:
    """REVOLVING_CREDIT strategy has no baseline concept."""
    return None


def get_current_revolving_credit(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """SPEC §2.3 REVOLVING_CREDIT Current Value = ledger balance (negative for owed amount)."""
    return ledger_balance if ledger_balance is not None else _get_account_ledger_balance(account)


def get_baseline_loan(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> None:
    """LOAN_OUTSTANDING strategy has no baseline concept for gain toggle."""
    return None


def get_current_loan(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """
    SPEC §2.4 LOAN_OUTSTANDING Current Value (Outstanding Principal):
    If linked_loan exists:
      - For BULLET or INTEREST_ONLY: returns loan.remaining_principal (no early EMI amortization).
      - For EMI: if schedule is up to date (no prepayments after last paid schedule entry),
        returns latest paid LoanScheduleInstallment.scheduled_balance.
      - Fallback: initial_principal - sum(principal_portion) - sum(prepayments).
    Else falls back to ledger balance.
    """
    from .utils import get_exchange_rate

    if not account.linked_loan_id or not account.linked_loan:
        return ledger_balance if ledger_balance is not None else _get_account_ledger_balance(account)

    loan = account.linked_loan
    repayment_type = getattr(loan, 'repayment_type', 'EMI')

    if repayment_type in ('BULLET', 'INTEREST_ONLY'):
        outstanding = loan.remaining_principal
    else:
        # EMI amortizing schedule logic
        from .models import CapitalEvent, LoanScheduleInstallment
        latest_paid = LoanScheduleInstallment.objects.filter(loan=loan, is_paid=True).order_by('-due_date', '-installment_no').first()

        if latest_paid:
            latest_prep = CapitalEvent.objects.filter(
                linked_loan=loan,
                subtype__in=['loan_down_payment', 'loan_prepayment'],
                date__gt=latest_paid.due_date,
            ).exists()
            if not latest_prep:
                outstanding = max(Decimal('0.00'), latest_paid.scheduled_balance)
            else:
                outstanding = loan.remaining_principal
        else:
            outstanding = loan.remaining_principal

    if loan.currency and loan.currency != account.currency:
        rate = get_exchange_rate(loan.currency, account.currency)
        outstanding = (outstanding * rate).quantize(Decimal('0.01'))

    return outstanding.quantize(Decimal('0.01'))


def get_baseline_physical_valuation(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal | None:
    """SPEC §2.5 PHYSICAL_VALUATION Baseline = acquisition_cost of linked asset."""
    from .utils import get_exchange_rate

    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return ledger_balance if ledger_balance is not None else _get_account_ledger_balance(account)

    asset = account.linked_physical_asset
    if asset.acquisition_cost is None:
        return Decimal('0.00')

    cost = asset.acquisition_cost
    if asset.currency and asset.currency != account.currency:
        rate = get_exchange_rate(asset.currency, account.currency)
        cost = (cost * rate).quantize(Decimal('0.01'))

    return cost.quantize(Decimal('0.01'))


def get_current_physical_valuation(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """SPEC §2.5 PHYSICAL_VALUATION Current Value = latest AssetValuation value (falling back to acquisition_cost)."""
    from .utils import get_exchange_rate

    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return ledger_balance if ledger_balance is not None else _get_account_ledger_balance(account)

    asset = account.linked_physical_asset
    latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
    if latest_val:
        val = latest_val.value
    else:
        val = asset.acquisition_cost or Decimal('0.00')

    if asset.currency and asset.currency != account.currency:
        rate = get_exchange_rate(asset.currency, account.currency)
        val = (val * rate).quantize(Decimal('0.01'))

    return val.quantize(Decimal('0.01'))


def get_baseline_insurance_surrender(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal | None:
    """SPEC §2.6 INSURANCE_SURRENDER Baseline = sum of linked premium Expense rows."""
    from .models import Expense
    from .utils import get_exchange_rate

    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return Decimal('0.00')

    asset = account.linked_physical_asset
    expenses = Expense.objects.filter(linked_physical_asset=asset)
    total_premiums = Decimal('0.00')
    for exp in expenses:
        amt = exp.amount
        if exp.currency and exp.currency != account.currency:
            rate = get_exchange_rate(exp.currency, account.currency)
            amt = (amt * rate).quantize(Decimal('0.01'))
        total_premiums += amt

    return total_premiums.quantize(Decimal('0.01'))


def get_current_insurance_surrender(account: Account, ledger_balance: Decimal | None = None, today: date | None = None) -> Decimal:
    """
    SPEC §2.6 INSURANCE_SURRENDER Current Value:
    - Latest AssetValuation value if any exists.
    - CRITICAL: Falls back to 0.00 when no valuation exists (NEVER baseline/acquisition cost).
    """
    from .utils import get_exchange_rate

    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return Decimal('0.00')

    asset = account.linked_physical_asset
    latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
    if not latest_val:
        return Decimal('0.00')

    val = latest_val.value
    if asset.currency and asset.currency != account.currency:
        rate = get_exchange_rate(asset.currency, account.currency)
        val = (val * rate).quantize(Decimal('0.01'))

    return val.quantize(Decimal('0.01'))


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
        return get_baseline_holdings(account, ledger_balance=ledger_balance, today=today)
    elif strategy == STRATEGY.REVOLVING_CREDIT:
        return get_baseline_revolving_credit(account, ledger_balance=ledger_balance, today=today)
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
        return get_current_holdings(account, ledger_balance=ledger_balance, today=today)
    elif strategy == STRATEGY.REVOLVING_CREDIT:
        return get_current_revolving_credit(account, ledger_balance=ledger_balance, today=today)
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


def process_matured_deposit_incomes(user=None):
    """
    Checks active deposit accounts with `record_maturity_income=True`
    whose maturity or closed date has arrived (<= date.today()), but no Income entry
    has been recorded under 'Investment Returns' yet.
    Automatically creates the Income record on the maturity/closed date.
    """
    from django.db.models import Q

    from .models import Account, Income

    today = date.today()
    qs = Account.objects.filter(record_maturity_income=True, is_active=True)
    if user:
        qs = qs.filter(user=user)

    qs = qs.filter(
        Q(deposit_closed_date__lte=today) | Q(deposit_maturity_date__lte=today)
    )

    created_incomes = []
    for account in qs:
        if not Income.objects.filter(account=account, source_type='Investment Returns').exists():
            maturity_or_closed_date = account.deposit_closed_date or account.deposit_maturity_date or today
            current_val = get_current(account, today=maturity_or_closed_date)
            baseline_val = get_baseline(account, today=maturity_or_closed_date) or Decimal('0.00')
            interest_earned = (current_val - baseline_val).quantize(Decimal('0.01'))

            if interest_earned > Decimal('0.00'):
                inc = Income.objects.create(
                    user=account.user,
                    date=maturity_or_closed_date,
                    amount=interest_earned,
                    currency=account.currency,
                    source_type='Investment Returns',
                    source=f"Interest from {account.name}",
                    account=account,
                    description=f"Accrued interest earned on deposit {account.name}",
                )
                created_incomes.append(inc)
    return created_incomes

