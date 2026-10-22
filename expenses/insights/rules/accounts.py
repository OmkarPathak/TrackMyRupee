"""Account rules: deposits maturing, credit-card billing and use, cash held against the person's own cushion target,
and balances worth a check. Card insights are about debt, so they are marked sensitive (extra gentle, no share, never
pushed); the existing reminders for a card's billing day are unchanged."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from ...reconciliation import is_reconcilable
from ...utils import get_safe_date
from .. import sampling
from ..format import Fmt
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
BILL_WINDOW_DAYS = 10           # the Accounts page's own "bill due soon" window
UTILISATION = D('50')           # a card using this share of its limit or more is mentioned
CUSHION_FACTOR = D('1.5')       # cash this far above the person's own target is mentioned
STALE_RECONCILIATION_DAYS = 60


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


def _next_billing(day: int, today):
    """The next billing date on or after today for a billing day of the month (short months use their last day)."""
    this = get_safe_date(today.year, today.month, day)
    if this >= today:
        return this
    return get_safe_date(today.year + (today.month == 12), today.month % 12 + 1, day)


@rule(id='accounts.card_billing_soon', module=Module.ACCOUNTS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'cards'}, cooldown_days=7, requires={'cards'}, sample=sampling.cards_sample)
def card_billing_soon(ctx) -> list[Insight]:
    """A card whose billing date is within ten days and that has something outstanding."""
    out = []
    for card, owed in ctx.cards:
        billing = _next_billing(card.credit_card_billing_day, ctx.today)
        if owed <= 0 or (billing - ctx.today).days > BILL_WINDOW_DAYS:
            continue
        own, link = Fmt(card.currency), reverse('account-detail', args=[card.uuid])
        args = {'card': card.name, 'date': ctx.fmt.day(billing), 'amount': own.approx(owed)}
        if card.has_credit_limit:
            args['pct'] = ctx.fmt.percent(owed / card.credit_limit * 100)
            message = _('%(card)s bills on %(date)s, with about %(amount)s outstanding, around %(pct)s of its limit. '
                        'A payment before then may lower what shows on the statement.') % args
        else:
            message = _('%(card)s bills on %(date)s, with about %(amount)s outstanding. '
                        'A payment before then may lower what shows on the statement.') % args
        out.append(Insight(
            rule_id='accounts.card_billing_soon', module=Module.ACCOUNTS, kind=Kind.UPCOMING, tone=Tone.CALM,
            priority=56, icon='credit-card', sensitive=True,
            title=_('A card billing date is near'), message=message,
            evidence=(Evidence(_('Outstanding'), own.money(owed), link), Evidence(_('Billing date'), ctx.fmt.day(billing))),
            action=Action(_('View card'), link),
            dedupe_key=f'{card.uuid}:{billing:%Y-%m-%d}', valid_until=billing,
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'card': card.name.strip().lower()},
        ))
    return out[:2]


@rule(id='accounts.card_utilisation', module=Module.ACCOUNTS, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'cards'}, cooldown_days=30, min_plan=Plan.PLUS, requires={'cards'}, sample=sampling.cards_sample)
def card_utilisation(ctx) -> list[Insight]:
    """The card using the largest share of a known limit, when that is half or more."""
    busy = [(owed / card.credit_limit * 100, card, owed) for card, owed in ctx.cards
            if card.has_credit_limit and owed > 0 and owed / card.credit_limit * 100 >= UTILISATION]
    if not busy:
        return []
    pct, card, owed = max(busy, key=lambda t: t[0])
    own, link = Fmt(card.currency), reverse('account-detail', args=[card.uuid])
    return [Insight(
        rule_id='accounts.card_utilisation', module=Module.ACCOUNTS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=33, icon='credit-card-2-front', sensitive=True,
        title=_('Card use is a little higher'),
        message=_('%(card)s is using about %(pct)s of its %(limit)s limit. '
                  'That is common after a big purchase, and it eases as the card is paid down.') % {
            'card': card.name, 'pct': ctx.fmt.percent(pct), 'limit': own.approx(card.credit_limit)},
        evidence=(Evidence(_('Used'), own.money(owed), link), Evidence(_('Limit'), own.money(card.credit_limit))),
        action=Action(_('View card'), link),
        dedupe_key=f'{card.uuid}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'card': card.name.strip().lower(), 'pct': int(pct)},
    )]


@rule(id='accounts.idle_cash', module=Module.ACCOUNTS, surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY},
      needs={'cash', 'facts'}, cooldown_days=60, min_history=2, min_plan=Plan.PLUS, cost='heavy',   # net worth is ~8 queries
      preview=lambda: _('A look at the cash you hold'), sample=sampling.idle_cash_sample)
def idle_cash(ctx) -> list[Insight]:
    """Cash and bank balances in months of the person's usual spending, when that is well above the cushion they
    chose for themselves (six months unless they said otherwise). A picture of their own numbers, not a target."""
    idle = ctx.cash.idle
    spends = [s for s in (ctx.summary(k).spending for k in ctx.keys_back(3)) if s > 0]
    if idle <= 0 or not spends:
        return []
    monthly = sum(spends, D(0)) / len(spends)
    months, target = idle / monthly, ctx.emergency_months
    if months < target * CUSHION_FACTOR:
        return []
    fmt, link = ctx.fmt, ctx.links.accounts()
    return [Insight(
        rule_id='accounts.idle_cash', module=Module.ACCOUNTS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=28, icon='safe',
        title=_('Cash set aside for emergencies'),
        message=_('About %(idle)s sits in your bank accounts, roughly %(months)s months of your spending, against '
                  'your %(target)s-month target. If that feels right, there is nothing to do.') % {
            'idle': fmt.approx(idle), 'months': fmt.number(round(months)), 'target': fmt.number(target)},
        evidence=(Evidence(_('Cash and bank'), fmt.money(idle), link), Evidence(_('Usual monthly spending'), fmt.money(monthly))),
        dedupe_key=f'{ctx.key:%Y-%m}', valid_until=ctx.cycle[1], surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.MONTHLY}),
        numbers={'months': round(months), 'target': target},
    )]


@rule(id='accounts.not_reconciled', module=Module.ACCOUNTS, surfaces={Surface.MODULE}, needs={'accounts_all', 'reconciliations'},
      cooldown_days=30, min_plan=Plan.PLUS, cost='heavy', preview=lambda: _('A balance could use a check'),
      sample=sampling.reconcile_sample)
def not_reconciled(ctx) -> list[Insight]:
    """An account the person has matched to a statement before, but not for two months."""
    lapsed = []
    for account in ctx.accounts_all:
        last = ctx.reconciliations.get(account.pk)
        if last and is_reconcilable(account) and (ctx.today - last).days >= STALE_RECONCILIATION_DAYS:
            lapsed.append((last, account))
    if not lapsed:
        return []
    last, account = min(lapsed, key=lambda t: t[0])
    link = reverse('account-reconcile', args=[account.uuid])
    return [Insight(
        rule_id='accounts.not_reconciled', module=Module.ACCOUNTS, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=24, icon='check2-square',
        title=_('A balance could use a check'),
        message=_('%(name)s was last matched to a statement %(days)s days ago. '
                  'A quick check when you have a moment keeps the balance trustworthy.') % {
            'name': account.name, 'days': ctx.fmt.number((ctx.today - last).days)},
        evidence=(Evidence(_('Last matched'), ctx.fmt.day_year(last), link),),
        action=Action(_('Check the balance'), link),
        dedupe_key=f'{account.uuid}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1], surfaces=frozenset({Surface.MODULE}),
        numbers={'name': account.name.strip().lower()},
    )]
