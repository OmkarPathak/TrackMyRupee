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


def monthly_spend_by_category(user, year: int, month: int) -> tuple[dict, Decimal]:
    """({normalised category name: total}, grand total) in base currency for one budget month (the calendar
    month, or the salary cycle for someone with a salary day: see budget_cycle). One query (expenses and
    budget-counted capital events come back together as a UNION)."""
    from django.db.models import Value

    from .budget_cycle import user_budget_range
    from .models import CapitalEvent, Expense

    first, last = user_budget_range(user, (year, month))   # a date range, not year/month lookups, so the date index applies
    expenses = (Expense.objects.filter(user=user, date__gte=first, date__lte=last).order_by()
                .values('category').annotate(subtype=Value(''), total=Sum('base_amount'))
                .values_list('category', 'subtype', 'total'))
    events = (CapitalEvent.objects.filter(user=user, date__gte=first, date__lte=last, exclude_from_budget=False)
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
    """{budget month key: {normalised category name: total}} for several months in two queries
    (same attribution rules as monthly_spend_by_category)."""
    from .budget_cycle import user_budget_range
    from .models import CapitalEvent, Expense

    months = sorted(months)
    if not months:
        return {}
    ranges = {m: user_budget_range(user, m) for m in months}
    first, last = min(r[0] for r in ranges.values()), max(r[1] for r in ranges.values())
    # Two plain queries, not a UNION: the halves would put the date column in different positions
    # (annotations follow model fields in the SELECT), which Postgres refuses to union.
    expenses = (Expense.objects.filter(user=user, date__gte=first, date__lte=last).order_by()
                .values('date', 'category').annotate(total=Sum('base_amount')))
    events = (CapitalEvent.objects.filter(user=user, date__gte=first, date__lte=last, exclude_from_budget=False)
              .order_by().values('date', 'category', 'subtype').annotate(total=Sum('base_amount')))
    names = None
    out = {m: {} for m in months}

    def month_of(day):
        for m, (start, end) in ranges.items():
            if start <= day <= end:
                return m
        return None

    def add(day, category, subtype, total):
        nonlocal names
        month = month_of(day)
        if month is None:
            return
        if subtype:
            names = names or _subtype_names()
            category = capital_report_name(category, subtype, names)
        key = normalize_category(category)
        out[month][key] = out[month].get(key, ZERO) + (total or ZERO)

    for row in expenses:
        add(row['date'], row['category'], '', row['total'])
    for row in events:
        add(row['date'], row['category'], row['subtype'], row['total'])
    return out


def _bulk_spend(start, end, user_ids=None, except_ids=None) -> dict:
    """{(user_id, normalised category name): total} between two dates for some users (None = all, minus
    `except_ids`). Two queries."""
    from .models import CapitalEvent, Expense

    def narrow(qs):
        if user_ids is not None:
            qs = qs.filter(user_id__in=user_ids)
        if except_ids:
            qs = qs.exclude(user_id__in=except_ids)
        return qs

    spend: dict = {}
    for row in (narrow(Expense.objects.filter(date__gte=start, date__lte=end))
                .values('user_id', 'category').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(row['category']))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)

    names = _subtype_names()
    for row in (narrow(CapitalEvent.objects.filter(date__gte=start, date__lte=end, exclude_from_budget=False))
                .values('user_id', 'category', 'subtype').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(capital_report_name(row['category'], row['subtype'], names)))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)
    return spend


def bulk_monthly_spend(year: int, month: int) -> dict:
    """{(user_id, normalised category name): total} for every user, for one budget month (each user's own
    calendar month or salary cycle). Two queries for the calendar users plus two per distinct salary day."""
    from .budget_cycle import budget_range, month_key
    from .models import UserProfile

    key = month_key((year, month))
    by_day: dict[int, list[int]] = {}
    for user_id, day in UserProfile.objects.filter(salary_date__gte=2, salary_date__lte=31).values_list('user_id', 'salary_date'):
        by_day.setdefault(day, []).append(user_id)
    start, end = budget_range(1, key)
    spend = _bulk_spend(start, end, except_ids=[i for ids in by_day.values() for i in ids])
    for day, ids in by_day.items():
        start, end = budget_range(day, key)
        spend.update(_bulk_spend(start, end, user_ids=ids))
    return spend


def bulk_current_spend(today) -> dict:
    """Like bulk_monthly_spend, but for the budget month each user is in today (their salary cycle can be
    in a different month's budget than someone else's)."""
    from .budget_cycle import current_groups

    spend: dict = {}
    for group in current_groups(today):
        spend.update(_bulk_spend(group['start'], group['end'], user_ids=group['user_ids'],
                                 except_ids=group.get('except_ids')))
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


def range_phase(start, end, today) -> str:
    """'past' | 'current' | 'future' for a budget month's days relative to today."""
    if end < today:
        return PAST
    return FUTURE if start > today else CURRENT


def range_days(start, end) -> int:
    return (end - start).days + 1


def range_days_left(start, end, today) -> int:
    """Days left in the budget month INCLUDING today; the whole month when it is ahead; 0 when it is over."""
    phase = range_phase(start, end, today)
    if phase == PAST:
        return 0
    return range_days(start, end) if phase == FUTURE else (end - today).days + 1


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
