"""Expense rules: the person's own categories against their own recent past."""
from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext as _

from ...analytics.links import EXPENSE
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Surface, Tone

D = Decimal
ABOVE_USUAL = D('1.1')          # a category this far above its recent average is worth a mention


@rule(id='expenses.category_above_usual', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'facts'}, cooldown_days=14, min_history=1, sample=sampling.default_sample)
def category_above_usual(ctx) -> list[Insight]:
    """The biggest category of the cycle, against its average over up to three earlier cycles."""
    totals = ctx.summary(ctx.key).categories
    if not totals:
        return []
    top_key = max(totals, key=totals.get)
    top = totals[top_key]
    earlier = [v for v in (ctx.facts.category_series(*ctx.cycle_of(k), top_key) for k in ctx.keys_back(3)) if v > 0]
    if not earlier:
        return []
    average = sum(earlier, D(0)) / len(earlier)
    if not top > average * ABOVE_USUAL:
        return []
    pct = int((top - average) / average * 100)
    name = ctx.facts.names.get(top_key, top_key)
    fmt, (start, end) = ctx.fmt, ctx.cycle
    link = ctx.links.transactions(start, ctx.today, types=(EXPENSE,), category=name)
    return [Insight(
        rule_id='expenses.category_above_usual', module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM,
        priority=45, icon='bar-chart-line',
        title=_('%(category)s is a little above usual') % {'category': fmt.short(name)},
        message=_('%(category)s is about %(pct)s above your recent average so far, %(now)s against %(usual)s. '
                  'One higher stretch is common, especially around festivals and travel.') % {
            'category': name, 'pct': fmt.percent(pct), 'now': fmt.approx(top), 'usual': fmt.approx(average)},
        detail=_('If it is on purpose, you can dismiss this.'),
        evidence=(
            Evidence(_('This period'), fmt.money(top), link),
            Evidence(_('Recent average'), fmt.money(average)),
        ),
        action=Action(_('See the entries'), link),
        dedupe_key=f'{top_key[:60]}:{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'category': top_key, 'pct': pct},
    )]
