"""
Period resolution module for TrackMyRupee.

Consolidates period resolution across the application so that the Dashboard,
Expenses, Income, Transactions, and Filter Engine resolve preset time periods
identically and cycle-correctly.

Semantics:
- 'this_month':
    When a non-default salary cycle is active (salary_date in 2..31), resolves to the
    salary cycle containing `today`. When salary_date == 1 or user has no profile,
    resolves to the current calendar month (1st of month to month-end).
- 'last_month':
    The cycle immediately preceding 'this_month' (e.g. cycle containing current_cycle_start - 1 day).
    When salary_date == 1 or no profile, resolves to the previous calendar month.
- 'last_3_months':
    A rolling window: `today - 90 days` to `today`. Calendar-based.
- 'last_6_months':
    A rolling window: `today - 180 days` to `today`. Calendar-based.
- 'this_year':
    Full calendar year: `1 Jan` to `31 Dec` of `today.year`.
- 'all':
    Unbounded date range (`start=None, end=None`). Stale start_date/end_date params are ignored.
- 'custom':
    Safely parsed start_date and end_date strings (YYYY-MM-DD). Malformed or unparseable
    dates are safely converted to None.
- Unrecognized or empty:
    Falls back to 'this_month'.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

from django.utils import timezone

from .services import SalaryAnalysisService


@dataclass(frozen=True)
class ResolvedPeriod:
    start: Optional[date]
    end: Optional[date]
    key: str
    is_cycle: bool


def _parse_safe_date(val: Any) -> Optional[date]:
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str) and val.strip():
        try:
            return datetime.strptime(val.strip(), '%Y-%m-%d').date()
        except (ValueError, TypeError):
            return None
    return None


def resolve_period(
    user: Any = None,
    time_period: Optional[str] = None,
    start_date: Any = None,
    end_date: Any = None,
    today: Optional[date] = None,
) -> ResolvedPeriod:
    """Resolve a time period for a user into a date range, canonical key, and cycle flag."""
    if today is None:
        today = timezone.localtime().date()

    profile = getattr(user, 'profile', None) if user and getattr(user, 'is_authenticated', True) else None
    salary_date = getattr(profile, 'salary_date', None)
    has_custom_cycle = (
        salary_date is not None
        and isinstance(salary_date, int)
        and 2 <= salary_date <= 31
    )

    clean_key = (time_period or '').strip()

    # Determine canonical key if empty
    if not clean_key:
        parsed_start = _parse_safe_date(start_date)
        parsed_end = _parse_safe_date(end_date)
        if parsed_start or parsed_end:
            clean_key = 'custom'
        else:
            clean_key = 'this_month'

    if clean_key == 'this_month':
        if has_custom_cycle:
            start, end = SalaryAnalysisService.get_salary_cycle_dates(user, today)
            return ResolvedPeriod(start=start, end=end, key='this_month', is_cycle=True)
        else:
            start = today.replace(day=1)
            last_day = calendar.monthrange(today.year, today.month)[1]
            end = today.replace(day=last_day)
            return ResolvedPeriod(start=start, end=end, key='this_month', is_cycle=False)

    elif clean_key == 'last_month':
        if has_custom_cycle:
            current_start, _ = SalaryAnalysisService.get_salary_cycle_dates(user, today)
            prev_cycle_target = current_start - timedelta(days=1)
            start, end = SalaryAnalysisService.get_salary_cycle_dates(user, prev_cycle_target)
            return ResolvedPeriod(start=start, end=end, key='last_month', is_cycle=True)
        else:
            first_day_this_month = today.replace(day=1)
            last_day_last_month = first_day_this_month - timedelta(days=1)
            start = last_day_last_month.replace(day=1)
            end = last_day_last_month
            return ResolvedPeriod(start=start, end=end, key='last_month', is_cycle=False)

    elif clean_key == 'last_3_months':
        return ResolvedPeriod(
            start=today - timedelta(days=90),
            end=today,
            key='last_3_months',
            is_cycle=False,
        )

    elif clean_key == 'last_6_months':
        return ResolvedPeriod(
            start=today - timedelta(days=180),
            end=today,
            key='last_6_months',
            is_cycle=False,
        )

    elif clean_key == 'this_year':
        return ResolvedPeriod(
            start=today.replace(month=1, day=1),
            end=today.replace(month=12, day=31),
            key='this_year',
            is_cycle=False,
        )

    elif clean_key == 'all':
        return ResolvedPeriod(start=None, end=None, key='all', is_cycle=False)

    elif clean_key == 'custom':
        start = _parse_safe_date(start_date)
        end = _parse_safe_date(end_date)
        return ResolvedPeriod(start=start, end=end, key='custom', is_cycle=False)

    else:
        # Fallback for any unrecognized value -> this_month
        return resolve_period(
            user=user,
            time_period='this_month',
            today=today,
        )


def calculate_budget_period_factor(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> float:
    """
    Calculate the cumulative budget scaling factor across the period [start_date, end_date].
    Each day in a given month contributes (1.0 / days_in_month) to the total monthly factor.
    A full month evaluates to exactly 1.0. A full year evaluates to exactly 12.0.
    Partial months contribute proportionally based on days elapsed in that month.

    If start_date or end_date is None, or if start_date > end_date, returns 1.0.
    """
    if not start_date or not end_date or start_date > end_date:
        return 1.0

    if start_date.year == end_date.year and start_date.month == end_date.month:
        days_in_month = calendar.monthrange(start_date.year, start_date.month)[1]
        days = (end_date - start_date).days + 1
        if days >= days_in_month:
            return 1.0
        return round(days / days_in_month, 4)

    total_factor = 0.0

    # First month (partial or full)
    days_in_first = calendar.monthrange(start_date.year, start_date.month)[1]
    days_first = days_in_first - start_date.day + 1
    total_factor += days_first / days_in_first

    # Intermediate months (always full months = 1.0 each)
    curr_year = start_date.year
    curr_month = start_date.month + 1
    if curr_month > 12:
        curr_month = 1
        curr_year += 1

    while (curr_year < end_date.year) or (curr_year == end_date.year and curr_month < end_date.month):
        total_factor += 1.0
        curr_month += 1
        if curr_month > 12:
            curr_month = 1
            curr_year += 1

    # Last month (partial or full)
    days_in_last = calendar.monthrange(end_date.year, end_date.month)[1]
    days_last = end_date.day
    total_factor += days_last / days_in_last

    return round(total_factor, 4)
