from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from django.db.models import Sum
from django.utils import timezone

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

def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 February in a year that has none
        return d.replace(year=d.year + years, day=28)


def _years_between(start: date, end: date) -> Decimal:
    """Time between two dates in years, the way a bank counts it for a deposit: whole years by calendar
    anniversary, then the odd days over 365. A one-year deposit is exactly 1.0 even across 29 February,
    so a deposit that reaches maturity shows the maturity amount the bank quotes."""
    if end <= start:
        return Decimal('0')
    years = end.year - start.year
    if _add_years(start, years) > end:
        years -= 1
    leftover_days = (end - _add_years(start, years)).days
    return Decimal(years) + Decimal(leftover_days) / Decimal('365')


def _calculate_fd_current(
    principal: Decimal,
    rate_pct: Decimal,
    start_date: date,
    compounding: str | None,
    maturity_date: date | None = None,
    closed_date: date | None = None,
    today: date | None = None,
) -> Decimal:
    today = today or timezone.localdate()
    if start_date > today:
        return principal

    end_date = today
    if maturity_date and end_date > maturity_date:
        end_date = maturity_date
    if closed_date and end_date > closed_date:
        end_date = closed_date

    if start_date > end_date:
        return principal

    years = _years_between(start_date, end_date)
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


def _in_account_currency(transfer, account: Account) -> Decimal:
    """A transfer's amount is in the sending account's currency; the deposit counts it in its own."""
    from .utils import get_exchange_rate
    sent_in = transfer.from_account.currency
    if not sent_in or sent_in == account.currency:
        return transfer.amount
    return (transfer.amount * get_exchange_rate(sent_in, account.currency)).quantize(Decimal('0.01'))


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
    - An installment never falls on the maturity date itself: a 12-month RD takes 12 installments, and
      the date it matures on is when the money comes back, not another payment.
    """
    return [(d, amount) for d, amount, _expected in _rd_installment_rows(account, end_date)]


def _rd_installment_rows(account: Account, end_date: date, today: date | None = None) -> list[tuple[date, Decimal, bool]]:
    """(date, amount, expected) for each installment of an RD up to end_date.

    With `today` given, a month after today with no transfer posted yet is an *expected* installment (the
    schedule says it is still to come), not a skipped one. A month that has already passed without a transfer
    stays skipped, as before. Without `today` nothing is expected: this is what has actually been paid.
    """
    import calendar

    from .models import Transfer

    start_date = account.deposit_start_date
    if not start_date:
        return []
    maturity = account.deposit_maturity_date

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
    ).select_related('from_account').order_by('date'))

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

    installments: list[tuple[date, Decimal, bool]] = []
    current_year = start_date.year
    current_month = start_date.month

    while True:
        if date(current_year, current_month, 1) > end_date:
            break

        key = (current_year, current_month)
        if key in transfers_by_month:
            for t in transfers_by_month[key]:
                installments.append((t.date, _in_account_currency(t, account), False))
        elif inst_amount > Decimal('0.00'):
            last_day = calendar.monthrange(current_year, current_month)[1]
            d_i = date(current_year, current_month, min(inst_day, last_day))
            in_range = start_date <= d_i <= end_date and (maturity is None or d_i < maturity)
            if in_range:
                is_before_tracking = (first_tx_month is None) or (key < first_tx_month)
                still_to_come = today is not None and d_i > today
                if is_before_tracking and not still_to_come:
                    installments.append((d_i, inst_amount, False))
                elif still_to_come:
                    installments.append((d_i, inst_amount, True))

        if current_month == 12:
            current_month = 1
            current_year += 1
        else:
            current_month += 1

    return installments


def _rd_accrued_value(account: Account, installments, end_date: date) -> Decimal:
    """What the installments are worth on `end_date`: each one grown from its own date at the deposit's rate."""
    rate = account.deposit_rate / Decimal('100')
    comp_freq = account.deposit_compounding or 'QUARTERLY'
    total_val = Decimal('0.00')
    for d_i, amt_i in installments:
        years_i = _years_between(d_i, end_date)

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


