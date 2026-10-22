"""Capital events: how much a one-off cost moved this cycle's savings rate."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..registry import rule
from ..types import Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
MIN_POINTS = D(3)               # percentage points the one-offs must move the savings rate before they are mentioned


@rule(id='capital_events.rate_effect', module=Module.CAPITAL_EVENTS,
      surfaces={Surface.MODULE, Surface.ANALYTICS, Surface.DASHBOARD}, needs={'facts'}, cooldown_days=30, min_history=1,
      min_plan=Plan.PLUS, preview=lambda: _('A one-off cost shaped this cycle'), sample=sampling.rate_effect_sample)
def rate_effect(ctx) -> list[Insight]:
    """The savings rate without the one-off capital events (how the app counts it by default) and with them counted."""
    without = ctx.summary(ctx.key)
    counted = ctx.facts.with_capital_events(True).summarize(*ctx.cycle)
    extra = counted.capital_extra
    if extra <= 0 or without.income <= 0 or without.rate - counted.rate < MIN_POINTS:
        return []
    biggest, amount = max(((key, counted.categories.get(key, D(0)) - without.categories.get(key, D(0)))
                           for key in counted.categories), key=lambda t: t[1])
    name = ctx.facts.names.get(biggest, biggest)
    fmt, link = ctx.fmt, reverse('capital-event-list')
    return [Insight(
        rule_id='capital_events.rate_effect', module=Module.CAPITAL_EVENTS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=38, icon='house-gear', title=_('A one-off cost shaped this cycle'),
        message=_('Your savings rate is %(rate)s, or %(with)s with %(name)s counted. '
                  'One-off costs are normal and are kept out of averages by default.') % {
            'rate': fmt.percent(without.rate), 'with': fmt.percent(counted.rate), 'name': f'{name} ({fmt.approx(amount)})'},
        evidence=(Evidence(_('One-off costs'), fmt.money(extra), link),),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.MODULE, Surface.ANALYTICS, Surface.DASHBOARD}),
        numbers={'rate': round(float(without.rate)), 'with': round(float(counted.rate))},
    )]
