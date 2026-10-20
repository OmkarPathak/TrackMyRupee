"""Which days a budget month covers.

A budget is kept per "budget month" (the 1st of a month is its key). For someone without a salary day, or
with day 1, it is the calendar month. With a salary day (2 to 31) the month is the salary cycle, so the Budget
page, the alerts and the Dashboard all count the same days:

  * salary day 15 or earlier: the cycle that STARTS in the month is its budget month
    (day 5: October is 5 Oct to 4 Nov);
  * salary day after the 15th: the cycle that ENDS in the month is its budget month
    (day 25: October is 25 Sep to 24 Oct).

Either way most of the cycle's days fall in the month that names it, exactly one cycle belongs to each month,
and "this month" is the one a person would call the current month. Months with fewer days than the salary
day use their last day (salary day 31 in February is 28 Feb), as the Dashboard's salary cycle does.

Everything here is pure date arithmetic on a salary day number, so bulk jobs can use it without loading users.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

LATE_SALARY_DAY = 15      # a salary day after this names its cycle by the month the cycle ends in


def salary_day_of(user) -> int:
    """The user's salary day, 1 when they have none (calendar months)."""
    profile = getattr(user, 'profile', None) if user is not None else None
    value = getattr(profile, 'salary_date', None)
    return value if isinstance(value, int) and 1 <= value <= 31 else 1


def _effective(year: int, month: int, salary_day: int) -> int:
    return min(salary_day, calendar.monthrange(year, month)[1])


def _shift(first: date, months: int) -> date:
    index = first.year * 12 + first.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def month_key(value) -> date:
    """The 1st of the month for a date (or a (year, month) pair)."""
    if isinstance(value, tuple):
        return date(value[0], value[1], 1)
    return value.replace(day=1)


def budget_range(salary_day: int, month) -> tuple[date, date]:
    """(first day, last day) covered by the budget month `month` (any date in it, or its key)."""
    m = month_key(month)
    if salary_day <= 1:
        return m, date(m.year, m.month, calendar.monthrange(m.year, m.month)[1])
    if salary_day <= LATE_SALARY_DAY:
        start = date(m.year, m.month, _effective(m.year, m.month, salary_day))
        nxt = _shift(m, 1)
        end = date(nxt.year, nxt.month, _effective(nxt.year, nxt.month, salary_day)) - timedelta(days=1)
    else:
        prev = _shift(m, -1)
        start = date(prev.year, prev.month, _effective(prev.year, prev.month, salary_day))
        end = date(m.year, m.month, _effective(m.year, m.month, salary_day)) - timedelta(days=1)
    return start, end


def budget_month_of(salary_day: int, day: date) -> date:
    """The key (1st of the month) of the budget month that contains `day`."""
    m = month_key(day)
    if salary_day <= 1:
        return m
    started_this_month = day.day >= _effective(day.year, day.month, salary_day)
    if salary_day <= LATE_SALARY_DAY:
        return m if started_this_month else _shift(m, -1)
    return _shift(m, 1) if started_this_month else m


def previous_month(key: date) -> date:
    return _shift(month_key(key), -1)


def next_month(key: date) -> date:
    return _shift(month_key(key), 1)


# ---- with a user ----

def current_budget_month(user, today: date) -> date:
    return budget_month_of(salary_day_of(user), today)


def user_budget_range(user, month) -> tuple[date, date]:
    return budget_range(salary_day_of(user), month)


def key_for_range(salary_day: int, start: date, end: date) -> date | None:
    """The budget month whose days are exactly start..end, or None when the range is not one."""
    key = budget_month_of(salary_day, start)
    return key if budget_range(salary_day, key) == (start, end) else None


# ---- many users at once (cron jobs) ----

def current_groups(today: date) -> list[dict]:
    """The users split by how their current budget month is cut:
    [{'key': 1st of the budget month, 'start', 'end', 'user_ids': [...] or None}]. The group with
    `user_ids` None is everyone not named in the others (no salary day, or day 1: calendar months)."""
    from .models import UserProfile

    by_day: dict[int, list[int]] = {}
    for user_id, day in UserProfile.objects.filter(salary_date__gte=2, salary_date__lte=31).values_list('user_id', 'salary_date'):
        by_day.setdefault(day, []).append(user_id)
    groups = []
    for day, ids in by_day.items():
        key = budget_month_of(day, today)
        start, end = budget_range(day, key)
        groups.append({'key': key, 'start': start, 'end': end, 'user_ids': ids})
    key = month_key(today)
    start, end = budget_range(1, key)
    groups.append({'key': key, 'start': start, 'end': end, 'user_ids': None, 'except_ids': [i for g in groups for i in g['user_ids']]})
    return groups