def get_rd_plan(account: Account, today: date | None = None) -> dict | None:
    """Where a running Recurring Deposit stands and where it is going, or None when there is nothing to project.

    Needs a start date and a maturity (or closed) date. `paid` is what has gone in so far, `upcoming` the
    installments still to come: ones already recorded with a later date, and ones the schedule expects but that
    have not been recorded yet. `maturity_value` is what the whole schedule is worth on the maturity date.
    """
    if account.account_type != 'RD' or not account.deposit_start_date:
        return None
    horizon = min((d for d in (account.deposit_maturity_date, account.deposit_closed_date) if d), default=None)
    if horizon is None:
        return None
    today = today or timezone.localdate()

    rows = _rd_installment_rows(account, horizon, today=today)
    if not rows:
        return None             # no installment amount and none recorded: there is no plan to show
    paid = [(d, amt) for d, amt, _expected in rows if d <= today]
    upcoming = [{'date': d, 'amount': amt, 'expected': expected} for d, amt, expected in rows if d > today]
    paid_total = sum((amt for _, amt in paid), Decimal('0.00')).quantize(Decimal('0.01'))
    upcoming_total = sum((u['amount'] for u in upcoming), Decimal('0.00')).quantize(Decimal('0.01'))
    planned_total = paid_total + upcoming_total

    maturity_value = None
    if account.deposit_rate is not None and rows:
        maturity_value = _rd_accrued_value(account, [(d, amt) for d, amt, _e in rows], horizon)
    return {
        'paid_count': len(paid), 'paid_total': paid_total,
        'upcoming': upcoming, 'upcoming_count': len(upcoming), 'upcoming_total': upcoming_total,
        'planned_count': len(rows), 'planned_total': planned_total,
        'maturity_value': maturity_value,
        'interest_at_maturity': (maturity_value - planned_total).quantize(Decimal('0.01')) if maturity_value is not None else None,
        'maturity_date': horizon,
        'matured': horizon <= today,             # the date has come: what follows is the value it reached, not a forecast
    }


_NO_PLAN = object()


def get_maturity_value(account: Account, ledger_balance: Decimal | None = None, today: date | None = None,
                       plan=_NO_PLAN) -> Decimal | None:
    """What a deposit is worth on its maturity date, from its terms alone. For a running RD that includes the
    installments still to come, so it matches what the bank will pay if the schedule is kept. Money already taken out
    does not change it (an FD redeemed on its maturity day still matured for what the terms say).

    A caller that has already worked out the RD's plan (get_rd_plan) passes it in rather than have it built again."""
    if not account.deposit_maturity_date or account.deposit_rate is None:
        return None
    if account.account_type == 'RD':
        if plan is _NO_PLAN:
            plan = get_rd_plan(account, today=today)
        if plan and plan['maturity_value'] is not None:
            return plan['maturity_value']
    return get_current_deposit(account, ledger_balance=ledger_balance, today=account.deposit_maturity_date, adjust=False)


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
        eval_today = today or timezone.localdate()
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


# Schemes people pay into year after year: a transfer in adds to the value, from its own date.
_TOP_UP_SCHEMES = frozenset({'PPF', 'EPF', 'VPF', 'SSY', 'POST_OFFICE', 'APY', 'NPS'})


