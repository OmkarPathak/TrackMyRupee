"""The Budget page / API payload for one month, built from the resolver and the shared rules in
budgets.py. Three queries: the user's categories, their limits (versions + overrides, one UNION)
and the month's spend (expenses + budget-counted capital events, one UNION).

Only categories the plan allows are listed; deleted categories simply are not there (their history
stays in the limit tables and still resolves for past months)."""

from __future__ import annotations

from decimal import Decimal

from . import budget_resolver as resolver
from .budget_cycle import user_budget_range
from .budgets import (
    AT_LIMIT,
    CURRENT,
    FUTURE,
    NO_LIMIT,
    ON_TRACK,
    OVER,
    PAST,
    budget_status,
    expected_pace,
    has_limit,
    monthly_spend_by_category,
    normalize_category,
    percent_used,
    range_days,
    range_days_left,
    range_phase,
    safe_to_spend_per_day,
)

ZERO = Decimal('0')
PACE_BAND = Decimal('0.05')        # within 5% of the total limit either way counts as "on pace"
STATUS_ORDER = {OVER: 0, AT_LIMIT: 1, ON_TRACK: 2, NO_LIMIT: 3}


def _money(value) -> float | None:
    return None if value is None else float(Decimal(str(value)).quantize(Decimal('0.01')))


def build_budget_page(user, month, today, sort='urgent') -> dict:
    """The full payload for `month` (a date in the month). `today` is the user's local date."""
    from .models import Category

    m = resolver.month_start(month)
    first_day, last_day = user_budget_range(user, m)      # the calendar month, or the salary cycle
    phase = range_phase(first_day, last_day, today)
    num_days = range_days(first_day, last_day)

    categories = list(Category.objects.filter(user=user).order_by('id'))
    allowed_ids = set(resolver.plan_allowed_category_ids(user, categories))
    allowed = [c for c in categories if c.id in allowed_ids]

    details = resolver.effective_limit_details_by_name(user, m)
    spend, grand_total = (monthly_spend_by_category(user, m.year, m.month) if phase != FUTURE else ({}, ZERO))

    rows, counts = [], {OVER: 0, AT_LIMIT: 0, ON_TRACK: 0, NO_LIMIT: 0}
    total_limit = budgeted_spent = ZERO
    seen = set()
    for c in allowed:
        key = normalize_category(c.name)
        limit, source, future_changes = details.get(key, (None, 'default', False))
        limit = limit if has_limit(limit) else None
        spent = Decimal(str(spend.get(key, 0)))
        status = budget_status(spent, limit)
        counts[status] += 1
        if limit is not None and key not in seen:
            total_limit += limit
            budgeted_spent += spent
        seen.add(key)
        pct = percent_used(spent, limit)
        rows.append({
            'id': c.id,
            'uuid': str(c.uuid),
            'name': c.name,
            'icon': c.icon,
            'limit': _money(limit),
            'spent': _money(spent),
            'remaining': _money(max(limit - spent, ZERO)) if limit is not None else None,
            'over_by': _money(spent - limit) if limit is not None and spent > limit else 0.0,
            'percent': round(pct, 1) if limit is not None else None,
            'status': status,
            'limit_source': source,
            'has_future_changes': future_changes,
        })

    if sort == 'name':
        rows.sort(key=lambda r: r['name'].lower())
    elif sort == 'limit':
        rows.sort(key=lambda r: -(r['limit'] or 0))
    elif sort == 'spent':
        rows.sort(key=lambda r: -(r['spent'] or 0))
    else:
        rows.sort(key=lambda r: (STATUS_ORDER[r['status']], -(r['percent'] or 0), r['name'].lower()))

    remaining = max(total_limit - budgeted_spent, ZERO)
    payload = {
        'month': m.strftime('%Y-%m'),
        'phase': phase,
        'total': _money(total_limit),                       # derived: sum of effective limits
        'spent': _money(budgeted_spent) if phase != FUTURE else None,
        'unbudgeted_spent': _money(max(grand_total - budgeted_spent, ZERO)) if phase != FUTURE else None,
        'remaining': _money(remaining) if phase != FUTURE else None,
        'over_by': _money(max(budgeted_spent - total_limit, ZERO)) if phase != FUTURE else None,
        'percent': round(float(budgeted_spent / total_limit * 100), 1) if has_limit(total_limit) and phase != FUTURE else None,
        'days_in_month': num_days,
        'start': first_day.isoformat(),
        'end': last_day.isoformat(),
        'is_cycle': first_day.day != 1,
        'days_left': None,
        'expected_pace': None,
        'pace_delta': None,          # expected pace - spent: positive = under pace, negative = over
        'pace_state': None,
        'safe_to_spend_per_day': None,
        'state': None,
        'counts': {'over': counts[OVER], 'at_limit': counts[AT_LIMIT],
                   'on_track': counts[ON_TRACK], 'no_limit': counts[NO_LIMIT]},
        'locked_count': len(categories) - len(allowed),
        'categories': rows,
    }

    if phase == CURRENT and has_limit(total_limit):
        left = range_days_left(first_day, last_day, today)
        expected = expected_pace(total_limit, (today - first_day).days + 1, num_days)
        delta = expected - budgeted_spent
        band = total_limit * PACE_BAND
        payload.update({
            'days_left': left,
            'expected_pace': _money(expected),
            'pace_delta': _money(delta),
            'pace_state': 'under' if delta > band else 'ahead' if delta < -band else 'on_track',
            'safe_to_spend_per_day': _money(safe_to_spend_per_day(remaining, left)),
            'state': 'over_budget' if budgeted_spent > total_limit or remaining <= 0 else 'ok',
        })
    elif phase == CURRENT:
        payload['days_left'] = range_days_left(first_day, last_day, today)
    elif phase == PAST and has_limit(total_limit):
        payload['state'] = 'over_budget' if budgeted_spent > total_limit else 'ok'    # the month's verdict
    return payload
