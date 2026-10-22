"""The longer-form surfaces, which look back at a month or a year that is over: the monthly report email and the
Year in Review. They take what the stored rules would have said at the end of that month (`engine.compose`), pick a
few, and leave the page or template to lay them out. Nothing here is stored.

Email never carries a sensitive insight (debt, a shortfall, a long gap in income); the Year in Review page, which is
the person's own and not shared with amounts unless they choose, does not either.
"""
from __future__ import annotations

from datetime import date, timedelta

from django.utils import timezone

from ..budget_cycle import budget_month_of, budget_range, salary_day_of
from . import engine, is_engine_on
from .gating import plan_of, surface_allowed
from .types import Kind, Surface, Tone

MONTHLY_PICKS = 3
YEAR_PICKS = 4


def last_completed_day(user, day: date) -> date:
    """`day`, or the last day of the budget month before it when `day` falls inside a budget month that is not over:
    with a salary day, the cycle a calendar month ends in has only just begun, and what it would say is about its
    first few days, not about the month the report or the year is describing."""
    salary_day = salary_day_of(user)
    start, end = budget_range(salary_day, budget_month_of(salary_day, day))
    return day if day >= end else start - timedelta(days=1)


def monthly(user, end_date: date, *, profile=None) -> dict | None:
    """The monthly report's Insights section for the month that ended on `end_date`, or None (not on Pro, the engine
    off, or nothing to say). `highlights` are up to three from different modules with the good news first;
    `went_well` is the best positive one; `try_next` is the best opportunity that has a next step."""
    profile = profile if profile is not None else user.profile
    if not is_engine_on(user) or not surface_allowed(Surface.MONTHLY, plan_of(profile)):
        return None
    found = [i for i in engine.compose(user, Surface.MONTHLY, today=last_completed_day(user, end_date), profile=profile) if not i.sensitive]
    if not found:
        return None
    highlights = engine.pick(found, MONTHLY_PICKS)
    went_well = next((i for i in highlights if i.tone == Tone.POSITIVE), None)
    options = [i for i in found if i.kind == Kind.OPPORTUNITY and i.action]
    try_next = (engine.pick(options, 1, positive_first=False) or [None])[0]
    return {'highlights': highlights, 'went_well': went_well, 'try_next': try_next}


def year_lines(user, year: int, *, profile=None) -> list:
    """A few lines for the Year in Review: what the stored rules would have said on the last day of `year` (or today,
    for the year in progress). Empty when the engine is off."""
    profile = profile if profile is not None else user.profile
    if not is_engine_on(user):
        return []
    today = timezone.localdate()
    today = last_completed_day(user, date(year, 12, 31)) if date(year, 12, 31) < today else today
    found = engine.compose(user, Surface.YEAR_IN_REVIEW, today=today, profile=profile)
    return engine.pick(found, YEAR_PICKS, allow_sensitive=False)
