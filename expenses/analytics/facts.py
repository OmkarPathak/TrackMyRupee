"""Daily money facts for one user, loaded once and summed any way the page needs.

Five queries (income, expenses, loan interest, capital events, investment transfers) cover the whole span: the
selected period and the one it is compared with. Every total, bar, category ranking and flow on the Analytics
page is then a sum over days, so they always agree with each other. The money definitions are the app's single
ones in ``expenses.savings`` (savings, savings rate, what counts as spending).
"""
from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import NamedTuple

from django.db.models import Count, F, Q, Sum

from ..account_types import investment_codes
from ..budgets import capital_report_name, normalize_category
from ..savings import CASHBACK_REFUND_TYPES, SavingsResult, calculate_savings

ZERO = Decimal('0')


class ExpenseRow(NamedTuple):
    """One expense, for the rules that look at single entries (a larger-than-usual one, a possible duplicate)."""
    id: int
    date: date
    category: str
    amount: Decimal                  # base currency
    account_id: int | None
    created_at: datetime
    description: str
    from_import: bool


def load_expense_rows(user, first: date, last: date) -> list[ExpenseRow]:
    """The user's expenses between two dates, oldest first, in one query and without building model objects."""
    from ..models import Expense

    rows = Expense.objects.filter(user=user, date__range=(first, last)).order_by('date', 'id').values_list(
        'id', 'date', 'category', 'base_amount', 'account_id', 'created_at', 'description', 'import_batch_id')
    return [ExpenseRow(i, d, c or '', a or ZERO, acc, made, x.decode('utf-8', 'replace') if isinstance(x, bytes) else (x or ''),
                       batch is not None)
            for i, d, c, a, acc, made, x, batch in rows]


class IncomeRow(NamedTuple):
    """One income entry, for the rules that look at where money came from and when it arrived."""
    date: date
    source_type: str
    amount: Decimal                  # base currency


def load_income_rows(user, first: date, last: date) -> list[IncomeRow]:
    """The user's income entries between two dates, oldest first, in one query and without building model objects."""
    from ..models import Income

    # (cashback and refunds are not income anywhere else in the app, so they do not count as pay arriving)
    rows = Income.objects.filter(user=user, date__range=(first, last)).exclude(
        source_type__in=CASHBACK_REFUND_TYPES).order_by('date', 'id').values_list(
        'date', 'source_type', 'base_amount')
    return [IncomeRow(d, t or '', a or ZERO) for d, t, a in rows]


def load_category_cycles(user, ranges, *, categories: Iterable[str] = (), account_ids: Iterable[int] = ()
                         ) -> dict[str, list[Decimal]]:
    """What the user spent in each category over each of some consecutive (first, last) date ranges, oldest first, in
    one query. Expenses only, narrowed by the toolbar's category and account filters."""
    from ..models import Expense

    ranges, categories, account_ids = list(ranges), list(categories), list(account_ids)
    rows = Expense.objects.filter(user=user, date__gte=ranges[0][0], date__lte=ranges[-1][1])
    if categories:
        rows = rows.filter(category__in=categories)
    if account_ids:
        rows = rows.filter(account_id__in=account_ids)
    sums = {f'c{i}': Sum('base_amount', filter=Q(date__range=span)) for i, span in enumerate(ranges)}
    return {row['category']: [row[f'c{i}'] or ZERO for i in range(len(ranges))]
            for row in rows.values('category').annotate(**sums)}


class LoanPaid(NamedTuple):
    """What repayments between two dates were made of (base currency)."""
    interest: Decimal
    principal: Decimal
    repayments: int


def load_loan_paid(user, first: date, last: date) -> LoanPaid:
    """Interest and principal paid on all the user's loans between two dates, in one query. Uses the same
    interest * exchange rate the savings definition does, so the two always agree."""
    from ..models import LoanRepayment

    row = LoanRepayment.objects.filter(loan__user=user, date__range=(first, last)).aggregate(
        interest=Sum(F('interest_portion') * F('exchange_rate')),
        principal=Sum(F('principal_portion') * F('exchange_rate')),
        n=Count('id'))
    return LoanPaid(row['interest'] or ZERO, row['principal'] or ZERO, row['n'] or 0)


