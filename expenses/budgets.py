"""Budget rules in one place: how spending is attributed to a category and when a category is
"at its limit" or "over".

Used by the Budget page and by the budget alerts (notifications), so they can never disagree.

  * Spending in a category for a month = expenses in that category + capital events that are NOT
    excluded from budgets (each counted under the category chosen on the event, else the name of its
    subtype, e.g. "Large Purchase").
  * Category names match case-insensitively ("food" counts towards "Food").
  * A category is  over  when spent > limit,  at its limit  from 85% of the limit (or exactly at
    it), otherwise on track. No limit (blank or 0) means no status.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

BUDGET_WARNING_PERCENT = 85

NO_LIMIT, ON_TRACK, AT_LIMIT, OVER = 'nolimit', 'ontrack', 'limit', 'over'

ZERO = Decimal('0')


def normalize_category(name) -> str:
    return (name or '').strip().lower()


def has_limit(limit) -> bool:
    return limit is not None and limit > 0


def percent_used(spent, limit) -> float:
    """Spent as a percentage of the limit (can exceed 100); 0 when there is no limit."""
    if not has_limit(limit):
        return 0.0
    return float(spent) / float(limit) * 100


def budget_status(spent, limit) -> str:
    """'nolimit' | 'ontrack' | 'limit' | 'over' for a category's spend against its limit."""
    if limit is None:
        return NO_LIMIT
    limit = Decimal(str(limit))
    if not has_limit(limit):
        return NO_LIMIT
    spent = Decimal(str(spent or 0))
    if spent > limit:
        return OVER
    if spent == limit or spent * 100 >= limit * BUDGET_WARNING_PERCENT:
        return AT_LIMIT
    return ON_TRACK


def _subtype_names() -> dict:
    """{subtype: the name a capital event without a category is reported under}.

    Always the English label, never the active language's: this is a name matched against the person's category
    names, so it must be the same on the Budget page for a Hindi or Marathi user, in the notification job (which
    runs with no language), and in a worker that an earlier request left in another language."""
    from django.utils import translation

    from .models import CapitalEvent
    with translation.override('en'):
        return {value: str(label) for value, label in CapitalEvent.SUBTYPE_CHOICES}


def capital_report_name(category, subtype, names: dict) -> str:
    """The category a capital event is reported under: the one the user chose, else its subtype's name."""
    return category or names.get(subtype, subtype)


def _month_bounds(year: int, month: int):
    """(first day of the month, first day of the next month)."""
    from datetime import date
    return date(year, month, 1), date(year + (month == 12), month % 12 + 1, 1)


def monthly_spend_by_category(user, year: int, month: int) -> tuple[dict, Decimal]:
    """({normalised category name: total}, grand total) in base currency for one month.
    One query (expenses and budget-counted capital events come back together as a UNION)."""
    from django.db.models import Value

    from .models import CapitalEvent, Expense

    first, after = _month_bounds(year, month)   # a date range, not year/month lookups, so the date index applies
    expenses = (Expense.objects.filter(user=user, date__gte=first, date__lt=after).order_by()
                .values('category').annotate(subtype=Value(''), total=Sum('base_amount'))
                .values_list('category', 'subtype', 'total'))
    events = (CapitalEvent.objects.filter(user=user, date__gte=first, date__lt=after, exclude_from_budget=False)
              .order_by().values('category', 'subtype').annotate(total=Sum('base_amount'))
              .values_list('category', 'subtype', 'total'))

    names = None
    spend: dict = {}
    for category, subtype, total in expenses.union(events, all=True):
        if subtype:   # a capital event: reported under its chosen category, else its subtype's name
            names = names or _subtype_names()
            category = capital_report_name(category, subtype, names)
        key = normalize_category(category)
        spend[key] = spend.get(key, ZERO) + (total or ZERO)

    return spend, sum(spend.values(), ZERO)


