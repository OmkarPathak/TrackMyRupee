"""Recurring rules: payments due this week, what the recurring payments come to, an amount that changed, and an
entry that looks like it repeats."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from ...analytics.links import EXPENSE
from ...recurring_utils import calculate_recurring_equivalents
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone
from .expenses import _is_posting

D = Decimal
EVIDENCE_ROWS = 3
FIXED_TYPES = ('EXPENSE', 'INSURANCE_PREMIUM', 'LOAN')     # the recurring payments that are costs
PRICE_CHANGE = D('0.05')        # a posting this far from the one before it is a change in price
RECENT_POSTING = timedelta(days=45)
REPEATS = 3                     # the same entry this many times in three months looks recurring


def _amount(payment) -> Decimal:
    return D(str(payment.base_amount if payment.base_amount is not None else payment.amount))


@rule(id='recurring.bills_soon', module=Module.RECURRING, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recurring'}, cooldown_days=3, requires={'recurring'}, sample=sampling.default_sample)
def bills_soon(ctx) -> list[Insight]:
    """Active recurring payments (anything that is not income) due in the next seven days."""
    due = ctx.bills_due
    if not due:
        return []
    fmt, count = ctx.fmt, len(due)
    total = sum((_amount(p) for p in due), D(0))
    first = due[0]
    link = reverse('recurring-list')
    args = {'description': fmt.short(first.description, words=6, chars=40), 'n': fmt.number(count),
            'total': fmt.approx(total), 'date': fmt.day(first.next_due_date)}
    if count == 1:
        message = _('%(description)s, about %(total)s, is due on %(date)s. '
                    'It is scheduled, so there is nothing to do unless plans have changed.') % args
    else:
        message = _('%(n)s payments totalling about %(total)s are due in the next 7 days, the first on %(date)s. '
                    'They are scheduled, so there is nothing to do unless plans have changed.') % args
    return [Insight(
        rule_id='recurring.bills_soon', module=Module.RECURRING, kind=Kind.UPCOMING, tone=Tone.CALM,
        priority=62, icon='calendar-event',
        title=_('Bills coming up this week'), message=message,
        evidence=tuple(Evidence(fmt.short(p.description, words=6, chars=40), fmt.money(_amount(p)), link)
                       for p in due[:EVIDENCE_ROWS]),
        action=Action(_('View recurring payments'), link),
        dedupe_key=f'{first.next_due_date:%Y-%m-%d}', valid_until=due[-1].next_due_date,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'items': [[p.description.strip(), p.next_due_date.isoformat()] for p in due[:EVIDENCE_ROWS]]},
    )]


def _digest(description: str, amount) -> str:
    """A stable name for 'this description and amount' that does not put either into a key."""
    return hashlib.sha1(f'{description.strip().lower()}|{amount}'.encode()).hexdigest()[:12]


@rule(id='recurring.fixed_costs', module=Module.RECURRING, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recurring_all', 'facts'}, cooldown_days=30, min_history=1, min_plan=Plan.PLUS, requires={'recurring'},
      preview=lambda: _('A look at your fixed costs'), sample=sampling.default_sample)
def fixed_costs(ctx) -> list[Insight]:
    """What the active recurring payments come to in a month, and what share of income that is."""
    items = [rt for rt in ctx.recurring_all if rt.transaction_type in FIXED_TYPES]
    if len(items) < 2:
        return []
    monthly = sum((D(str(calculate_recurring_equivalents(rt.frequency, rt.base_amount or rt.amount)[0])) for rt in items), D(0))
    incomes = [i for i in (ctx.summary(k).income for k in ctx.keys_back(3)) if i > 0]
    if monthly <= 0 or not incomes:
        return []
    share = monthly / (sum(incomes, D(0)) / len(incomes)) * 100
    fmt, link = ctx.fmt, reverse('recurring-list')
    return [Insight(
        rule_id='recurring.fixed_costs', module=Module.RECURRING, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=34, icon='arrow-repeat',
        title=_('Your fixed costs each month'),
        message=_('Recurring payments come to about %(monthly)s a month, around %(share)s of your income. '
                  'Knowing this number can make the rest of the month easier to plan.') % {
            'monthly': fmt.approx(monthly), 'share': fmt.percent(share)},
        evidence=(Evidence(_('Each month'), fmt.money(monthly), link), Evidence(_('Share of income'), fmt.percent(share)),
                  Evidence(_('Payments'), fmt.number(len(items)))),
        action=Action(_('View recurring payments'), link),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'monthly': str(monthly.quantize(D('0.01'))), 'items': len(items), 'share': int(share)},
    )]


@rule(id='recurring.price_change', module=Module.RECURRING, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recurring_all', 'postings'}, cooldown_days=30, min_plan=Plan.PLUS, requires={'recurring'},
      preview=lambda: _('A recurring amount changed'), sample=sampling.price_change_sample)
def price_change(ctx) -> list[Insight]:
    """A recurring expense whose latest posting is 5% or more away from the one before it (the schedule keeps no
    history of edits, so what it actually cost each time is read from the entries it posted)."""
    out, seen = [], set()
    for rt in ctx.recurring_all:
        rows = ctx.postings.get(f'{rt.description} (Recurring)', []) if rt.transaction_type == 'EXPENSE' else []
        if len(rows) < 2 or ctx.today - rows[-1][0] > RECENT_POSTING or rt.description in seen:
            continue                    # two schedules with one name post to the same entries: say it once
        seen.add(rt.description)
        (_before_day, before), (day, now) = rows[-2], rows[-1]
        if before <= 0 or abs(now - before) / before < PRICE_CHANGE:
            continue
        up, fmt, link = now > before, ctx.fmt, reverse('recurring-list')
        args = {'name': fmt.short(rt.description, words=6, chars=40), 'before': fmt.approx(before), 'after': fmt.approx(now)}
        out.append(Insight(
            rule_id='recurring.price_change', module=Module.RECURRING, kind=Kind.OBSERVATION,
            tone=Tone.CALM if up else Tone.POSITIVE, priority=44, icon='tag',
            title=(_('%(name)s now costs a bit more') if up else _('%(name)s now costs a bit less')) % {
                'name': fmt.short(rt.description)},
            message=(_('%(name)s went up from %(before)s to %(after)s on the latest entry. '
                       'If you expected this, you can dismiss it.') if up else
                     _('%(name)s came down from %(before)s to %(after)s on the latest entry. '
                       'If you expected this, you can dismiss it.')) % args,
            evidence=(Evidence(_('Before'), fmt.money(before), link), Evidence(_('Now'), fmt.money(now))),
            action=Action(_('View recurring payments'), link),
            dedupe_key=f'price:{rt.uuid}:{day:%Y-%m-%d}', valid_until=day + RECENT_POSTING,
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'name': rt.description.strip().lower(), 'direction': 'up' if up else 'down'},
        ))
    return out[:2]


@rule(id='recurring.looks_recurring', module=Module.RECURRING, surfaces={Surface.MODULE},
      needs={'rows', 'recurring_all'}, cooldown_days=60, cost='heavy', sample=sampling.looks_recurring_sample)
def looks_recurring(ctx) -> list[Insight]:
    """The same entry (same description and amount) three or more times in the last three months, over at least two
    months, that is not already a recurring payment. The bell already carries this as a notification, so this is for
    the Recurring page only."""
    since = ctx.today - timedelta(days=90)
    known = {rt.description.strip().lower() for rt in ctx.recurring_all}
    groups = defaultdict(list)
    for row in ctx.expense_rows:
        text = row.description.strip().lower()
        if row.date < since or not text or _is_posting(row.description) or text in known or row.amount <= 0:
            continue
        groups[(text, row.amount)].append(row)
    found = [(len(rows), rows) for rows in groups.values()
             if len(rows) >= REPEATS and len({(r.date.year, r.date.month) for r in rows}) >= 2]
    out = []
    for count, rows in sorted(found, key=lambda t: -t[0])[:2]:
        first, fmt = rows[-1], ctx.fmt
        what = fmt.short(first.description, words=6, chars=40)
        link = ctx.links.transactions(since, ctx.today, types=(EXPENSE,), category=first.category)
        out.append(Insight(
            rule_id='recurring.looks_recurring', module=Module.RECURRING, kind=Kind.OPPORTUNITY, tone=Tone.CALM,
            priority=36, icon='arrow-repeat',
            title=_('%(what)s looks like it repeats') % {'what': fmt.short(first.description)},
            message=_('The same %(amount)s entry for %(what)s appeared %(n)s times in the last 3 months. '
                      'You could make it recurring, or leave it as it is.') % {
                'amount': fmt.approx(first.amount), 'what': what, 'n': fmt.number(count)},
            evidence=(Evidence(_('Amount'), fmt.money(first.amount), link), Evidence(_('Times'), fmt.number(count))),
            action=Action(_('Make it recurring'), reverse('recurring-create')),
            dedupe_key=f'repeat:{_digest(first.description, first.amount)}:{ctx.key:%Y-%m}',
            valid_until=ctx.cycle[1], surfaces=frozenset({Surface.MODULE}),
            numbers={'what': first.description.strip().lower(), 'n': count},
        ))
    return out
