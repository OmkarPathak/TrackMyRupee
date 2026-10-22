"""Holdings: a SIP that has not been recorded, and a plain picture of where invested money sits. Descriptive only:
no rule here says what a portfolio should look like."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from ...account_types import investment_codes
from ...calendar_data import next_unposted
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
SIP_LATE_DAYS = 3               # due this long ago and still not recorded
SIP_WINDOW_DAYS = 30            # and mentioned for a month at most
HOLDING_SHARE = D(40)           # one holding at this share of invested money
TYPE_SHARE = D(70)              # one kind of holding at this share
MIN_HOLDINGS = 3                # fewer than this and the shares say nothing new


def _type_name(code: str) -> str:
    names = {
        'MF': _('Mutual funds'), 'STOCK': _('Stocks'), 'NPS': 'NPS', 'PPF': 'PPF', 'EPF': 'EPF',     # acronyms read the same everywhere
        'RD': _('Recurring deposits'),
    }
    return names.get(code) or _('Other holdings')


@rule(id='holdings.sip_missed', module=Module.HOLDINGS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recurring_all'}, cooldown_days=7, min_plan=Plan.PLUS, requires={'recurring'},
      preview=lambda: _('A SIP has not been recorded'), sample=sampling.sip_missed_sample)
def sip_missed(ctx) -> list[Insight]:
    """A recurring transfer into an investment account that was due a few days ago and has not been posted. Posting
    runs on its own, so a due date that is still open means the instalment did not go through the app: the same
    "pending" the Calendar shows."""
    invest, found = set(investment_codes()), []
    for rt in ctx.recurring_all:
        if rt.transaction_type != 'TRANSFER' or rt.to_account is None or rt.to_account.account_type not in invest:
            continue
        if not rt.to_account.is_active:
            continue
        due = next_unposted(rt)
        if due is None or (rt.end_date and due > rt.end_date):
            continue
        late = (ctx.today - due).days
        if SIP_LATE_DAYS <= late <= SIP_WINDOW_DAYS:
            found.append((due, rt))
    if not found:
        return []
    due, rt = min(found, key=lambda t: t[0])
    link = reverse('transfer-create')
    amount = D(str(rt.base_amount or rt.amount))
    return [Insight(
        rule_id='holdings.sip_missed', module=Module.HOLDINGS, kind=Kind.UPCOMING, tone=Tone.GENTLE_ATTENTION,
        priority=52, icon='arrow-repeat', title=_('A SIP has not been recorded'),
        message=_('Your %(amount)s SIP (%(name)s) was due on %(day)s and is not recorded yet. '
                  'If it went through, you can add it.') % {
            'amount': ctx.fmt.approx(amount), 'name': rt.description, 'day': ctx.fmt.day(due)},
        evidence=(Evidence(_('Due on'), ctx.fmt.day_year(due), reverse('calendar')),),
        action=Action(_('Add the transfer'), link),
        dedupe_key=f'{rt.uuid}:{due.isoformat()}', valid_until=due + timedelta(days=SIP_WINDOW_DAYS),
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}), numbers={'due': due.isoformat()},
    )]


@rule(id='holdings.concentration', module=Module.HOLDINGS, surfaces={Surface.MODULE}, needs={'holdings'},
      cooldown_days=90, min_plan=Plan.PLUS, cost='heavy', requires={'holdings'},
      preview=lambda: _('Where your invested money sits'), sample=sampling.concentration_sample)
def concentration(ctx) -> list[Insight]:
    """One holding at 40% or more of invested value, or one kind of holding at 70% or more. A picture, not advice."""
    held = [h for h in ctx.holdings if h.value > 0]
    total = sum((h.value for h in held), D(0))
    if len(held) < MIN_HOLDINGS or total <= 0:
        return []
    top = max(held, key=lambda h: h.value)
    by_type: dict[str, Decimal] = {}
    for h in held:
        by_type[h.holding.instrument_type] = by_type.get(h.holding.instrument_type, D(0)) + h.value
    kind, kind_value = max(by_type.items(), key=lambda t: t[1])
    one_share, kind_share = top.value / total * 100, kind_value / total * 100
    heavy_one, heavy_kind = one_share >= HOLDING_SHARE, kind_share >= TYPE_SHARE
    if not (heavy_one or heavy_kind):
        return []
    fmt, link = ctx.fmt, reverse('holding-list')
    args = {'kind': _type_name(kind), 'kind_share': fmt.percent(kind_share), 'name': top.holding.instrument_name,
            'share': fmt.percent(one_share)}
    if heavy_one and heavy_kind:
        message = _('%(kind)s are about %(kind_share)s of your invested money and %(name)s about %(share)s. '
                    'This is just a picture of your mix.') % args
    elif heavy_kind:
        message = _('%(kind)s are about %(kind_share)s of your invested money. This is just a picture of your mix.') % args
    else:
        message = _('%(name)s is about %(share)s of your invested money. This is just a picture of your mix.') % args
    return [Insight(
        rule_id='holdings.concentration', module=Module.HOLDINGS, kind=Kind.OBSERVATION, tone=Tone.CALM, priority=22,
        icon='pie-chart', title=_('Where your invested money sits'), message=message,
        evidence=(Evidence(_('Invested value'), fmt.money(total), link),),
        dedupe_key=f'{ctx.today.year}:{(ctx.today.month - 1) // 3}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.MODULE}), numbers={'one': round(float(one_share)), 'kind': round(float(kind_share))},
    )]