def monthly_spend_by_category_months(user, months) -> dict:
    """{month (1st): {normalised category name: total}} for several months in two queries
    (same attribution rules as monthly_spend_by_category)."""
    from django.db.models.functions import TruncMonth

    from .models import CapitalEvent, Expense

    months = sorted(months)
    if not months:
        return {}
    first, last = months[0], months[-1]
    end = last.replace(year=last.year + (last.month == 12), month=last.month % 12 + 1)
    # Two plain queries, not a UNION: the halves would put the month column in different positions
    # (annotations follow model fields in the SELECT), which Postgres refuses to union.
    expenses = (Expense.objects.filter(user=user, date__gte=first, date__lt=end).order_by()
                .annotate(m=TruncMonth('date')).values('m', 'category')
                .annotate(total=Sum('base_amount')))
    events = (CapitalEvent.objects.filter(user=user, date__gte=first, date__lt=end, exclude_from_budget=False)
              .order_by().annotate(m=TruncMonth('date')).values('m', 'category', 'subtype')
              .annotate(total=Sum('base_amount')))
    names = None
    out = {m: {} for m in months}

    def add(month, category, subtype, total):
        nonlocal names
        month = month.date() if hasattr(month, 'date') else month
        if month not in out:
            return
        if subtype:
            names = names or _subtype_names()
            category = capital_report_name(category, subtype, names)
        key = normalize_category(category)
        out[month][key] = out[month].get(key, ZERO) + (total or ZERO)

    for row in expenses:
        add(row['m'], row['category'], '', row['total'])
    for row in events:
        add(row['m'], row['category'], row['subtype'], row['total'])
    return out


def bulk_monthly_spend(year: int, month: int) -> dict:
    """{(user_id, normalised category name): total} for every user, for one month (2 queries)."""
    from .models import CapitalEvent, Expense

    spend: dict = {}
    first, after = _month_bounds(year, month)
    for row in (Expense.objects.filter(date__gte=first, date__lt=after)
                .values('user_id', 'category').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(row['category']))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)

    names = _subtype_names()
    for row in (CapitalEvent.objects.filter(date__gte=first, date__lt=after, exclude_from_budget=False)
                .values('user_id', 'category', 'subtype').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(capital_report_name(row['category'], row['subtype'], names)))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)
    return spend


# --- Month maths for the Budget page / API (pure functions, unit-tested) ---------------------

PAST, CURRENT, FUTURE = 'past', 'current', 'future'


def month_phase(month_start_date, today) -> str:
    """'past' | 'current' | 'future' for a month (its 1st) relative to today."""
    this_month = today.replace(day=1)
    if month_start_date < this_month:
        return PAST
    return CURRENT if month_start_date == this_month else FUTURE


def days_in_month(month_start_date) -> int:
    import calendar
    return calendar.monthrange(month_start_date.year, month_start_date.month)[1]


def days_left(month_start_date, today) -> int:
    """Days left in the month INCLUDING today (current month); whole month for a future month;
    0 for a past month."""
    phase = month_phase(month_start_date, today)
    if phase == PAST:
        return 0
    if phase == FUTURE:
        return days_in_month(month_start_date)
    return days_in_month(month_start_date) - today.day + 1


def expected_pace(total_limit, day: int, num_days: int) -> Decimal:
    """What should have been spent by day `day` of `num_days`: total limit x day / num_days."""
    if not has_limit(total_limit) or num_days <= 0:
        return ZERO
    return (Decimal(str(total_limit)) * day / num_days).quantize(Decimal('0.01'))


def safe_to_spend_per_day(remaining, left_days: int) -> Decimal:
    """Remaining budget / days left (incl. today). Never negative; 0 when nothing is left."""
    remaining = Decimal(str(remaining or 0))
    if remaining <= 0 or left_days <= 0:
        return ZERO
    return (remaining / left_days).quantize(Decimal('0.01'))
