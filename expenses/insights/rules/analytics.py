"""The Analytics sentence: what the period came to, in one line. An inline rule: worked out per request from the
page the tab built, never stored. (The wording and numbers are the ones the tab always had.)"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils.translation import gettext as _

from ...analytics.money import percent
from .. import sampling
from ..registry import rule
from ..types import Insight, Kind, Module, Surface, Tone

D = Decimal


@rule(id='analytics.period_summary', module=Module.ANALYTICS, surfaces={Surface.ANALYTICS}, needs={'page'}, mode='inline',
      cooldown_days=0, sample=sampling.page_sample)
def period_summary(ctx) -> list[Insight]:
    """What was kept of the income in the period, and how that compares with the comparison period."""
    page = ctx.page
    now, before, money = page.summary, page.compare, page.money
    base = dict(rule_id='analytics.period_summary', module=Module.ANALYTICS, kind=Kind.OBSERVATION, tone=Tone.CALM,
                priority=50, icon='lightbulb', dedupe_key=f'{page.period.range[0]}:{page.period.range[1]}',
                valid_until=page.period.range[1] + timedelta(days=1), surfaces=frozenset({Surface.ANALYTICS}))
    if not now.income:
        return [Insight(title=_('No income in this period'), message=_('No income was recorded in this period.'), **base)]
    text = _('You kept %(saved)s of %(income)s (%(rate)s).') % {
        'saved': money.full(now.saved), 'income': money.full(now.income), 'rate': percent(now.rate)}
    if before is not None and before.income:
        text += ' ' + (_('That is ahead of the comparison period (%(rate)s).') if now.rate >= before.rate
                       else _('That is behind the comparison period (%(rate)s).')) % {'rate': percent(before.rate)}
    return [Insight(title=_('What you kept this period'), message=text, **base)]
