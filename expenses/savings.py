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

from django.db.models import Count, DecimalField, F, IntegerField, Q, Sum, Value
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


def _zero():
    return Value(ZERO, output_field=DecimalField())


def _fetch_together(parts) -> list:
    """Rows of several querysets that each return the same number of columns, in one UNION ALL query
    (one database round trip instead of one per queryset)."""
    parts = [p for p in parts if not p.query.is_empty()]
    if not parts:
        return []
    return list(parts[0].union(*parts[1:], all=True)) if len(parts) > 1 else list(parts[0])


def _one_row(qs, kind, a, b=None, c=None):
    """`qs` reduced to one row (kind, a, b, c): the building block of `period_totals`."""
    return (qs.order_by().annotate(_kind=Value(kind, output_field=IntegerField())).values('_kind')
            .annotate(_a=a, _b=b if b is not None else _zero(),
                      _c=c if c is not None else Value(0, output_field=IntegerField()))
            .values_list('_kind', '_a', '_b', '_c'))


def period_totals(income_qs, expense_qs, repayment_qs, capital_qs) -> dict:
    """Everything the savings maths needs from four querysets, in one query:
    income, cashback_refund, expenses, interest (loan), emi (loan, whole repayment), repayments (count),
    capital (counted in averages). Missing rows are zero."""
    rows = _fetch_together([
        _one_row(income_qs, 0, Sum('base_amount'), Sum('base_amount', filter=Q(source_type__in=CASHBACK_REFUND_TYPES))),
        _one_row(expense_qs, 1, Sum('base_amount')),
        _one_row(repayment_qs, 2, Sum(F('interest_portion') * F('exchange_rate')), Sum('base_amount'), Count('id')),
        _one_row(capital_qs.filter(exclude_from_averages=False), 3, Sum('base_amount')),
    ])
    out = {'income': ZERO, 'cashback_refund': ZERO, 'expenses': ZERO, 'interest': ZERO, 'emi': ZERO,
           'repayments': 0, 'capital': ZERO}
    for kind, a, b, c in rows:
        if kind == 0:
            out['income'], out['cashback_refund'] = _dec(a), _dec(b)
        elif kind == 1:
            out['expenses'] = _dec(a)
        elif kind == 2:
            out['interest'], out['emi'], out['repayments'] = _dec(a), _dec(b), int(c or 0)
        else:
            out['capital'] = _dec(a)
    return out


def savings_from_querysets(income_qs, expense_qs, repayment_qs, capital_qs, extra_capital_events=0) -> SavingsResult:
    t = period_totals(income_qs, expense_qs, repayment_qs, capital_qs)
    return calculate_savings(
        income=t['income'],
        cashback_refund=t['cashback_refund'],
        operating_expenses=t['expenses'],
        loan_interest=t['interest'],
        capital_events=t['capital'],
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


def totals_by_period(trunc, **sources) -> dict:
    """{name: {period: Decimal}} for several querysets, grouped by `trunc('date')` (e.g. TruncMonth), in one query.
    `sources` maps a name to (queryset, aggregate expression)."""
    names = list(sources)
    parts = [(qs.order_by().annotate(_kind=Value(i, output_field=IntegerField()), _p=trunc('date'))
              .values('_kind', '_p').annotate(_a=agg).values_list('_kind', '_p', '_a'))
             for i, (qs, agg) in enumerate(sources.values())]
    out = {name: {} for name in names}
    for kind, period, total in _fetch_together(parts):
        out[names[kind]][period] = _dec(total)
    return out


def monthly_parts(user, start: date, end: date | None = None) -> dict:
    """{(year, month): {'income', 'cb_rf', 'expenses', 'interest', 'capital'}} for every month with activity,
    in one query. `end` None means no upper bound. Capital events count only when not excluded from averages."""
    from .models import CapitalEvent, Expense, Income, LoanRepayment

    window = {'date__gte': start} if end is None else {'date__range': (start, end)}

    def by_month(qs, kind, total, extra=None):
        return (qs.order_by().annotate(_kind=Value(kind, output_field=IntegerField()), _m=TruncMonth('date'))
                .values('_kind', '_m').annotate(_a=total, _b=extra if extra is not None else _zero())
                .values_list('_kind', '_m', '_a', '_b'))

    names = ('income', 'expenses', 'interest', 'capital')
    parts: dict = {}
    for kind, month, a, b in _fetch_together([
        by_month(Income.objects.filter(user=user, **window), 0, Sum('base_amount'),
                 Sum('base_amount', filter=Q(source_type__in=CASHBACK_REFUND_TYPES))),
        by_month(Expense.objects.filter(user=user, **window), 1, Sum('base_amount')),
        by_month(LoanRepayment.objects.filter(loan__user=user, **window), 2, Sum(F('interest_portion') * F('exchange_rate'))),
        by_month(CapitalEvent.objects.filter(user=user, exclude_from_averages=False, **window), 3, Sum('base_amount')),
    ]):
        slot = parts.setdefault(_month_key(month), {'income': ZERO, 'cb_rf': ZERO, 'expenses': ZERO, 'interest': ZERO, 'capital': ZERO})
        slot[names[kind]] = _dec(a)
        if kind == 0:
            slot['cb_rf'] = _dec(b)
    return parts


def monthly_savings(user, start: date, end: date, extra_capital_by_month=None) -> dict:
    """{(year, month): SavingsResult} for every month that has any activity, in one query.

    ``extra_capital_by_month`` maps (year, month) -> amount to count as spending on top
    (used by the Analytics "include capital events" toggle).
    """
    parts = monthly_parts(user, start, end)
    extra = extra_capital_by_month or {}
    for key in extra:
        parts.setdefault(key, {'income': ZERO, 'cb_rf': ZERO, 'expenses': ZERO, 'interest': ZERO, 'capital': ZERO})

    return {
        key: calculate_savings(
            income=p['income'], cashback_refund=p['cb_rf'], operating_expenses=p['expenses'],
            loan_interest=p['interest'], capital_events=p['capital'], extra_capital_events=extra.get(key, 0),
        )
        for key, p in parts.items()
    }
