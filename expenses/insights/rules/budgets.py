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

from ... import budget_suggestions
from ...analytics.links import CAPITAL_EVENT, EXPENSE
from ...budgets import has_limit, normalize_category
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
SPEND_TYPES = (EXPENSE, CAPITAL_EVENT)
EVIDENCE_ROWS = 3
UNDER_SHARE = D('0.7')          # "well under": 70% of the limit or less, in each of three budget months


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
      surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}, needs={'budgets', 'budget_history'},
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
    weekly = _weekly_when_within(ctx, lead['name'])
    detail = _('In earlier cycles when it stayed within the limit, it averaged about %(weekly)s a week.') % {
        'weekly': fmt.approx(weekly)} if count == 1 and weekly else None
    return [Insight(
        rule_id='budgets.category_runout', module=Module.BUDGETS, kind=Kind.UPCOMING, tone=Tone.GENTLE_ATTENTION,
        priority=65, icon='speedometer2', title=title, message=message, detail=detail,
        evidence=_evidence(ctx, rows),
        action=Action(_('Review the limit'), reverse('budget')),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}),
        numbers={'categories': _names(rows)},
    )]


def _weekly_when_within(ctx, name: str) -> Decimal | None:
    """What the person spent a week on a category, on average, in the earlier cycles that stayed within its limit:
    "what has historically helped". None when no earlier cycle did."""
    history, key = ctx.budget_history, normalize_category(name)
    spent = days = D(0)
    for month in history.months:
        limit, amount = history.limits.get(month, {}).get(key), history.spend.get(month, {}).get(key, D(0))
        if has_limit(limit) and amount <= limit:
            start, end = ctx.cycle_of(month)
            spent, days = spent + amount, days + ((end - start).days + 1)
    return spent / days * 7 if days and spent > 0 else None


def _three_months(ctx) -> dict[str, tuple[str, list[tuple]]]:
    """{normalised name: (name, [(budget month, limit, spent) x 3])} for each category that had a limit in all of
    the last three budget months (a category with no limit in one of them has no pattern to speak of)."""
    history, out = ctx.budget_history, {}
    for row in _rows(ctx):
        key, cells = normalize_category(row['name']), []
        for month in history.months:
            limit = history.limits.get(month, {}).get(key)
            if not has_limit(limit):
                break
            cells.append((month, limit, history.spend.get(month, {}).get(key, D(0))))
        else:
            out[key] = (row['name'], cells)
    return out


def _month_chips(ctx, name: str, cells) -> tuple[Evidence, ...]:
    return tuple(Evidence(
        ctx.fmt.month(month),
        _('%(spent)s of %(limit)s') % {'spent': ctx.fmt.money(spent), 'limit': ctx.fmt.money(limit)},
        ctx.links.transactions(*ctx.cycle_of(month), types=SPEND_TYPES, category=name),
    ) for month, limit, spent in cells)


@rule(id='budgets.limit_often_over', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'budgets', 'budget_history'}, cooldown_days=45, min_history=3, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('A pattern in one of your limits'), sample=sampling.often_over_sample)
def limit_often_over(ctx) -> list[Insight]:
    """A category that finished above its limit in each of the last three budget months."""
    found = []
    for key, (name, cells) in _three_months(ctx).items():
        if all(spent > limit for _month, limit, spent in cells):
            found.append((sum((spent - limit for _m, limit, spent in cells), D(0)) / 3, key, name, cells))
    out = []
    for over, key, name, cells in sorted(found, key=lambda t: -t[0])[:2]:
        fmt = ctx.fmt
        out.append(Insight(
            rule_id='budgets.limit_often_over', module=Module.BUDGETS, kind=Kind.OPPORTUNITY, tone=Tone.CALM,
            priority=52, icon='bar-chart-steps',
            title=_('%(category)s keeps passing its limit') % {'category': fmt.short(name)},
            message=_('%(category)s ended above its limit in each of the last 3 budget months, by about %(over)s on '
                      'average. You could raise the limit to match how you live, or keep it as it is.') % {
                'category': name, 'over': fmt.approx(over)},
            evidence=_month_chips(ctx, name, cells),
            action=Action(_('Review the limit'), reverse('budget')),
            dedupe_key=f'over:{key[:50]}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'category': key},
        ))
    return out


@rule(id='budgets.limit_often_under', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'budgets', 'budget_history'}, cooldown_days=45, min_history=3, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('A limit with room to spare'), sample=sampling.often_under_sample)
def limit_often_under(ctx) -> list[Insight]:
    """A category that finished at 70% of its limit or less in each of the last three budget months."""
    found = []
    for key, (name, cells) in _three_months(ctx).items():
        if all(spent <= limit * UNDER_SHARE for _month, limit, spent in cells):
            found.append((sum((limit - spent for _m, limit, spent in cells), D(0)) / 3, key, name, cells))
    out = []
    for unused, key, name, cells in sorted(found, key=lambda t: -t[0])[:2]:
        fmt = ctx.fmt
        out.append(Insight(
            rule_id='budgets.limit_often_under', module=Module.BUDGETS, kind=Kind.OPPORTUNITY, tone=Tone.POSITIVE,
            priority=38, icon='piggy-bank',
            title=_('%(category)s stays under its limit') % {'category': fmt.short(name)},
            message=_('%(category)s finished well under its limit in each of the last 3 budget months, leaving about '
                      '%(unused)s unused each time. You might lower the limit, or share the room with another '
                      'category.') % {'category': name, 'unused': fmt.approx(unused)},
            evidence=_month_chips(ctx, name, cells),
            action=Action(_('Review the limit'), reverse('budget')),
            dedupe_key=f'under:{key[:50]}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'category': key},
        ))
    return out


@rule(id='budgets.room_to_share', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'budgets', 'budget_history'}, cooldown_days=7, min_history=3, sample=sampling.room_to_share_sample)
def room_to_share(ctx) -> list[Insight]:
    """The Budget page's "smart move", as an insight: one category at its limit while another has barely been used
    for three budget months. It is the same rule (budgets.budget_suggestions.pick_suggestion) and the same dismissals."""
    rows = _rows(ctx)
    if not budget_suggestions.may_have_suggestion(rows):
        return []
    history = ctx.budget_history
    suggestion = budget_suggestions.pick_suggestion(rows, ctx.key, history.limits, history.spend)
    if suggestion is None or suggestion['key'] in ctx.dismissed_suggestions:
        return []
    fmt = ctx.fmt
    donor, needy = suggestion['donor_name'], suggestion['needy_name']
    return [Insight(
        rule_id='budgets.room_to_share', module=Module.BUDGETS, kind=Kind.OPPORTUNITY, tone=Tone.CALM,
        priority=50, icon='arrow-left-right',
        title=_('%(category)s has room to spare') % {'category': fmt.short(donor)},
        message=_('%(donor)s has barely used its limit for 3 budget months, while %(needy)s has reached its own limit. '
                  'You could move about %(amount)s across for now, or leave things as they are.') % {
            'donor': donor, 'needy': needy, 'amount': fmt.approx(suggestion['amount'])},
        evidence=(Evidence(_('Room in %(category)s') % {'category': fmt.short(donor, words=3, chars=28)},
                           fmt.money(suggestion['donor_untouched'])),
                  Evidence(_('Limit of %(category)s') % {'category': fmt.short(needy, words=3, chars=28)},
                           fmt.money(suggestion['needy_limit']))),
        action=Action(_('Review the budget'), reverse('budget')),
        dedupe_key=suggestion['key'], valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'donor': suggestion['donor_id'], 'needy': suggestion['needy_id'], 'amount': suggestion['amount']},
    )]
