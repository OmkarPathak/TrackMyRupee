"""Budget rules in one place: how spending is attributed to a category and when a category is
"at its limit" or "over".

Used by the Budget page and by the budget alerts (notifications), so they can never disagree.

  * Spending in a category for a month = expenses in that category + capital events that are NOT
    excluded from budgets (each counted under the name of its subtype, e.g. "Large Purchase").
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
    from .models import CapitalEvent
    return {value: str(label) for value, label in CapitalEvent.SUBTYPE_CHOICES}


def monthly_spend_by_category(user, year: int, month: int) -> tuple[dict, Decimal]:
    """({normalised category name: total}, grand total) in base currency for one month."""
    from .models import CapitalEvent, Expense

    spend: dict = {}
    for row in (Expense.objects.filter(user=user, date__year=year, date__month=month)
                .values('category').annotate(total=Sum('base_amount'))):
        key = normalize_category(row['category'])
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)

    names = _subtype_names()
    for row in (CapitalEvent.objects.filter(user=user, date__year=year, date__month=month, exclude_from_budget=False)
                .values('subtype').annotate(total=Sum('base_amount'))):
        key = normalize_category(names.get(row['subtype'], row['subtype']))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)

    return spend, sum(spend.values(), ZERO)


def bulk_monthly_spend(year: int, month: int) -> dict:
    """{(user_id, normalised category name): total} for every user, for one month (2 queries)."""
    from .models import CapitalEvent, Expense

    spend: dict = {}
    for row in (Expense.objects.filter(date__year=year, date__month=month)
                .values('user_id', 'category').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(row['category']))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)

    names = _subtype_names()
    for row in (CapitalEvent.objects.filter(date__year=year, date__month=month, exclude_from_budget=False)
                .values('user_id', 'subtype').annotate(total=Sum('base_amount'))):
        key = (row['user_id'], normalize_category(names.get(row['subtype'], row['subtype'])))
        spend[key] = spend.get(key, ZERO) + (row['total'] or ZERO)
    return spend
