"""Cash-flow rules: how the cycle is going against the person's own usual, and their saving habit."""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from ...analytics.links import SPENDING_TYPES
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Surface, Tone

D = Decimal
PACE_FACTOR = D('1.25')         # this much above the usual before the pace is worth a mention
MIN_SPENT = D('1000')           # and some real spending so far (the dashboard has always used 1,000)
CENTS = D('0.01')


def _q(value) -> str:
    return str(D(value).quantize(CENTS, rounding=ROUND_HALF_UP))


@rule(id='cashflow.pace_vs_usual', module=Module.CASHFLOW,
      surfaces={Surface.DASHBOARD, Surface.MODULE}, needs={'facts'}, cooldown_days=7, min_history=1,
      sample=sampling.default_sample)
def pace_vs_usual(ctx) -> list[Insight]:
    """Projected spending for the cycle against the average of up to three earlier cycles."""
    start, end = ctx.cycle
    spent = ctx.summary(ctx.key).spending
    earlier = [s for s in (ctx.summary(k).spending for k in ctx.keys_back(3)) if s > 0]
    if spent <= 0 or not earlier:
        return []
    usual = sum(earlier, D(0)) / len(earlier)
    projected = spent / ctx.elapsed_days * ctx.cycle_days
    if not (projected > usual * PACE_FACTOR and spent > MIN_SPENT):
        return []
    pct = int((projected - usual) / usual * 100)
    fmt = ctx.fmt
    spending_link = ctx.links.transactions(start, ctx.today, types=SPENDING_TYPES)
    return [Insight(
        rule_id='cashflow.pace_vs_usual', module=Module.CASHFLOW, kind=Kind.PATTERN, tone=Tone.GENTLE_ATTENTION,
        priority=60, icon='graph-up-arrow',
        title=_('Spending is running a little higher'),
        message=_('At this pace you could end up about %(pct)s above your usual %(usual)s. '
                  'Money moves in waves, so this may simply be a busy stretch.') % {
            'pct': fmt.percent(pct), 'usual': fmt.approx(usual)},
        detail=_('If it is on purpose, you can dismiss this.'),
        evidence=(
            Evidence(_('Spent so far'), fmt.money(spent), spending_link),
            Evidence(_('Your usual'), fmt.money(usual)),
            Evidence(_('At this pace'), fmt.money(projected)),
        ),
        action=Action(_('See where it went'), spending_link),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'pct': pct, 'projected': _q(projected), 'usual': _q(usual)},
    )]


@rule(id='cashflow.saving_streak', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD}, needs={'facts'},
      cooldown_days=30, min_history=1, sample=sampling.default_sample)
def saving_streak(ctx) -> list[Insight]:
    """Months in a row (this one included, up to six) in which more came in than went out."""
    if ctx.summary(ctx.key).saved <= 0:
        return []
    streak = 1
    for key in ctx.keys_back(5):
        if ctx.summary(key).saved > 0:
            streak += 1
        else:
            break
    if streak < 2:
        return []
    start, end = ctx.cycle
    return [Insight(
        rule_id='cashflow.saving_streak', module=Module.CASHFLOW, kind=Kind.PROGRESS, tone=Tone.POSITIVE,
        priority=40, icon='graph-up',
        title=_('A steady run of saving'),
        message=ngettext(
            'You have kept more than you spent for %(n)s month in a row. Small habits like this add up quietly.',
            'You have kept more than you spent for %(n)s months in a row. Small habits like this add up quietly.',
            streak) % {'n': ctx.fmt.number(streak)},
        evidence=(Evidence(_('Kept so far'), ctx.fmt.money(ctx.summary(ctx.key).saved),
                           ctx.links.transactions(start, ctx.today)),),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end, surfaces=frozenset({Surface.DASHBOARD}),
        numbers={'streak': streak},
    )]


@rule(id='cashflow.savings_momentum', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD}, needs={'facts'},
      cooldown_days=14, min_history=1, sample=sampling.default_sample)
def savings_momentum(ctx) -> list[Insight]:
    """What has been kept so far is already more than the whole of the previous cycle."""
    now = ctx.summary(ctx.key).saved
    before = ctx.summary(ctx.keys_back(1)[0]).saved
    if not (now > 0 and before > 0 and now > before):
        return []
    pct = int((now - before) / before * 100)
    fmt, (start, end) = ctx.fmt, ctx.cycle
    args = {'now': fmt.approx(now), 'before': fmt.approx(before)}
    message = (_('You have kept about %(now)s so far, up from %(before)s in the previous cycle. That is a good sign.')
               if ctx.is_cycle else
               _('You have kept about %(now)s so far, up from %(before)s last month. That is a good sign.')) % args
    return [Insight(
        rule_id='cashflow.savings_momentum', module=Module.CASHFLOW, kind=Kind.PROGRESS, tone=Tone.POSITIVE,
        priority=45, icon='piggy-bank',
        title=_('Your savings are growing'), message=message,
        evidence=(
            Evidence(_('Kept so far'), fmt.money(now), ctx.links.transactions(start, ctx.today)),
            Evidence(_('Kept before'), fmt.money(before)),
            Evidence(_('Change'), fmt.percent(pct)),
        ),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end, surfaces=frozenset({Surface.DASHBOARD}),
        numbers={'pct': pct},
    )]


@rule(id='cashflow.year_pace', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD, Surface.ANALYTICS},
      needs={'facts'}, cooldown_days=30, min_history=1, sample=sampling.default_sample)
def year_pace(ctx) -> list[Insight]:
    """Year-to-date savings, carried forward at the same monthly average to the end of the year."""
    months = ctx.key.month
    if months < 2:
        return []
    kept = sum((ctx.summary(date(ctx.key.year, m, 1)).saved for m in range(1, months + 1)), D(0))
    if kept <= 0:
        return []
    projected = kept + kept / months * (12 - months)
    first_day = ctx.cycle_of(date(ctx.key.year, 1, 1))[0]
    return [Insight(
        rule_id='cashflow.year_pace', module=Module.CASHFLOW, kind=Kind.PROGRESS, tone=Tone.POSITIVE,
        priority=35, icon='graph-up-arrow',
        title=_('Your year is shaping up well'),
        message=_("If this year's average continues, you could keep around %(amount)s by the end of the year. "
                  'It is only an estimate and will move as your months do.') % {'amount': ctx.fmt.approx(projected)},
        evidence=(Evidence(_('Kept so far this year'), ctx.fmt.money(kept),
                           ctx.links.transactions(first_day, ctx.today)),),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.ANALYTICS}),
        numbers={'projected': _q(projected), 'kept': _q(kept)},
    )]
