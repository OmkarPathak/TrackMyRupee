"""The Budget page's "smart move" banner. Shown only when the data supports it:

  * a category is at or over its limit in the current month, and
  * another category (within the plan allowance) has had a limit and used under 10% of it in each
    of the last three completed months, and is still under 10% this month.

The suggestion moves part of the donor's unused limit to the category that needs it, for this
month. Computed from the resolver's per-month limits plus spend history, so it costs queries only
when a category needs attention. A dismissal is remembered per user for that pair and month.
"""

from __future__ import annotations

import math
from decimal import Decimal

from . import budget_resolver as resolver
from .budgets import has_limit, monthly_spend_by_category_months, normalize_category

LOOKBACK_MONTHS = 3
UNUSED_SHARE = Decimal('0.10')      # "barely used": under 10% of the limit
ROUND_TO = 100


def _add_months(d, n):
    idx = d.year * 12 + d.month - 1 + n
    return d.replace(year=idx // 12, month=idx % 12 + 1, day=1)


def suggestion_key(donor_id, needy_id, month) -> str:
    return f"move:{donor_id}:{needy_id}:{month:%Y-%m}"


def _needy_and_donors(rows):
    needy = [r for r in rows if r['status'] in ('over', 'limit') and r['limit']]
    donors = [r for r in rows if r['limit'] and r['status'] == 'ontrack' and (r['spent'] or 0) < r['limit'] * float(UNUSED_SHARE)]
    return needy, donors


def lookback_months(month) -> list:
    return [_add_months(month, -k) for k in range(LOOKBACK_MONTHS, 0, -1)]


def pick_suggestion(rows, month, limits, spend) -> dict | None:
    """The suggestion the data supports, from the page rows and the three earlier months' limits and spend
    (`limits` and `spend` are keyed by budget month, then normalised category name). No queries, and no
    dismissals: the caller decides what to do about those."""
    needy, donors = _needy_and_donors(rows)
    if not needy or not donors:
        return None
    months = lookback_months(month)

    def barely_used(name):
        key = normalize_category(name)
        for m in months:
            limit = limits[m].get(key)
            if not has_limit(limit) or spend[m].get(key, Decimal('0')) >= limit * UNUSED_SHARE:
                return False
        return True

    donors = [d for d in donors if barely_used(d['name'])]
    if not donors:
        return None
    donor = max(donors, key=lambda d: d['limit'] - (d['spent'] or 0))
    target = max(needy, key=lambda r: (r['status'] == 'over', r['percent'] or 0))

    room = donor['limit'] - (donor['spent'] or 0)
    want = max(target['over_by'] or 0, target['limit'] * 0.10)
    amount = min(math.floor(room), math.ceil(want / ROUND_TO) * ROUND_TO)
    if amount <= 0:
        return None
    return {
        'key': suggestion_key(donor['id'], target['id'], month),
        'donor_id': donor['id'], 'donor_name': donor['name'], 'donor_untouched': round(room, 2),
        'needy_id': target['id'], 'needy_name': target['name'], 'needy_limit': target['limit'],
        'amount': amount, 'months': LOOKBACK_MONTHS,
    }


def find_suggestion(user, month, rows) -> dict | None:
    """`rows`: the page payload's category rows for the current month. Returns None when nothing is
    supported by the data, or the suggestion was dismissed."""
    from .models import BudgetSuggestionDismissal

    needy, donors = _needy_and_donors(rows)
    if not needy or not donors:
        return None
    months = lookback_months(month)
    limits = resolver.effective_limits_range_by_name(user, months[0], months[-1])
    spend = monthly_spend_by_category_months(user, months)          # one query for all three months
    suggestion = pick_suggestion(rows, month, limits, spend)
    if suggestion is None or BudgetSuggestionDismissal.objects.filter(user=user, key=suggestion['key']).exists():
        return None
    return suggestion


def may_have_suggestion(rows) -> bool:
    """Cheap check on the page rows: is there a category at its limit AND one barely used this month?
    Only then is the (slower) history lookup worth doing."""
    needy = any(r['status'] in ('over', 'limit') and r['limit'] for r in rows)
    donor = any(r['limit'] and r['status'] == 'ontrack' and (r['spent'] or 0) < r['limit'] * float(UNUSED_SHARE) for r in rows)
    return needy and donor
