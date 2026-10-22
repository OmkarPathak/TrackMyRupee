"""Account rules: deposits maturing, and (later) card billing, idle cash and balances worth a check."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..format import Fmt
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Surface, Tone

D = Decimal


@rule(id='accounts.deposit_maturing', module=Module.ACCOUNTS,
      surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}, needs={'accounts'},
      cooldown_days=30, push=True, sample=sampling.default_sample)
def deposit_maturing(ctx) -> list[Insight]:
    """An active fixed or recurring deposit that matures within the next 30 days."""
    out = []
    for account in ctx.maturing:
        own = Fmt(account.currency)            # the deposit's own balance, in the currency it is held in
        link = reverse('account-detail', args=[account.uuid])
        out.append(Insight(
            rule_id='accounts.deposit_maturing', module=Module.ACCOUNTS, kind=Kind.UPCOMING, tone=Tone.CALM,
            priority=60, icon='hourglass-split',
            title=_('A deposit matures soon'),
            message=_('%(name)s matures on %(date)s, with about %(amount)s coming back to you. '
                      'You might decide where it goes next, or leave it for now.') % {
                'name': account.name, 'date': ctx.fmt.day_year(account.deposit_maturity_date),
                'amount': own.approx(account.balance)},
            evidence=(
                Evidence(_('Matures on'), ctx.fmt.day_year(account.deposit_maturity_date), link),
                Evidence(_('Balance'), own.money(account.balance)),
            ),
            action=Action(_('View account'), link),
            dedupe_key=f'{account.uuid}:{account.deposit_maturity_date:%Y-%m-%d}',
            valid_until=account.deposit_maturity_date,
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}),
            numbers={'name': account.name.strip().lower(), 'balance': str(account.balance)},
        ))
    return out
