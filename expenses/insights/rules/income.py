"""Income rules: how steady it is, whether the usual salary has arrived, and a long quiet spell. They describe the
person's own pattern; none of them says what income "should" be."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.urls import reverse
from django.utils.translation import gettext as _

from ...utils import get_safe_date
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
SALARY = 'Salary'
LATE_AFTER_DAYS = 3             # the salary is "late" this many days after the day it usually arrives
LATE_WINDOW_DAYS = 20           # and the note stops after this many
SALARY_EARLY_DAYS = 3           # salary credited this many days before the cycle begins is still this cycle's
MIN_SALARY_MONTHS = 2           # months with a salary entry before a "usual day" is learned
GAP_DAYS = 45
GAP_PATTERN_MONTHS = 3          # months with income before a gap means anything


def _months(rows) -> set[tuple[int, int]]:
    return {(r.date.year, r.date.month) for r in rows}


def _usual_salary_day(ctx, salary_rows) -> int | None:
    """The day of the month salary usually arrives: the person's own salary day when they set one, else the middle of
    the days it arrived on (needs at least two months of salary entries)."""
    if len(_months(salary_rows)) < MIN_SALARY_MONTHS:
        return None
    if ctx.salary_day > 1:
        return ctx.salary_day
    return int(median(r.date.day for r in salary_rows))


@rule(id='income.steady_vs_variable', module=Module.INCOME,
      surfaces={Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW},
      needs={'income_rows', 'facts'}, cooldown_days=60, min_history=3, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('How steady your income is'), sample=sampling.income_sample)
def steady_vs_variable(ctx) -> list[Insight]:
    """How much of the last three cycles' income was salary, and what the rest did from cycle to cycle."""
    keys = ctx.keys_back(3)
    first, last = ctx.cycle_of(keys[-1])[0], ctx.cycle_of(keys[0])[1]
    rows = [r for r in ctx.income_rows if first <= r.date <= last]
    total = sum((r.amount for r in rows), D(0))
    salary = sum((r.amount for r in rows if r.source_type == SALARY), D(0))
    if total <= 0 or salary <= 0 or salary >= total:
        return []
    per_cycle = []
    for key in keys:
        start, end = ctx.cycle_of(key)
        per_cycle.append(sum((r.amount for r in rows if start <= r.date <= end and r.source_type != SALARY), D(0)))
    varied = [v for v in per_cycle if v > 0]
    fmt, share = ctx.fmt, salary / total * 100
    args = {'share': fmt.percent(share), 'low': fmt.approx(min(varied)), 'high': fmt.approx(max(varied)),
            'avg': fmt.approx((total - salary) / len(keys))}
    if len(varied) >= 2 and min(varied) != max(varied):
        message = _('About %(share)s of your income came from salary; the rest varied between %(low)s and %(high)s a cycle.') % args
    else:
        message = _('About %(share)s of your income came from salary; the rest came to around %(avg)s a cycle.') % args
    link = reverse('income-list')
    return [Insight(
        rule_id='income.steady_vs_variable', module=Module.INCOME, kind=Kind.PATTERN, tone=Tone.CALM, priority=36,
        icon='cash-stack', title=_('How steady your income is'), message=message,
        evidence=(Evidence(_('Salary'), fmt.money(salary), link), Evidence(_('Other income'), fmt.money(total - salary))),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW}),
        numbers={'share': round(float(share)), 'low': str(min(varied)), 'high': str(max(varied))},
    )]


@rule(id='income.salary_late', module=Module.INCOME, surfaces={Surface.DASHBOARD, Surface.MODULE}, needs={'income_rows'},
      cooldown_days=5, min_history=2, min_plan=Plan.PLUS, preview=lambda: _('Salary has not arrived yet'),
      sample=sampling.salary_late_sample)
def salary_late(ctx) -> list[Insight]:
    """The usual salary day was a few days ago and no salary is recorded in this cycle. Gentle: it may be on its way."""
    salary_rows = [r for r in ctx.income_rows if r.source_type == SALARY]
    day = _usual_salary_day(ctx, salary_rows)
    if day is None:
        return []
    start, end = ctx.cycle
    if ctx.salary_day > 1:
        expected = start
    else:
        expected = get_safe_date(start.year, start.month, day)
    if any(start - timedelta(days=SALARY_EARLY_DAYS) <= r.date <= end for r in salary_rows):      # paid a few days early (a weekend) counts
        return []
    late_by = (ctx.today - expected).days
    if not LATE_AFTER_DAYS <= late_by <= LATE_WINDOW_DAYS:
        return []
    link = reverse('income-create')
    return [Insight(
        rule_id='income.salary_late', module=Module.INCOME, kind=Kind.UPCOMING, tone=Tone.GENTLE_ATTENTION, priority=55,
        icon='calendar-event', title=_('Salary has not arrived yet'),
        message=_('It usually lands around day %(day)s of the month and none is recorded this cycle. '
                  'If it is on its way, there is nothing to do.') % {'day': ctx.fmt.number(day)},
        action=Action(_('Add income'), link),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=min(end, expected + timedelta(days=LATE_WINDOW_DAYS)),
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}), numbers={'day': day, 'late_by': late_by},
    )]


@rule(id='income.gap', module=Module.INCOME, surfaces={Surface.MODULE}, needs={'income_rows'}, cooldown_days=30,
      min_history=3, min_plan=Plan.PLUS, cost='heavy', sample=sampling.income_gap_sample)
def gap(ctx) -> list[Insight]:
    """No income recorded for a good while after a regular run of it. Sensitive: extra gentle, no share, no preview."""
    rows = ctx.income_rows
    if not rows or len(_months(rows)) < GAP_PATTERN_MONTHS:
        return []
    days = (ctx.today - rows[-1].date).days
    if days < GAP_DAYS:
        return []
    link = reverse('income-create')
    return [Insight(
        rule_id='income.gap', module=Module.INCOME, kind=Kind.OBSERVATION, tone=Tone.GENTLE_ATTENTION, priority=40,
        icon='hourglass-split', sensitive=True, title=_('No income recorded for a while'),
        message=_('There has been no income entry for %(days)s days. You can add income whenever you are ready.') % {
            'days': ctx.fmt.number(days)},
        evidence=(Evidence(_('Last income'), ctx.fmt.day_year(rows[-1].date), reverse('income-list')),),
        action=Action(_('Add income'), link),
        dedupe_key=f'{rows[-1].date.isoformat()}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.MODULE}), numbers={'days': days},
    )]
