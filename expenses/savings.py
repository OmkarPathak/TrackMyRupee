"""The one place where "savings" and "savings rate" are defined.

    savings      = income - expenses - loan interest - capital events counted in averages
    denominator  = income - cashback/refund income
    savings rate = savings / denominator * 100          (0 when the denominator is <= 0)

Notes on the choices (they match what the dashboard has always displayed):
  * Loan principal is NOT spending: it is repaying borrowed money, so only the interest of a
    repayment reduces savings.
  * Capital events only count when they are not "excluded from averages" (the default is to
    exclude them, so a one-off purchase does not wreck the month). Callers that let the user
    opt in to see them (the Analytics toggle) pass the extra amount as ``extra_capital_events``.
  * Cashback & Rewards and Refund / Reimbursement still count as income (they are money in),
    but are left out of the denominator so one-off recoveries do not inflate the rate.

Every screen, email and service must call into this module instead of re-deriving the maths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import F, Q, Sum
from django.db.models.functions import TruncMonth

CASHBACK_REFUND_TYPES = ('Cashback & Rewards', 'Refund / Reimbursement')

ZERO = Decimal('0')


def _dec(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass(frozen=True)
class SavingsResult:
    income: Decimal = ZERO
    cashback_refund: Decimal = ZERO
    operating_expenses: Decimal = ZERO
    loan_interest: Decimal = ZERO
    capital_events: Decimal = ZERO

    @property
    def spending(self) -> Decimal:
        """Everything that counts as money spent (what the dashboard shows as "Spent")."""
        return self.operating_expenses + self.loan_interest + self.capital_events

    @property
    def savings(self) -> Decimal:
        return self.income - self.spending

    @property
    def denominator(self) -> Decimal:
        return self.income - self.cashback_refund

    @property
    def rate(self) -> Decimal:
        """Savings rate in percent, unrounded. 0 when there is no income to measure against."""
        denominator = self.denominator
        if denominator <= 0:
            return ZERO
        return self.savings / denominator * 100

    def rate_rounded(self, places: int = 1) -> Decimal:
        return self.rate.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def calculate_savings(income=0, cashback_refund=0, operating_expenses=0, loan_interest=0,
                      capital_events=0, extra_capital_events=0) -> SavingsResult:
    """Build a SavingsResult from already-aggregated base-currency amounts."""
    return SavingsResult(
        income=_dec(income),
        cashback_refund=_dec(cashback_refund),
        operating_expenses=_dec(operating_expenses),
        loan_interest=_dec(loan_interest),
        capital_events=_dec(capital_events) + _dec(extra_capital_events),
    )


# ── Aggregation helpers (querysets stay with the caller so each screen keeps its own filters) ──

def income_totals(income_qs) -> tuple[Decimal, Decimal]:
    """(total income, cashback+refund income) in base currency, in one query."""
    agg = income_qs.aggregate(
        total=Sum('base_amount'),
        cb_rf=Sum('base_amount', filter=Q(source_type__in=CASHBACK_REFUND_TYPES)),
    )
    return _dec(agg['total']), _dec(agg['cb_rf'])


def expense_total(expense_qs) -> Decimal:
    return _dec(expense_qs.aggregate(total=Sum('base_amount'))['total'])


def loan_interest_total(repayment_qs) -> Decimal:
    """Interest part of loan repayments, in base currency."""
    return _dec(repayment_qs.aggregate(total=Sum(F('interest_portion') * F('exchange_rate')))['total'])


def counted_capital_total(capital_qs) -> Decimal:
    """Capital events that count towards spending (those not excluded from averages)."""
    return _dec(capital_qs.filter(exclude_from_averages=False).aggregate(total=Sum('base_amount'))['total'])


def savings_from_querysets(income_qs, expense_qs, repayment_qs, capital_qs, extra_capital_events=0) -> SavingsResult:
    income, cashback_refund = income_totals(income_qs)
    return calculate_savings(
        income=income,
        cashback_refund=cashback_refund,
        operating_expenses=expense_total(expense_qs),
        loan_interest=loan_interest_total(repayment_qs),
        capital_events=counted_capital_total(capital_qs),
        extra_capital_events=extra_capital_events,
    )


def savings_for_period(user, start: date, end: date) -> SavingsResult:
    """Savings for ``user`` between two dates (inclusive)."""
    from .models import CapitalEvent, Expense, Income, LoanRepayment

    return savings_from_querysets(
        Income.objects.filter(user=user, date__range=(start, end)),
        Expense.objects.filter(user=user, date__range=(start, end)),
        LoanRepayment.objects.filter(loan__user=user, date__range=(start, end)),
        CapitalEvent.objects.filter(user=user, date__range=(start, end)),
    )


def _month_key(value):
    value = value.date() if hasattr(value, 'date') else value
    return (value.year, value.month)


def monthly_savings(user, start: date, end: date, extra_capital_by_month=None) -> dict:
    """{(year, month): SavingsResult} for every month that has any activity, in 5 queries.

    ``extra_capital_by_month`` maps (year, month) -> amount to count as spending on top
    (used by the Analytics "include capital events" toggle).
    """
    from .models import CapitalEvent, Expense, Income, LoanRepayment

    def by_month(qs, **aggregates):
        return qs.annotate(m=TruncMonth('date')).values('m').annotate(**aggregates)

    parts: dict = {}

    def slot(key):
        return parts.setdefault(key, {'income': ZERO, 'cb_rf': ZERO, 'expenses': ZERO, 'interest': ZERO, 'capital': ZERO})

    window = {'date__range': (start, end)}
    for row in by_month(Income.objects.filter(user=user, **window), total=Sum('base_amount'),
                        cb_rf=Sum('base_amount', filter=Q(source_type__in=CASHBACK_REFUND_TYPES))):
        slot(_month_key(row['m']))['income'] = _dec(row['total'])
        parts[_month_key(row['m'])]['cb_rf'] = _dec(row['cb_rf'])
    for row in by_month(Expense.objects.filter(user=user, **window), total=Sum('base_amount')):
        slot(_month_key(row['m']))['expenses'] = _dec(row['total'])
    for row in by_month(LoanRepayment.objects.filter(loan__user=user, **window),
                        total=Sum(F('interest_portion') * F('exchange_rate'))):
        slot(_month_key(row['m']))['interest'] = _dec(row['total'])
    for row in by_month(CapitalEvent.objects.filter(user=user, exclude_from_averages=False, **window),
                        total=Sum('base_amount')):
        slot(_month_key(row['m']))['capital'] = _dec(row['total'])

    extra = extra_capital_by_month or {}
    for key in extra:
        slot(key)

    return {
        key: calculate_savings(
            income=p['income'], cashback_refund=p['cb_rf'], operating_expenses=p['expenses'],
            loan_interest=p['interest'], capital_events=p['capital'], extra_capital_events=extra.get(key, 0),
        )
        for key, p in parts.items()
    }
