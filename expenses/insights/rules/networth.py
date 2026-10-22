"""Net worth: what moved it this cycle, split into what the person kept and changes in value."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..registry import rule
from ..types import Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
MIN_DAYS = 10                   # days into the cycle before the split means anything
MIN_MOVE = D(1000)              # and a movement worth describing


@rule(id='networth.what_moved', module=Module.ACCOUNTS,
      surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW},
      needs={'networth', 'facts'}, cooldown_days=30, min_history=2, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('What moved your net worth'), sample=sampling.net_worth_sample)
def what_moved(ctx) -> list[Insight]:
    """The change in net worth since the cycle began, against what the person kept (income less spending). What is
    left over is changes in value: market moves, interest, a deposit's growth, a loan balance."""
    if ctx.elapsed_days < MIN_DAYS:
        return []
    before, now = ctx.networth
    if before is None:                    # no snapshot from the start of this cycle: nothing to compare with
        return []
    change, kept = now.total - before.total, ctx.summary(ctx.key).saved
    rest = change - kept
    if kept <= 0 or abs(change) < MIN_MOVE or before.total <= 0:
        return []
    fmt = ctx.fmt
    args = {'change': fmt.approx(abs(change)), 'kept': fmt.approx(kept), 'rest': fmt.approx(abs(rest))}
    if change > 0 and rest >= 0:
        message = _('It is up about %(change)s this cycle: roughly %(kept)s from what you kept and %(rest)s from changes in value.') % args
    elif change > 0:
        message = _('It is up about %(change)s this cycle: you kept %(kept)s, and changes in value took back %(rest)s.') % args
    else:
        message = _('It is down about %(change)s this cycle: you kept %(kept)s, and changes in value took away %(rest)s.') % args
    link = reverse('account-list')
    return [Insight(
        rule_id='networth.what_moved', module=Module.ACCOUNTS, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=42,
        icon='graph-up', title=_('What moved your net worth'), message=message,
        evidence=(Evidence(_('Net worth'), fmt.money(now.total), link), Evidence(_('Kept this cycle'), fmt.money(kept))),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW}),
        numbers={'change': str(change.quantize(D('0.01'))), 'kept': str(kept.quantize(D('0.01')))},
    )]


@rule(id='networth.what_moved_period', module=Module.ACCOUNTS, surfaces={Surface.ANALYTICS}, needs={'page'}, mode='inline',
      cooldown_days=0, min_plan=Plan.PLUS, sample=sampling.net_worth_period_sample)
def what_moved_period(ctx) -> list[Insight]:
    """The Net worth tab's sentence: over the period shown, what the person kept and what changes in value did. It
    needs the whole picture, so it says nothing while the toolbar's filters narrow the page."""
    page, change, kept = ctx.page, ctx.values.get('change'), ctx.values.get('kept')
    if page.filters_active or change is None or kept is None or kept <= 0 or abs(change) < MIN_MOVE:
        return []
    rest, fmt = change - kept, ctx.fmt
    args = {'change': fmt.approx(abs(change)), 'kept': fmt.approx(kept), 'rest': fmt.approx(abs(rest))}
    if change > 0 and rest >= 0:
        message = _('Over this period it is up about %(change)s: roughly %(kept)s from what you kept and %(rest)s from changes in value.') % args
    elif change > 0:
        message = _('Over this period it is up about %(change)s: you kept %(kept)s, and changes in value took back %(rest)s.') % args
    else:
        message = _('Over this period it is down about %(change)s: you kept %(kept)s, and changes in value took away %(rest)s.') % args
    return [Insight(
        rule_id='networth.what_moved_period', module=Module.ACCOUNTS, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=42,
        icon='graph-up', title=_('What moved your net worth'), message=message,
        dedupe_key=f'{page.period.range[0]}:{page.period.range[1]}', valid_until=page.period.range[1] + timedelta(days=1),
        surfaces=frozenset({Surface.ANALYTICS}), numbers={'change': str(change), 'kept': str(kept)})]
