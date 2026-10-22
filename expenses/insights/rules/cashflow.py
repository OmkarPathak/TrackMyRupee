"""Cash-flow rules: how the cycle is going against the person's own usual, and their saving habit."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from ...analytics.links import SPENDING_TYPES
from ...utils import get_safe_date
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone
from .expenses import _is_posting

D = Decimal
PACE_FACTOR = D('1.25')         # this much above the usual before the pace is worth a mention
MIN_SPENT = D('1000')           # and some real spending so far (the dashboard has always used 1,000)
CENTS = D('0.01')
LOOKAHEAD_DAYS = 14             # the next payday is looked for this far ahead
TYPICAL_DAYS = 28               # everyday spending is read from this many days
BUFFER_DAYS = 7                 # less than this many days of everyday spending left is a tight stretch
OUTFLOW_KINDS = ('out', 'invest')   # scheduled items that leave the bank accounts


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


def _payday(ctx):
    """The next salary: a scheduled salary income in the next two weeks, else the person's own salary day."""
    for day in sorted(ctx.pending):
        if any(item['is_salary'] for item in ctx.pending[day]):
            return day
    if ctx.is_cycle:
        this = get_safe_date(ctx.today.year, ctx.today.month, ctx.salary_day)
        day = this if this > ctx.today else get_safe_date(ctx.today.year + (ctx.today.month == 12),
                                                          ctx.today.month % 12 + 1, ctx.salary_day)
        if (day - ctx.today).days <= LOOKAHEAD_DAYS:
            return day
    return None


@rule(id='cashflow.low_point_before_salary', module=Module.CASHFLOW, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'rows', 'cash', 'pending'}, cooldown_days=7, min_history=1, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('A closer look at the days before payday'), sample=sampling.low_point_sample)
def low_point_before_salary(ctx) -> list[Insight]:
    """The lowest the bank balance could get before the next salary, at the person's everyday pace: what is held now,
    less the scheduled payments, less a day of everyday spending (what they typed in the last four weeks, leaving out
    what the app posted itself, which is already in the schedule) for each day. Said only when under a week of
    everyday spending would be left, and extra gently when it could dip below zero."""
    payday = _payday(ctx)
    if payday is None or (payday - ctx.today).days < 2:
        return []
    cash = ctx.cash.idle
    since = ctx.today - timedelta(days=TYPICAL_DAYS)
    typical = sum((r.amount for r in ctx.expense_rows if r.date > since and not _is_posting(r.description)), D(0)) / TYPICAL_DAYS
    if cash <= 0:
        return []
    low, low_day, balance, scheduled = None, None, cash, D(0)
    for offset in range((payday - ctx.today).days):
        day = ctx.today + timedelta(days=offset)
        for item in ctx.pending.get(day, []):
            if item['kind'] in OUTFLOW_KINDS:
                balance, scheduled = balance - item['amount'], scheduled + item['amount']
            elif item['kind'] == 'in' and not item['is_salary']:
                balance += item['amount']
        level = balance - typical * offset
        if low is None or level < low:
            low, low_day = level, day
    if low >= typical * BUFFER_DAYS:
        return []
    fmt, below = ctx.fmt, low < 0
    args = {'amount': fmt.approx(max(low, D(0))), 'day': fmt.day(low_day), 'payday': fmt.day(payday)}
    message = (_('At this pace, your bank balance could run a little short around %(day)s, before your salary on '
                 '%(payday)s. If it helps, you could move a bill or keep a small buffer.') if below else
               _('At this pace, your bank balance could dip to about %(amount)s around %(day)s, before your salary on '
                 '%(payday)s. If it helps, you could move a bill or keep a small buffer.')) % args
    return [Insight(
        rule_id='cashflow.low_point_before_salary', module=Module.CASHFLOW, kind=Kind.UPCOMING, tone=Tone.GENTLE_ATTENTION,
        priority=58, icon='calendar-range', sensitive=below,
        title=_('A tight stretch before payday'), message=message,
        evidence=(Evidence(_('Cash and bank today'), fmt.money(cash), ctx.links.accounts()),
                  Evidence(_('Scheduled until payday'), fmt.money(scheduled), reverse('calendar')),
                  Evidence(_('Everyday spending a day'), fmt.money(typical))),
        action=Action(_('View the calendar'), reverse('calendar')),
        dedupe_key=f'{payday:%Y-%m-%d}', valid_until=payday,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'short': below, 'low_day': low_day.isoformat()},
    )]