@dataclass
class Summary:
    """Everything that happened between two dates, already through the savings definition."""
    savings: SavingsResult
    invested: Decimal
    categories: dict[str, Decimal]                       # normalised category -> spending (expenses + capital events)
    invested_to: dict[tuple[int, str], Decimal]          # (account id, name) -> money moved into it
    capital_extra: Decimal = ZERO                        # capital events counted only because the switch is on

    @property
    def income(self) -> Decimal:
        return self.savings.income

    @property
    def spending(self) -> Decimal:
        return self.savings.spending

    @property
    def saved(self) -> Decimal:
        return self.savings.savings

    @property
    def rate(self) -> Decimal:
        return self.savings.rate


@dataclass
class Facts:
    first: date
    last: date
    include_capital_events: bool = False
    income: list[Decimal] = field(default_factory=list)
    cashback_refund: list[Decimal] = field(default_factory=list)
    interest: list[Decimal] = field(default_factory=list)
    invested: list[Decimal] = field(default_factory=list)
    capital_counted: list[Decimal] = field(default_factory=list)
    capital_extra: list[Decimal] = field(default_factory=list)
    expense_cats: list[dict[str, Decimal] | None] = field(default_factory=list)
    capital_cats_counted: list[dict[str, Decimal] | None] = field(default_factory=list)
    capital_cats_extra: list[dict[str, Decimal] | None] = field(default_factory=list)
    invested_dest: list[dict[tuple[int, str], Decimal] | None] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)  # normalised category -> the name to show

    def __post_init__(self):
        n = (self.last - self.first).days + 1
        for attr in ('income', 'cashback_refund', 'interest', 'invested', 'capital_counted', 'capital_extra'):
            setattr(self, attr, [ZERO] * n)
        for attr in ('expense_cats', 'capital_cats_counted', 'capital_cats_extra', 'invested_dest'):
            setattr(self, attr, [None] * n)

    @property
    def size(self) -> int:
        return len(self.income)

    def index(self, day: date) -> int:
        return (day - self.first).days

    def _span(self, start: date, end: date) -> range:
        return range(max(0, self.index(start)), min(self.size - 1, self.index(end)) + 1)

    @staticmethod
    def _add(slot: list[dict | None], i: int, key, amount: Decimal):
        if slot[i] is None:
            slot[i] = {}
        slot[i][key] = slot[i].get(key, ZERO) + amount

    def summarize(self, start: date, end: date) -> Summary:
        span = self._span(start, end)
        categories: dict[str, Decimal] = {}
        invested_to: dict[tuple[int, str], Decimal] = {}
        for i in span:
            for slot in (self.expense_cats[i], self.capital_cats_counted[i],
                         self.capital_cats_extra[i] if self.include_capital_events else None):
                if slot:
                    for key, amount in slot.items():
                        categories[key] = categories.get(key, ZERO) + amount
            if self.invested_dest[i]:
                for key, amount in self.invested_dest[i].items():
                    invested_to[key] = invested_to.get(key, ZERO) + amount
        extra = sum((self.capital_extra[i] for i in span), ZERO) if self.include_capital_events else ZERO
        expenses = sum(categories.values(), ZERO) - sum((self.capital_counted[i] for i in span), ZERO) - extra
        result = calculate_savings(
            income=sum((self.income[i] for i in span), ZERO),
            cashback_refund=sum((self.cashback_refund[i] for i in span), ZERO),
            operating_expenses=expenses,
            loan_interest=sum((self.interest[i] for i in span), ZERO),
            capital_events=sum((self.capital_counted[i] for i in span), ZERO),
            extra_capital_events=extra,
        )
        return Summary(savings=result, invested=sum((self.invested[i] for i in span), ZERO),
                       categories=categories, invested_to=invested_to, capital_extra=extra)

    def with_capital_events(self, counted: bool) -> Facts:
        """The same facts with the "count one-off capital events" switch set the other way (nothing is reloaded:
        the days are shared, only what `summarize` counts changes)."""
        other = copy.copy(self)
        other.include_capital_events = counted
        return other

    def daily_spending(self, start: date, end: date) -> list[Decimal]:
        """Spending on each day from start to end, as the savings definition counts it."""
        out = []
        for day_index in range(self.index(start), self.index(end) + 1):
            if not 0 <= day_index < self.size:
                out.append(ZERO)
                continue
            total = self.interest[day_index] + self.capital_counted[day_index]
            if self.include_capital_events:
                total += self.capital_extra[day_index]
            if self.expense_cats[day_index]:
                total += sum(self.expense_cats[day_index].values(), ZERO)
            out.append(total)
        return out

    def daily_expenses(self, start: date, end: date) -> list[Decimal]:
        """Expenses only (no loan interest, no capital events) on each day from start to end: the shape of everyday
        spending, without the lumps."""
        out = []
        for day_index in range(self.index(start), self.index(end) + 1):
            slot = self.expense_cats[day_index] if 0 <= day_index < self.size else None
            out.append(sum(slot.values(), ZERO) if slot else ZERO)
        return out

    def category_series(self, start: date, end: date, key: str) -> Decimal:
        """One category's spending between two dates."""
        total = ZERO
        for i in self._span(start, end):
            for slot in (self.expense_cats[i], self.capital_cats_counted[i],
                         self.capital_cats_extra[i] if self.include_capital_events else None):
                if slot and key in slot:
                    total += slot[key]
        return total