class DepositMovements:
    """Money moved into and out of deposit-type accounts, loaded for many accounts in a few queries.

    A deposit's value depends on what was paid out of it (a redemption, a transfer to another account) and, for the
    schemes people keep paying into, on what was paid in after it started. Looking that up account by account costs
    two or three queries each time a deposit is valued, and net worth, the Accounts list and the account page value
    every deposit. Register them once with `DepositMovements.load(accounts)` and hand the result to
    `get_current_deposit`: nothing is queried until a deposit actually needs its movements (many never do, see
    get_current_deposit), and then all the registered accounts are loaded together. Without it, an account's
    movements are loaded the first time they are asked for.
    """

    def __init__(self):
        self._out: dict[int, list[tuple[date, Decimal]]] = {}
        self._into: dict[int, list[tuple[date, Decimal]]] = {}
        self._pending: list[Account] = []

    @classmethod
    def load(cls, accounts) -> 'DepositMovements':
        movements = cls()
        movements._pending = [a for a in accounts if a.pk]
        return movements

    def _ensure(self, account: Account) -> None:
        if account.pk in self._out:
            return
        self._fill([account] + self._pending)
        self._pending = []

    def _fill(self, accounts) -> None:
        from .models import Expense, Transfer

        seen = set()
        accounts = [a for a in accounts if a.pk not in self._out and not (a.pk in seen or seen.add(a.pk))]
        if not accounts:
            return
        ids = [a.pk for a in accounts]
        for account_id in ids:
            self._out[account_id] = []
            self._into[account_id] = []
        for account_id, paid_on, amount in Transfer.objects.filter(from_account_id__in=ids).values_list('from_account_id', 'date', 'amount'):
            self._out[account_id].append((paid_on, Decimal(amount)))
        currencies = {a.pk: a.currency for a in accounts}
        rates: dict[tuple[str, str], Decimal] = {}
        for account_id, paid_on, amount, spent_in in Expense.objects.filter(account_id__in=ids).values_list('account_id', 'date', 'amount', 'currency'):
            amount = Decimal(amount)
            own = currencies[account_id]
            if spent_in and own and spent_in != own:
                # An expense is in its own currency, which need not be the account's: count it in the account's
                if (spent_in, own) not in rates:
                    from .utils import get_exchange_rate
                    rates[(spent_in, own)] = Decimal(str(get_exchange_rate(spent_in, own)))
                amount = (amount * rates[(spent_in, own)]).quantize(Decimal('0.01'))
            self._out[account_id].append((paid_on, amount))
        top_up_accounts = {a.pk: a for a in accounts if a.account_type in _TOP_UP_SCHEMES}
        if top_up_accounts:
            # In the deposit's own currency: a transfer's amount is in the sending account's (see _in_account_currency)
            for transfer in Transfer.objects.filter(to_account_id__in=list(top_up_accounts)).select_related('from_account'):
                self._into[transfer.to_account_id].append((transfer.date, _in_account_currency(transfer, top_up_accounts[transfer.to_account_id])))

    def withdrawn(self, account: Account, as_of: date) -> Decimal:
        """Everything moved or spent out of the account up to `as_of`, in its own currency."""
        if not account.pk:
            return Decimal('0.00')
        self._ensure(account)
        return sum((amount for paid_on, amount in self._out[account.pk] if paid_on <= as_of), Decimal('0.00')).quantize(Decimal('0.01'))

    def top_ups(self, account: Account, start: date, as_of: date) -> list[tuple[date, Decimal]]:
        """What went into the account after `start`, up to `as_of`, as (date, amount) in its own currency."""
        if not account.pk:
            return []
        self._ensure(account)
        return [(paid_on, amount) for paid_on, amount in self._into[account.pk] if start < paid_on <= as_of]


def _top_ups_value(account: Account, movements: DepositMovements, start: date, today: date) -> Decimal:
    """What the money transferred in after the deposit started is worth now, each from the day it went in."""
    total = Decimal('0.00')
    for paid_on, amount in movements.top_ups(account, start, today):
        if account.deposit_payout:
            total += amount
            continue
        total += _calculate_fd_current(
            principal=amount, rate_pct=account.deposit_rate, start_date=paid_on, compounding=account.deposit_compounding,
            maturity_date=account.deposit_maturity_date, closed_date=account.deposit_closed_date, today=today)
    return total


