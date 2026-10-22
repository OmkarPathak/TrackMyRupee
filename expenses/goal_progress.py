"""What the savings goals pages, the bell and the Insights engine all say about a goal's progress, in one place."""
from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

MILESTONES = (100, 90, 50)          # share of the target; the highest one reached is the one announced
PACE_DAYS = 90                      # the pace is read from the last three months of contributions
MIN_CONTRIBUTIONS = 2
MIN_SPAN_DAYS = 30                  # and from at least a month between the first and the last


def milestone_reached(percent) -> int | None:
    """The highest of 50, 90 and 100 that `percent` has reached, or None."""
    return next((m for m in MILESTONES if percent >= m), None)


def monthly_pace(contributions, today: date) -> Decimal | None:
    """What goes into the goal in a typical month, from `(date, amount)` pairs: the last 90 days, or the whole
    stretch when the goal is younger. None when there are too few contributions to say anything."""
    rows = sorted((d, Decimal(str(a))) for d, a in contributions)
    if len(rows) < MIN_CONTRIBUTIONS or (rows[-1][0] - rows[0][0]).days < MIN_SPAN_DAYS:
        return None
    since = max(rows[0][0], today - timedelta(days=PACE_DAYS))
    total = sum((a for d, a in rows if d >= since), Decimal(0))
    days = max((today - since).days, 30)
    pace = total / Decimal(days) * Decimal('30.4')
    return pace if pace > 0 else None


def months_to_finish(remaining, monthly) -> int | None:
    """Whole months until `remaining` is saved at `monthly`, rounded up; None when it would never finish."""
    remaining, monthly = Decimal(str(remaining)), Decimal(str(monthly))
    if remaining <= 0 or monthly <= 0:
        return None
    return max(1, math.ceil(remaining / monthly))


def finish_date(today: date, months: int) -> date:
    index = today.year * 12 + today.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)
