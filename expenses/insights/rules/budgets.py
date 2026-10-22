"""Budget rules, read from the Budget page's own payload so the numbers match that page.

These count budget spending (expenses plus capital events not excluded from the budget), the Budget page's
definition, not the savings definition. Each rule speaks once per cycle about all the categories it concerns.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from ...analytics.links import CAPITAL_EVENT, EXPENSE
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Surface, Tone

D = Decimal
SPEND_TYPES = (EXPENSE, CAPITAL_EVENT)
EVIDENCE_ROWS = 3


def _dec(value) -> Decimal:
    return D(str(value or 0))


def _rows(ctx) -> list[dict]:
    page = ctx.budgets
    return page['categories'] if page.get('phase') == 'current' else []


def _evidence(ctx, rows: list[dict]) -> tuple[Evidence, ...]:
    start, end = ctx.cycle
    return tuple(Evidence(
        r['name'],
        _('%(spent)s of %(limit)s') % {'spent': ctx.fmt.money(r['spent']), 'limit': ctx.fmt.money(r['limit'])},
        ctx.links.transactions(start, end, types=SPEND_TYPES, category=r['name']),
    ) for r in rows[:EVIDENCE_ROWS])


def _names(rows) -> list[str]:
    return sorted(r['name'].strip().lower() for r in rows)


def _over(rows) -> list[dict]:
    return [r for r in rows if r['limit'] and r['percent'] is not None and r['percent'] > 100]


@rule(id='budgets.limit_passed', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'budgets'}, cooldown_days=7, sample=sampling.over_limit_sample)
def limit_passed(ctx) -> list[Insight]:
    over = _over(_rows(ctx))
    if not over:
        return []
    fmt, (_start, end) = ctx.fmt, ctx.cycle
    over.sort(key=lambda r: -_dec(r['over_by']))
    biggest, count = over[0], len(over)
    within = [r for r in _rows(ctx) if r['limit'] and r['percent'] is not None and r['percent'] <= 100]
    args = {'category': biggest['name'], 'n': fmt.number(count), 'over': fmt.approx(biggest['over_by']),
            'limit': fmt.approx(biggest['limit'])}
    args_title = {**args, 'category': fmt.short(biggest['name'])}
    detail = ngettext('%(n)s other category with a limit is within it.',
                      '%(n)s other categories with limits are within them.',
                      len(within)) % {'n': fmt.number(len(within))} if within else None
    if count == 1:
        title = _('%(category)s went past its limit') % args_title
        message = _('%(category)s is about %(over)s over its %(limit)s limit. '
                    'It happens in many months, and you could raise the limit or leave it as it is.') % args
    else:
        title = _('%(n)s categories went past their limits') % args
        message = _('%(n)s categories are over their limits so far, the largest being %(category)s by about %(over)s. '
                    'It happens in many months, and a limit can be adjusted if it no longer fits.') % args
    return [Insight(
        rule_id='budgets.limit_passed', module=Module.BUDGETS, kind=Kind.OBSERVATION, tone=Tone.GENTLE_ATTENTION,
        priority=70, icon='wallet2', title=title, message=message,
        detail=detail,
        evidence=_evidence(ctx, over),
        action=Action(_('Review the limit'), reverse('budget')),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'categories': _names(over)},
    )]


@rule(id='budgets.limit_nearing', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'budgets'}, cooldown_days=7, sample=sampling.near_limit_sample)
def limit_nearing(ctx) -> list[Insight]:
    rows = _rows(ctx)
    if _over(rows):
        return []                      # a passed limit already says it; the two never show together
    near = [r for r in rows if r['status'] == 'limit']
    if not near:
        return []
    fmt, (_start, end) = ctx.fmt, ctx.cycle
    near.sort(key=lambda r: -(r['percent'] or 0))
    lead, count = near[0], len(near)
    args = {'category': lead['name'], 'n': fmt.number(count), 'percent': fmt.percent(lead['percent']),
            'limit': fmt.approx(lead['limit'])}
    if count == 1:
        title = _('%(category)s is close to its limit') % {**args, 'category': fmt.short(lead['name'])}
        message = _('%(category)s has used about %(percent)s of its %(limit)s limit so far. '
                    'If that feels right, there is nothing to do.') % args
    else:
        title = _('%(n)s categories are close to their limits') % args
        message = _('%(n)s categories have used most of their limits so far, %(category)s the most at about '
                    '%(percent)s. If that feels right, there is nothing to do.') % args
    return [Insight(
        rule_id='budgets.limit_nearing', module=Module.BUDGETS, kind=Kind.OBSERVATION, tone=Tone.GENTLE_ATTENTION,
        priority=55, icon='wallet2', title=title, message=message,
        evidence=_evidence(ctx, near),
        action=Action(_('Review the limit'), reverse('budget')),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'categories': _names(near)},
    )]


@rule(id='budgets.category_runout', module=Module.BUDGETS,
      surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}, needs={'budgets'},
      cooldown_days=5, push=True, sample=sampling.runout_sample)
def category_runout(ctx) -> list[Insight]:
    """A category still within its limit that, at the pace so far, would pass it before the cycle ends."""
    start, end = ctx.cycle
    elapsed, length = ctx.elapsed_days, ctx.cycle_days
    running_out = []
    for r in _rows(ctx):
        if not r['limit'] or r['percent'] is None or r['percent'] > 100 or _dec(r['spent']) <= 0:
            continue
        projected_pct = round(_dec(r['spent']) / elapsed * length / _dec(r['limit']) * 100, 1)
        if projected_pct > 100:
            days_to_limit = elapsed * _dec(r['limit']) / _dec(r['spent'])
            running_out.append((start + timedelta(days=int(days_to_limit)), r))
    if not running_out:
        return []
    running_out.sort(key=lambda item: (item[0], item[1]['name'].lower()))
    first_day, lead = running_out[0]
    fmt, count = ctx.fmt, len(running_out)
    rows = [r for _day, r in running_out]
    args = {'category': lead['name'], 'n': fmt.number(count), 'limit': fmt.approx(lead['limit']),
            'date': fmt.day(first_day), 'spent': fmt.approx(lead['spent'])}
    if count == 1:
        title = _('%(category)s may reach its limit early') % {**args, 'category': fmt.short(lead['name'])}
        message = _('At this pace, %(category)s could reach its %(limit)s limit around %(date)s; %(spent)s is used '
                    'so far. If that sounds right, you could ease the pace or leave it.') % args
    else:
        title = _('%(n)s categories may reach their limits early') % args
        message = _('At this pace, %(n)s categories could reach their limits before the period ends, the first being '
                    '%(category)s around %(date)s. If that sounds right, there is nothing to do.') % args
    return [Insight(
        rule_id='budgets.category_runout', module=Module.BUDGETS, kind=Kind.UPCOMING, tone=Tone.GENTLE_ATTENTION,
        priority=65, icon='speedometer2', title=title, message=message,
        evidence=_evidence(ctx, rows),
        action=Action(_('Review the limit'), reverse('budget')),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}),
        numbers={'categories': _names(rows)},
    )]