def earliest_date(user) -> date | None:
    """The user's first dated transaction (income or expense), for "all time" and open-ended ranges."""
    from django.db.models import Min

    from ..models import Expense, Income

    dates = [Expense.objects.filter(user=user).aggregate(d=Min('date'))['d'],
             Income.objects.filter(user=user).aggregate(d=Min('date'))['d']]
    dates = [d for d in dates if d]
    return min(dates) if dates else None


def load_facts(user, first: date, last: date, *, categories: Iterable[str] = (), account_ids: Iterable[int] = (),
               include_capital_events: bool = False) -> Facts:
    """Load the daily facts between two dates (both inclusive), narrowed by the toolbar's filters.

    A category filter narrows spending (expenses and capital events) only: income, savings and investing
    have no category, and loan interest is left out because it has none either.
    """
    from ..models import CapitalEvent, Expense, Income, LoanRepayment, Transfer

    categories, account_ids = list(categories), list(account_ids)
    facts = Facts(first=first, last=last, include_capital_events=include_capital_events)
    window = {'date__range': (first, last)}

    incomes = Income.objects.filter(user=user, **window)
    if account_ids:
        incomes = incomes.filter(account_id__in=account_ids)
    for row in incomes.values('date').annotate(
            total=Sum('base_amount'), cb_rf=Sum('base_amount', filter=Q(source_type__in=CASHBACK_REFUND_TYPES))):
        i = facts.index(row['date'])
        facts.income[i] += row['total'] or ZERO
        facts.cashback_refund[i] += row['cb_rf'] or ZERO

    expenses = Expense.objects.filter(user=user, **window)
    if categories:
        expenses = expenses.filter(category__in=categories)
    if account_ids:
        expenses = expenses.filter(account_id__in=account_ids)
    for row in expenses.values('date', 'category').annotate(total=Sum('base_amount')):
        key = normalize_category(row['category'])
        facts.names.setdefault(key, row['category'])
        facts._add(facts.expense_cats, facts.index(row['date']), key, row['total'] or ZERO)

    if not categories:
        repayments = LoanRepayment.objects.filter(loan__user=user, **window)
        if account_ids:
            repayments = repayments.filter(from_account_id__in=account_ids)
        for row in repayments.values('date').annotate(total=Sum(F('interest_portion') * F('exchange_rate'))):
            facts.interest[facts.index(row['date'])] += row['total'] or ZERO

    events = CapitalEvent.objects.filter(user=user, is_deleted=False, **window)
    if categories:
        events = events.filter(category__in=categories)
    if account_ids:
        events = events.filter(account_id__in=account_ids)
    subtype_names = {value: str(label) for value, label in CapitalEvent.SUBTYPE_CHOICES}
    for row in events.values('date', 'category', 'subtype', 'exclude_from_averages').annotate(total=Sum('base_amount')):
        name = capital_report_name(row['category'], row['subtype'], subtype_names)
        key = normalize_category(name)
        facts.names.setdefault(key, name)
        i, amount = facts.index(row['date']), row['total'] or ZERO
        if row['exclude_from_averages']:
            facts.capital_extra[i] += amount
            facts._add(facts.capital_cats_extra, i, key, amount)
        else:
            facts.capital_counted[i] += amount
            facts._add(facts.capital_cats_counted, i, key, amount)

    transfers = Transfer.objects.filter(user=user, to_account__account_type__in=list(investment_codes()), **window)
    if account_ids:
        transfers = transfers.filter(Q(from_account_id__in=account_ids) | Q(to_account_id__in=account_ids))
    for row in transfers.values('date', 'to_account_id', 'to_account__name').annotate(total=Sum('converted_amount')):
        i, amount = facts.index(row['date']), row['total'] or ZERO
        facts.invested[i] += amount
        facts._add(facts.invested_dest, i, (row['to_account_id'], row['to_account__name']), amount)
    return facts
