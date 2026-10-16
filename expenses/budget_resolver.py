"""The one place that answers "what is the budget limit for category X in month M?".

Resolution for (user, category, month):
  1. a BudgetMonthOverride for that month, else
  2. the BudgetLimitVersion with the greatest effective_from <= month, else
  3. no limit (None).
A stored NULL amount means "explicitly no limit". A stored 0 is treated as no limit by the
callers (budgets.has_limit), as it was for Category.limit.

Rows are keyed by normalised category name, so history survives deleting a category. Every public
function costs exactly one query (versions and overrides come back together as a UNION), done in
portable ORM so it behaves the same on SQLite (tests) and Postgres (prod). The resolver returns
raw limits and knows nothing about plan locks; use plan_allowed_category_ids() to restrict
which categories count.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from finance_tracker.plans import get_limit

from .budgets import has_limit, normalize_category

ZERO = Decimal('0')


def month_start(value) -> date:
    """The 1st of the month for a date (or (year, month) tuple)."""
    if isinstance(value, tuple):
        return date(value[0], value[1], 1)
    return value.replace(day=1)


def _next_month(d: date) -> date:
    return date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def _months(start: date, end: date) -> list[date]:
    months, cur = [], month_start(start)
    end = month_start(end)
    while cur <= end:
        months.append(cur)
        cur = _next_month(cur)
    return months


def _rows(user, upto: date):
    """(kind, name, date, amount) for versions up to `upto` and overrides up to `upto`, one query.
    kind 0 = version, 1 = override. `user=None` means every user and prepends the user id."""
    from django.db.models import IntegerField, Value

    from .models import BudgetLimitVersion, BudgetMonthOverride

    versions, overrides = BudgetLimitVersion.objects.all(), BudgetMonthOverride.objects.all()
    if upto is not None:
        versions, overrides = versions.filter(effective_from__lte=upto), overrides.filter(month__lte=upto)
    if user is not None:
        versions, overrides = versions.filter(user=user), overrides.filter(user=user)
    lead = ['user_id'] if user is None else []
    versions = (versions.annotate(kind=Value(0, output_field=IntegerField()))
                .values_list(*lead, 'kind', 'category_name', 'effective_from', 'amount'))
    overrides = (overrides.annotate(kind=Value(1, output_field=IntegerField()))
                 .values_list(*lead, 'kind', 'category_name', 'month', 'amount'))
    return list(versions.union(overrides, all=True))


def _resolve(rows, months: list[date]) -> dict[date, dict[str, Decimal | None]]:
    """{month: {normalised name: limit or None}} for the requested months from fetched rows."""
    versions: dict[str, list] = {}
    overrides: dict[tuple, Decimal | None] = {}
    for kind, name, when, amount in rows:
        if kind == 0:
            versions.setdefault(name, []).append((when, amount))
        else:
            overrides[(name, when)] = amount
    for history in versions.values():
        history.sort()
    names = set(versions) | {n for n, _ in overrides}

    out: dict[date, dict[str, Decimal | None]] = {}
    for m in months:
        limits = {}
        for name in names:
            if (name, m) in overrides:
                limits[name] = overrides[(name, m)]
                continue
            amount = None
            for when, amt in versions.get(name, ()):
                if when <= m:
                    amount = amt
                else:
                    break
            limits[name] = amount
        out[m] = limits
    return out


def bulk_effective_limits_by_name(month) -> dict[tuple[int, str], Decimal | None]:
    """{(user_id, normalised name): limit or None} for EVERY user in one query (for cron jobs)."""
    m = month_start(month)
    per_user: dict[int, list] = {}
    for user_id, *row in _rows(None, m):
        per_user.setdefault(user_id, []).append(tuple(row))
    return {(uid, name): limit
            for uid, rows in per_user.items()
            for name, limit in _resolve(rows, [m])[m].items()}


def effective_limit_details_by_name(user, month) -> dict[str, tuple[Decimal | None, str, bool]]:
    """{normalised name: (limit, source, has_future_changes)} for one month. One query.

    source is 'override' when a one-month override decides the limit, else 'default'.
    has_future_changes: a later month has its own version or override for the category."""
    m = month_start(month)
    rows = _rows(user, None)
    limits = _resolve([r for r in rows if r[2] <= m], [m])[m]
    overridden = {name for kind, name, when, _ in rows if kind == 1 and when == m}
    future = {name for kind, name, when, _ in rows if when > m}
    names = set(limits) | future
    return {name: (limits.get(name), 'override' if name in overridden else 'default', name in future)
            for name in names}


def effective_limits_by_name(user, month) -> dict[str, Decimal | None]:
    """{normalised category name: limit or None} for one month. One query."""
    m = month_start(month)
    return _resolve(_rows(user, m), [m])[m]


def effective_limits_range_by_name(user, start_month, end_month) -> dict[date, dict[str, Decimal | None]]:
    """{month: {normalised name: limit or None}} for every month in [start, end]. One query."""
    months = _months(start_month, end_month)
    if not months:
        return {}
    return _resolve(_rows(user, months[-1]), months)


def effective_limits(user, month, categories=None) -> dict[int, Decimal | None]:
    """{category_id: limit or None} for one month.

    Pass `categories` (an iterable of Category the caller already loaded) to keep this to one
    query; otherwise the user's categories are fetched (a second query).
    """
    by_name = effective_limits_by_name(user, month)
    return {c.id: by_name.get(normalize_category(c.name)) for c in _categories(user, categories)}


def effective_limits_range(user, start_month, end_month, categories=None) -> dict[date, dict[int, Decimal | None]]:
    """{month: {category_id: limit or None}} for trends. One query (plus categories if not given)."""
    cats = list(_categories(user, categories))
    return {
        m: {c.id: by_name.get(normalize_category(c.name)) for c in cats}
        for m, by_name in effective_limits_range_by_name(user, start_month, end_month).items()
    }


def effective_limit(user, category, month) -> Decimal | None:
    """The limit for one category in one month. One query."""
    return effective_limits_by_name(user, month).get(normalize_category(category.name))


def total_budget(user, month, categories=None) -> Decimal:
    """Sum of the effective limits of the given categories (default: all the user's categories).
    Derived, never stored. Pass the plan-allowed categories to respect the plan lock."""
    by_name = effective_limits_by_name(user, month)
    total, seen = ZERO, set()
    for c in _categories(user, categories):
        key = normalize_category(c.name)
        limit = by_name.get(key)
        if key not in seen and has_limit(limit):
            total += limit
        seen.add(key)
    return total


def plan_allowed_category_ids(user, categories=None) -> list[int]:
    """Ids of the categories the user's plan lets them budget: the first N by id
    (plan `budget_categories`, -1 = all). One source for the page, API, alerts and totals."""
    ids = sorted(c.id for c in _categories(user, categories))
    cap = get_limit(user.profile.active_tier, 'budget_categories')
    return ids if cap == -1 or cap is None else ids[:cap]


def _categories(user, categories):
    if categories is not None:
        return categories
    from .models import Category
    return Category.objects.filter(user=user).order_by('id')


def plan_allowed_categories(user, categories=None) -> list:
    """The Category objects the plan lets the user budget (first N by id, all on Pro)."""
    cats = sorted(_categories(user, categories), key=lambda c: c.id)
    cap = get_limit(user.profile.active_tier, 'budget_categories')
    return cats if cap == -1 or cap is None else cats[:cap]


def month_weights_for_range(start: date, end: date) -> list[tuple[date, float]]:
    """[(month, share of that month inside [start, end])]; a full month weighs 1.0. The weights
    add up to periods.calculate_budget_period_factor(start, end)."""
    import calendar

    out = []
    for m in _months(start, end):
        dim = calendar.monthrange(m.year, m.month)[1]
        first = max(start, m)
        last = min(end, date(m.year, m.month, dim))
        days = (last - first).days + 1
        out.append((m, 1.0 if days >= dim else days / dim))
    return out


def period_limits(user, month_weights, factor, categories=None):
    """Budget limits for a reporting period made of whole or partial months.

    Returns ({normalised name: period limit}, {normalised name: limit in the period's last month}).
    A period limit is each month's effective limit times the share of that month in the period;
    when the limit never changed over the period that is exactly monthly limit x factor, the rule
    the dashboard has always used. Names without any limit map to None. One query.
    """
    if not month_weights:
        return {}, {}
    months = sorted({m for m, _ in month_weights})
    by_month = effective_limits_range_by_name(user, months[0], months[-1])
    names = {normalize_category(c.name) for c in _categories(user, categories)}
    period, reference = {}, {}
    last = by_month[months[-1]]
    for name in names:
        values = [(w, by_month[month_start(m)].get(name)) for m, w in month_weights]
        reference[name] = last.get(name) if has_limit(last.get(name)) else None
        live = [(w, v) for w, v in values if has_limit(v)]
        if not live:
            period[name] = None
        elif len(live) == len(values) and len({v for _, v in live}) == 1:
            period[name] = float(live[0][1]) * factor
        else:
            period[name] = sum(float(v) * w for w, v in live)
    return period, reference


def annotate_current_limit(queryset, month=None):
    """Annotate a Category queryset with `cur_limit`: the effective limit for `month` (default:
    this month), in SQL, for list filters and sorts. Idempotent. Uses the same override-then-
    latest-version rule as the resolver."""
    if 'cur_limit' in queryset.query.annotations:
        return queryset
    from django.db.models import Case, DecimalField, Exists, OuterRef, Subquery, When
    from django.db.models.functions import Lower, Trim
    from django.utils import timezone

    from .models import BudgetLimitVersion, BudgetMonthOverride

    m = month_start(month or timezone.localdate())
    key = Lower(Trim(OuterRef('name')))
    override = BudgetMonthOverride.objects.filter(user=OuterRef('user'), category_name=key, month=m)
    version = (BudgetLimitVersion.objects.filter(user=OuterRef('user'), category_name=key, effective_from__lte=m)
               .order_by('-effective_from'))
    field = DecimalField(max_digits=15, decimal_places=2, null=True)
    return queryset.annotate(cur_limit=Case(
        When(Exists(override), then=Subquery(override.values('amount')[:1], output_field=field)),
        default=Subquery(version.values('amount')[:1], output_field=field),
        output_field=field,
    ))