def get_current_deposit(account: Account, ledger_balance: Decimal | None = None, today: date | None = None,
                        movements: DepositMovements | None = None, adjust: bool = True) -> Decimal:
    """
    Current accrued value for DEPOSIT strategy:
    For RD: sum of accrued future value for each installment (annuity formula).
    For FD/lump-sum: compound interest capped at maturity_date or closed_date.

    `adjust` (the default) then takes into account the money that has moved in and out of the deposit; a projection of
    what the terms alone will be worth (see get_maturity_value) passes False. `movements` carries those movements when
    the caller has loaded them for many accounts at once.
    """
    if ledger_balance is None:
        ledger_balance = _get_account_ledger_balance(account)

    if account.deposit_rate is None or account.deposit_start_date is None:
        return ledger_balance

    eval_today = today or timezone.localdate()
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

        return _rd_accrued_value(account, installments, end_date)

    # FD / lump-sum deposits
    principal = account.deposit_principal if account.deposit_principal is not None else ledger_balance
    if principal is None or principal == Decimal('0.00'):
        return ledger_balance

    if account.deposit_payout:
        # The interest is paid out to another account as it falls due, so the deposit itself stays at its principal.
        # Accruing it here as well would count the same interest twice in net worth.
        value = Decimal(principal).quantize(Decimal('0.01'))
    else:
        value = _calculate_fd_current(
            principal=principal,
            rate_pct=account.deposit_rate,
            start_date=start,
            compounding=account.deposit_compounding,
            maturity_date=account.deposit_maturity_date,
            closed_date=account.deposit_closed_date,
            today=eval_today,
        )
    if not adjust:
        return value
    # Money taken out of the deposit (redeemed, or moved to another account) is no longer in it. Without this a
    # deposit redeemed into the bank is counted twice: once in the bank and once from its terms.
    # (The ledger cannot say it: a balance equal to the principal can also be money in matching money out, such as
    # interest credited and a part withdrawn. The movements are loaded once for all deposits, so looking is cheap.)
    movements = movements or DepositMovements()
    withdrawn = movements.withdrawn(account, eval_today)
    if withdrawn > 0 and account.deposit_closed_date and account.deposit_closed_date <= eval_today:
        # A closed deposit holds nothing: what the bank paid out can be less than the accrued value (tax deducted at
        # source, rounding), and that difference is not still sitting in the deposit.
        return Decimal('0.00')
    if account.account_type in _TOP_UP_SCHEMES:
        value += _top_ups_value(account, movements, start, eval_today)
    return max(Decimal('0.00'), value - withdrawn)


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


def holdings_cash_summary(accounts) -> dict:
    """What an investment account holds besides its holdings, for several accounts with one ledger read and one
    holdings read: {account_id: {'ledger', 'cost', 'uninvested', 'shortfall'}}, in each account's currency.

    ``uninvested`` is the money in the account that no holding accounts for (ledger balance minus the cost of the
    holdings), the cash waiting to be invested or left over from a sale. ``shortfall`` is the opposite: the holdings
    cost more than the money recorded in the account, usually because a transfer into it was never recorded."""
    from .ledger_read_service import LedgerReadService
    from .models import Holding
    from .utils import get_exchange_rate

    accounts = list(accounts)
    if not accounts:
        return {}
    ledger = LedgerReadService.get_account_balances(accounts)
    currency = {a.id: a.currency for a in accounts}
    cost = {a.id: Decimal('0.00') for a in accounts}
    for h in (Holding.objects.filter(account_id__in=list(currency), is_active=True)
              .values('account_id', 'currency', 'units', 'avg_cost')):
        value = ((h['units'] or Decimal('0')) * (h['avg_cost'] or Decimal('0'))).quantize(Decimal('0.01'))
        if h['currency'] and h['currency'] != currency[h['account_id']]:
            value = (value * get_exchange_rate(h['currency'], currency[h['account_id']])).quantize(Decimal('0.01'))
        cost[h['account_id']] += value
    out = {}
    for a in accounts:
        bal = Decimal(str(ledger.get(a.id, a.balance)))
        gap = bal - cost[a.id]
        out[a.id] = {'ledger': bal, 'cost': cost[a.id], 'uninvested': max(Decimal('0.00'), gap),
                     'shortfall': max(Decimal('0.00'), -gap)}
    return out


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
            val = latest_val.value_for(holding.units)
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

    # Net uninvested cash: max(0, ledger_balance - cost_basis_total). A sale puts its proceeds in as cash and
    # records its profit or loss in the ledger (holdings_service), so what is left here is real cash.
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


def is_protection_policy(account: Account) -> bool:
    """Term / health cover: premiums are a cost, the policy itself is worth nothing."""
    asset = account.linked_physical_asset if account.linked_physical_asset_id else None
    return bool(asset and getattr(asset, 'policy_type', 'SAVINGS') == 'PROTECTION')


