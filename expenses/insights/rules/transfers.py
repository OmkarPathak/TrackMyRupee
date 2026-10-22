"""Transfers and investing: the share of income that went into investments, against the person's own usual."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..registry import rule
from ..types import Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
MIN_POINTS = 5                  # percentage points away from their usual before it is worth a mention
MIN_CYCLE_SHARE = D('0.5')      # and at least half the cycle gone, so a payday that came before the SIPs does not mislead


@rule(id='transfers.invested_share', module=Module.TRANSFERS,
      surfaces={Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW}, needs={'facts'},
      cooldown_days=30, min_history=3, min_plan=Plan.PLUS, preview=lambda: _('Share of income you invest'),
      sample=sampling.invested_share_sample)
def invested_share(ctx) -> list[Insight]:
    """Invested money as a share of income this cycle, against the average of the person's three earlier cycles."""
    if D(ctx.elapsed_days) / ctx.cycle_days < MIN_CYCLE_SHARE:
        return []
    now = ctx.summary(ctx.key)
    shares = [s.invested / s.income * 100 for s in (ctx.summary(k) for k in ctx.keys_back(3)) if s.income > 0]
    if now.income <= 0 or now.invested <= 0 or not shares:
        return []
    usual, share = sum(shares, D(0)) / len(shares), now.invested / now.income * 100
    if abs(share - usual) < MIN_POINTS:
        return []
    fmt, link = ctx.fmt, reverse('transfer-list')
    return [Insight(
        rule_id='transfers.invested_share', module=Module.TRANSFERS, kind=Kind.PATTERN, tone=Tone.CALM, priority=30,
        icon='piggy-bank', title=_('Share of income you invest'),
        message=_('About %(share)s of income went into investments this cycle, against your usual %(usual)s.') % {
            'share': fmt.percent(share), 'usual': fmt.percent(usual)},
        evidence=(Evidence(_('Invested'), fmt.money(now.invested), link), Evidence(_('Income'), fmt.money(now.income))),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.MODULE, Surface.ANALYTICS, Surface.MONTHLY, Surface.YEAR_IN_REVIEW}),
        numbers={'share': round(float(share)), 'usual': round(float(usual))},
    )]
