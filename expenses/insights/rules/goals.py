"""Savings goal rules: where a goal is heading at the person's own pace, what a small change would do, a long quiet
spell, and the 50/90/100% marks. The pace and the marks come from goal_progress, which the goals pages and the
existing milestone bell share. Nothing here says a goal "should" be reached faster."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from ... import goal_progress
from .. import sampling
from ..format import Fmt
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
MAX_MONTHS = 120               # a goal further off than ten years at this pace is not worth a sentence
STALLED_DAYS = 45
SMALL_CHANGE = D('0.05')       # the "small change" is 5% of what is left, a month, rounded to a friendly step
MIN_MONTHS_BROUGHT_FORWARD = 2
MILESTONE_DAYS = 14            # a mark is mentioned for two weeks after it is reached
LATE_BY_MONTHS = 1


def _step(fmt) -> Decimal:
    return D(500) if fmt.indian else D(50)


def _open_goals(ctx):
    for gf in ctx.goals:
        if not gf.locked and not gf.goal.is_completed and gf.goal.target_amount > gf.goal.current_amount:
            yield gf


def _going(ctx, gf) -> bool:
    """A goal still being added to. One that has gone quiet is described by goals.stalled; a finish date worked out
    from contributions that stopped would read as a promise."""
    return bool(gf.contributions) and (ctx.today - gf.contributions[-1][0]).days < STALLED_DAYS


def _link(goal) -> str:
    return reverse('goal-detail', args=[goal.uuid])


def _remaining(goal) -> Decimal:
    return D(str(goal.target_amount)) - D(str(goal.current_amount))


@rule(id='goals.projected_finish', module=Module.GOALS, surfaces={Surface.DASHBOARD, Surface.MODULE}, needs={'goals'},
      cooldown_days=30, requires={'goals'}, sample=sampling.goals_sample)
def projected_finish(ctx) -> list[Insight]:
    """Where a goal reaches its target at the pace of the last three months, and whether that is before its date."""
    found = []
    for gf in _open_goals(ctx):
        goal = gf.goal
        pace = goal_progress.monthly_pace(gf.contributions, ctx.today) if _going(ctx, gf) else None
        months = goal_progress.months_to_finish(_remaining(goal), pace) if pace else None
        if months is None or months > MAX_MONTHS:
            continue
        reach = goal_progress.finish_date(ctx.today, months)
        own, name = Fmt(goal.currency), Fmt.short(goal.name)
        later = bool(goal.target_date) and reach > goal.target_date.replace(day=1) + timedelta(days=31 * LATE_BY_MONTHS)
        args = {'name': goal.name, 'target': own.approx(goal.target_amount), 'when': ctx.fmt.month_year(reach),
                'date': ctx.fmt.month_year(goal.target_date) if goal.target_date else ''}
        if later:
            title = _('%(name)s may finish a little later') % {'name': name}
            message = _('At the pace of the last 3 months, it reaches %(target)s around %(when)s, a little after your '
                        '%(date)s date. A small top-up could close the gap.') % args
        else:
            title = _('%(name)s is on track for %(month)s') % {'name': name, 'month': _month_name(reach)}
            message = _('At the pace of the last 3 months, it reaches %(target)s around %(when)s.') % args
        found.append(Insight(
            rule_id='goals.projected_finish', module=Module.GOALS, kind=Kind.PROGRESS,
            tone=Tone.GENTLE_ATTENTION if later else Tone.POSITIVE, priority=34, icon='bullseye', title=title, message=message,
            evidence=(Evidence(_('Saved so far'), own.money(goal.current_amount), _link(goal)),
                      Evidence(_('Each month'), own.money(pace))),
            action=Action(_('Open the goal'), _link(goal)),
            dedupe_key=f'{goal.uuid}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'goal': goal.pk, 'months': months, 'pace': str(pace.quantize(D('0.01')))}))
    return found[:3]


def _month_name(value: date) -> str:
    from django.utils.formats import date_format

    from ...utils import translate_digits
    return translate_digits(date_format(value, 'F'))


@rule(id='goals.small_change', module=Module.GOALS, surfaces={Surface.MODULE, Surface.MONTHLY}, needs={'goals'}, cooldown_days=60,
      min_plan=Plan.PLUS, requires={'goals'}, preview=lambda: _('A small change for a goal'), sample=sampling.goals_sample)
def small_change(ctx) -> list[Insight]:
    """What adding a little each month (5% of what is left, rounded) would do to when a goal finishes."""
    found = []
    for gf in _open_goals(ctx):
        goal = gf.goal
        pace = goal_progress.monthly_pace(gf.contributions, ctx.today) if _going(ctx, gf) else None
        if not pace:
            continue
        remaining, own = _remaining(goal), Fmt(goal.currency)
        step = _step(own)
        extra = max(step, (remaining * SMALL_CHANGE / step).quantize(D(1)) * step)
        now, then = goal_progress.months_to_finish(remaining, pace), goal_progress.months_to_finish(remaining, pace + extra)
        if now is None or then is None or now > MAX_MONTHS or now - then < MIN_MONTHS_BROUGHT_FORWARD:
            continue
        found.append(Insight(
            rule_id='goals.small_change', module=Module.GOALS, kind=Kind.OPPORTUNITY, tone=Tone.CALM, priority=26,
            icon='plus-circle', title=_('A small change for %(name)s') % {'name': Fmt.short(goal.name)},
            message=_('Adding %(amount)s a month would bring the finish forward by about %(n)s months. '
                      'It is one option; the current pace is fine too.') % {
                'amount': own.approx(extra), 'n': ctx.fmt.number(now - then)},
            evidence=(Evidence(_('Extra each month'), own.money(extra), _link(goal)),),
            action=Action(_('Open the goal'), _link(goal)),
            dedupe_key=f'{goal.uuid}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1], surfaces=frozenset({Surface.MODULE, Surface.MONTHLY}),
            numbers={'goal': goal.pk, 'extra': str(extra), 'brought_forward': now - then}))
    return found[:2]


@rule(id='goals.stalled', module=Module.GOALS, surfaces={Surface.DASHBOARD, Surface.MODULE}, needs={'goals'},
      cooldown_days=30, min_plan=Plan.PLUS, requires={'goals'}, preview=lambda: _('A goal has been quiet lately'),
      sample=sampling.goal_stalled_sample)
def stalled(ctx) -> list[Insight]:
    """No contribution for a good while after at least two earlier ones. A pause is fine, and the copy says so."""
    found = []
    for gf in _open_goals(ctx):
        goal = gf.goal
        if len(gf.contributions) < goal_progress.MIN_CONTRIBUTIONS:
            continue
        days = (ctx.today - gf.contributions[-1][0]).days
        if days < STALLED_DAYS:
            continue
        own = Fmt(goal.currency)
        found.append(Insight(
            rule_id='goals.stalled', module=Module.GOALS, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=33,
            icon='pause-circle', title=_('%(name)s has been quiet lately') % {'name': Fmt.short(goal.name)},
            message=_('There has been no contribution for %(days)s days; it stands at %(pct)s of %(target)s. '
                      'A small top-up or a pause are both fine.') % {
                'days': ctx.fmt.number(days), 'pct': ctx.fmt.percent(goal.progress_percentage),
                'target': own.approx(goal.target_amount)},
            evidence=(Evidence(_('Saved so far'), own.money(goal.current_amount), _link(goal)),),
            action=Action(_('Open the goal'), _link(goal)),
            dedupe_key=f'{goal.uuid}:{gf.contributions[-1][0].isoformat()}', valid_until=ctx.cycle[1],
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}), numbers={'goal': goal.pk, 'days': days}))
    return found[:2]


def _reached_on(gf, threshold: int) -> date | None:
    """The day the running total of contributions first reached `threshold` percent of the target, when the
    contributions add up to what the goal holds (otherwise nobody can say when, and the mark is left alone)."""
    goal = gf.goal
    if sum((a for _d, a in gf.contributions), D(0)) != D(str(goal.current_amount)) or goal.target_amount <= 0:
        return None
    need, running = D(str(goal.target_amount)) * threshold / 100, D(0)
    for day, amount in gf.contributions:
        running += amount
        if running >= need:
            return day
    return None


@rule(id='goals.milestone', module=Module.GOALS, surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.YEAR_IN_REVIEW},
      needs={'goals'}, cooldown_days=365, requires={'goals'}, sample=sampling.goal_milestone_sample)
def milestone(ctx) -> list[Insight]:
    """A goal reached 50, 90 or 100 percent of its target in the last two weeks. The bell already announces these
    (once, by its own slug), so this surfaces them on the pages and never adds a second bell item."""
    found = []
    for gf in ctx.goals:
        goal = gf.goal
        if gf.locked or goal.target_amount <= 0:
            continue
        threshold = goal_progress.milestone_reached(D(str(goal.current_amount)) / D(str(goal.target_amount)) * 100)
        reached = _reached_on(gf, threshold) if threshold else None
        if reached is None or (ctx.today - reached).days > MILESTONE_DAYS:
            continue
        own, name = Fmt(goal.currency), Fmt.short(goal.name)
        if threshold == 100:
            title = _('%(name)s is complete') % {'name': name}
            message = _('You have reached all of %(target)s. Steady contributions got you here.') % {
                'target': own.approx(goal.target_amount)}
        else:
            title = (_('%(name)s is nearly there') if threshold == 90 else _('%(name)s is halfway')) % {'name': name}
            message = _('You have reached %(pct)s of %(target)s. Steady contributions got you here.') % {
                'pct': ctx.fmt.percent(threshold), 'target': own.approx(goal.target_amount)}
        found.append(Insight(
            rule_id='goals.milestone', module=Module.GOALS, kind=Kind.MILESTONE, tone=Tone.POSITIVE, priority=48,
            icon='flag', title=title, message=message,
            evidence=(Evidence(_('Saved so far'), own.money(goal.current_amount), _link(goal)),),
            dedupe_key=f'{goal.uuid}:{threshold}', valid_until=reached + timedelta(days=MILESTONE_DAYS),
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.YEAR_IN_REVIEW}),
            numbers={'goal': goal.pk, 'threshold': threshold}))
    return found[:2]
