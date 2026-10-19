"""The period an Analytics page covers, what it is compared with, and how it is cut into bars.

Pure date arithmetic (no queries), so every rule can be pinned down in a small test.

  * the period comes from the unified filter toolbar (``expenses.periods.resolve_period``), clipped to today;
  * "this year" is compared with the same dates last year, "this month" (or salary cycle) with the same number
    of days of the month before, every other period with the stretch of the same length just before it, and
    "all time" with nothing;
  * a short period gets a bar per day, a longer one per week, then per month, then per quarter.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

DAY_MAX_DAYS = 35
WEEK_MAX_DAYS = 200
MONTH_MAX_DAYS = 800

# what the comparison is called on the page, keyed by the rule that produced it
COMPARE_LAST_YEAR = 'last_year'
COMPARE_LAST_MONTH = 'last_month'
COMPARE_BEFORE = 'before'


@dataclass(frozen=True)
class Bucket:
    start: date
    end: date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class AnalyticsPeriod:
    key: str
    start: date
    end: date
    granularity: str                              # 'day' | 'week' | 'month' | 'quarter'
    compare: tuple[date, date] | None
    compare_kind: str | None
    full_end: date | None = None               # where the period really ends (a month or cycle still running)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def full_days(self) -> int:
        return ((self.full_end or self.end) - self.start).days + 1

    @property
    def range(self) -> tuple[date, date]:
        return (self.start, self.end)


def granularity_for(days: int) -> str:
    if days <= DAY_MAX_DAYS:
        return 'day'
    if days <= WEEK_MAX_DAYS:
        return 'week'
    if days <= MONTH_MAX_DAYS:
        return 'month'
    return 'quarter'


def _same_day_in_year(value: date, year: int) -> date:
    """The same calendar day in another year (29 Feb becomes 28 Feb)."""
    try:
        return value.replace(year=year)
    except ValueError:
        return value.replace(year=year, day=28)


def _comparison(key: str, start: date, end: date, previous_month: tuple[date, date] | None):
    if key == 'all':
        return None, None
    if key == 'this_year':
        return (_same_day_in_year(start, start.year - 1), _same_day_in_year(end, end.year - 1)), COMPARE_LAST_YEAR
    if key == 'this_month' and previous_month:
        prev_start, prev_end = previous_month
        elapsed = (end - start).days
        return (prev_start, min(prev_end, prev_start + timedelta(days=elapsed))), COMPARE_LAST_MONTH
    length = (end - start).days + 1
    before_end = start - timedelta(days=1)
    return (before_end - timedelta(days=length - 1), before_end), COMPARE_BEFORE


def build_period(user, state: dict, today: date, earliest: date | None = None) -> AnalyticsPeriod:
    """Resolve the toolbar state into the period to show.

    ``earliest`` is the user's first transaction date, used when the period has no start ("all time", or a
    custom range with only an end date).
    """
    from ..periods import resolve_period

    resolved = resolve_period(user=user, time_period=state.get('time_period'), start_date=state.get('start_date'),
                              end_date=state.get('end_date'), today=today)
    start = resolved.start or earliest or today.replace(month=1, day=1)
    end = min(resolved.end or today, today)
    if start > end:
        start, end = end, start

    previous_month = None
    if resolved.key == 'this_month':
        last = resolve_period(user=user, time_period='last_month', today=today)
        previous_month = (last.start, last.end)

    compare, kind = _comparison(resolved.key, start, end, previous_month)
    if compare and earliest and compare[1] < earliest:
        compare, kind = None, None                    # there is nothing before the first transaction to compare with
    return AnalyticsPeriod(key=resolved.key, start=start, end=end, granularity=granularity_for((end - start).days + 1),
                           compare=compare, compare_kind=kind, full_end=resolved.end)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _add_months(value: date, months: int) -> date:
    index = value.year * 12 + (value.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def buckets(start: date, end: date, granularity: str) -> list[Bucket]:
    """Cut [start, end] into consecutive bars; the first and last are clipped to the period."""
    out: list[Bucket] = []
    if granularity == 'day':
        day = start
        while day <= end:
            out.append(Bucket(day, day))
            day += timedelta(days=1)
    elif granularity == 'week':
        day = start
        while day <= end:
            out.append(Bucket(day, min(day + timedelta(days=6), end)))
            day += timedelta(days=7)
    elif granularity == 'month':
        month = _month_start(start)
        while month <= end:
            last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
            out.append(Bucket(max(month, start), min(last, end)))
            month = _add_months(month, 1)
    else:
        quarter = date(start.year, (start.month - 1) // 3 * 3 + 1, 1)
        while quarter <= end:
            last_month = _add_months(quarter, 2)
            last = last_month.replace(day=calendar.monthrange(last_month.year, last_month.month)[1])
            out.append(Bucket(max(quarter, start), min(last, end)))
            quarter = _add_months(quarter, 3)
    return out


def sample(items: list, limit: int) -> list:
    """At most ``limit`` evenly spaced items, always keeping the first and the last."""
    if len(items) <= limit:
        return list(items)
    step = (len(items) - 1) / (limit - 1)
    picked = sorted({round(i * step) for i in range(limit)})
    return [items[i] for i in picked]
