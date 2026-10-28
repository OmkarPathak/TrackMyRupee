"""The narrow door: the only code that reads one member's data on behalf of another.

``shared_items`` returns plain, immutable values with a fixed set of fields. It never returns a model instance,
so no view or template can reach a transaction, a note, an account number or anything that was not shared. Amounts
come from the same valuation code as the owner's own net worth, so a shared number never disagrees with it.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.utils import timezone
from django.utils.translation import gettext as _

from ..ledger_read_service import LedgerReadService
from ..models import FamilyShare, LoanScheduleInstallment
from . import catalog
from .presenters import display_name

_FREQUENCY_MONTHS = {'ANNUAL': 12, 'SEMI_ANNUAL': 6, 'QUARTERLY': 3, 'MONTHLY': 1}


def _frequency_label(code):
    return {
        'ANNUAL': _("a year"), 'SEMI_ANNUAL': _("every 6 months"), 'QUARTERLY': _("a quarter"), 'MONTHLY': _("a month"),
    }.get(code, '')


@dataclass(frozen=True)
class SharedItem:
    """What a family member sees of one shared account. Every field here is on the whitelist."""
    share_id: int
    owner_id: int
    owner_name: str
    kind: str
    section: str
    type_label: str
    name: str
    show_amounts: bool
    currency: str
    amount: Decimal | None = None        # balance, negative for what is owed (Show amounts only)
    limit: Decimal | None = None         # credit card
    available: Decimal | None = None     # credit card
    next_bill: date | None = None        # credit card
    cover: Decimal | None = None         # insurance
    premium: Decimal | None = None       # insurance
    premium_per: str = ''                # insurance: "a year"
    next_due: date | None = None         # insurance
    covers: str = ''                     # insurance: who it covers
    emi: Decimal | None = None           # loan


def next_premium_due(start, frequency, today=None):
    """The next premium date on or after today, from the policy start and how often it is paid."""
    months = _FREQUENCY_MONTHS.get(frequency)
    if not start or not months:
        return None
    today = today or timezone.localdate()
    due, step = start, 0
    while due < today and step < 1200:
        step += 1
        due = start + relativedelta(months=months * step)
    return due


def _next_emi(loan):
    if loan is None:
        return None
    installment = (LoanScheduleInstallment.objects.filter(loan=loan, is_paid=False)
                   .order_by('due_date').values_list('scheduled_principal', 'scheduled_interest').first())
    return (installment[0] + installment[1]) if installment else None


def _build(owner_name, currency, account, name, show_amounts, value, share_id=0):
    """One item from an account. ``value`` is the account's own valuation, used only when amounts are shown."""
    kind = catalog.kind_of(account.account_type)
    base = dict(
        share_id=share_id, owner_id=account.user_id, owner_name=owner_name, kind=kind,
        section=catalog.section_of(kind), type_label=catalog.type_label(account),
        name=name, show_amounts=show_amounts, currency=currency)
    if not show_amounts:
        return SharedItem(**base)
    extra = {}
    if kind in (catalog.CARD, catalog.LOAN):
        value = -abs(value)          # what is owed is negative, whichever way the valuation signs it
    if kind == catalog.CARD:
        used = -value
        extra.update(amount=value, next_bill=account.next_billing_date)
        if account.has_credit_limit:
            extra.update(limit=account.credit_limit, available=max(Decimal('0.00'), account.credit_limit - used))
    elif kind == catalog.INSURANCE:
        policy = account.linked_physical_asset
        if policy is not None:
            extra.update(
                cover=policy.sum_assured, premium=policy.premium_amount,
                premium_per=_frequency_label(policy.premium_frequency), covers=policy.covers,
                next_due=next_premium_due(policy.policy_start_date, policy.premium_frequency))
    elif kind == catalog.LOAN:
        extra.update(amount=value, emi=_next_emi(account.linked_loan))
    else:
        extra.update(amount=value)
    return SharedItem(**base, **extra)


def shared_items(owner, household):
    """The items ``owner`` shares with ``household``, in the order they were shared."""
    shares = list(FamilyShare.objects.filter(owner=owner, household=household, account__is_active=True)
                  .select_related('account', 'account__linked_physical_asset', 'account__linked_loan'))
    if not shares:
        return []
    visible_ids = [s.account_id for s in shares if s.level == FamilyShare.AMOUNTS]
    values = LedgerReadService.get_net_worth(owner, only_account_ids=visible_ids)[1] if visible_ids else {}
    owner_name, currency = display_name(owner), owner.profile.currency
    return [
        _build(owner_name, currency, s.account, s.alias or s.account.name, s.level == FamilyShare.AMOUNTS,
               values.get(s.account_id, Decimal('0.00')), share_id=s.pk)
        for s in shares]


def own_items(owner):
    """Every active account of ``owner`` in the same shape, with amounts, for ``owner``'s own "What I share" page.
    Only ever called with the signed-in user's own data."""
    accounts = list(owner.accounts.filter(is_active=True).select_related('linked_physical_asset', 'linked_loan')
                    .order_by('name', 'id'))
    values = LedgerReadService.get_net_worth(owner, only_account_ids=[a.pk for a in accounts])[1] if accounts else {}
    owner_name, currency = display_name(owner), owner.profile.currency
    return {a.pk: _build(owner_name, currency, a, a.name, True, values.get(a.pk, Decimal('0.00'))) for a in accounts}