def premiums_paid_by_asset(asset_ids, as_of: date | None = None) -> dict:
    """Premium expenses linked to each policy, ONE query for any number of policies.
    Returns {asset_id: {currency: Decimal}}; ``as_of`` leaves out premiums dated after it."""
    from .models import Expense

    ids = [i for i in set(asset_ids) if i]
    if not ids:
        return {}
    rows = Expense.objects.filter(linked_physical_asset_id__in=ids)
    if as_of is not None:
        rows = rows.filter(date__lte=as_of)
    out: dict = {}
    for row in rows.values('linked_physical_asset_id', 'currency').annotate(total=Sum('amount')):
        out.setdefault(row['linked_physical_asset_id'], {})[row['currency']] = row['total'] or Decimal('0.00')
    return out


def get_premiums_paid(account: Account, ledger_balance: Decimal | None = None,
                      by_asset: dict | None = None, as_of: date | None = None) -> Decimal:
    """What has gone into the policy, in the account's currency: premium expenses linked to the
    policy plus money transferred into the account (its ledger balance, when positive).

    Pass ``by_asset`` (from premiums_paid_by_asset) when valuing many policies so no query runs per
    account. A premium paid from the policy account itself lowers its balance and is not added back."""
    from .utils import get_exchange_rate

    total = Decimal('0.00')
    asset_id = account.linked_physical_asset_id
    if asset_id:
        if by_asset is None:
            by_asset = premiums_paid_by_asset([asset_id], as_of=as_of)
        for currency, amount in by_asset.get(asset_id, {}).items():
            if currency and currency != account.currency:
                amount = (amount * get_exchange_rate(currency, account.currency)).quantize(Decimal('0.01'))
            total += amount
    if ledger_balance is not None and Decimal(str(ledger_balance)) > 0:
        total += Decimal(str(ledger_balance))
    return total.quantize(Decimal('0.01'))


def get_baseline_insurance_surrender(account: Account, ledger_balance: Decimal | None = None, today: date | None = None,
                                     by_asset: dict | None = None) -> Decimal | None:
    """SPEC §2.6 INSURANCE_SURRENDER Baseline = premiums paid (0 for pure protection cover)."""
    if is_protection_policy(account):
        return Decimal('0.00')
    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return Decimal('0.00')
    return get_premiums_paid(account, ledger_balance, by_asset=by_asset)


def get_current_insurance_surrender(account: Account, ledger_balance: Decimal | None = None, today: date | None = None,
                                    by_asset: dict | None = None) -> Decimal:
    """
    SPEC §2.6 INSURANCE_SURRENDER Current Value:
    - Pure protection (term / health): always 0.00.
    - Latest AssetValuation value when one above 0.00 is recorded.
    - Otherwise the premiums paid so far (users cannot enter a surrender value and the form seeds 0.00).
    """
    from .utils import get_exchange_rate

    if is_protection_policy(account):
        return Decimal('0.00')
    fallback = get_premiums_paid(account, ledger_balance, by_asset=by_asset)
    if not account.linked_physical_asset_id or not account.linked_physical_asset:
        return fallback

    asset = account.linked_physical_asset
    latest_val = asset.valuations.order_by('-as_of_date', '-created_at').first()
    if not latest_val or not latest_val.value:
        return fallback

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
        return get_baseline_insurance_surrender(account, ledger_balance=ledger_balance, today=today)
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
        return get_current_insurance_surrender(account, ledger_balance=ledger_balance, today=today)
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
    Results are cached for 5 minutes to avoid 2-4 repeated queries per account list load.
    """
    from django.core.cache import cache
    from .models import Expense, Income

    cache_key = f'interest_summary_{user.id}_{start_date}_{end_date}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

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

    result = {
        'interest_earned': income_interest,
        'interest_charged': expense_interest,
    }
    cache.set(cache_key, result, 300)  # 5-minute TTL
    return result


def process_matured_deposit_incomes(user=None):
    """
    Checks active deposit accounts with `record_maturity_income=True`
    whose maturity or closed date has arrived (<= timezone.localdate()), but no Income entry
    has been recorded under 'Investment Returns' yet.
    Automatically creates the Income record on the maturity/closed date.
    """
    from django.db.models import Q

    from .models import Account, Income

    today = timezone.localdate()
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

