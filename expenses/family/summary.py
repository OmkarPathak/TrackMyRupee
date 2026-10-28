"""The narrow door: the only code that reads one member's data on behalf of another.

``shared_items`` returns plain, immutable values with a fixed set of fields. It never returns a model instance,
so no view or template can reach a transaction, a note, an account number or anything that was not shared. Amounts
come from the same valuation code as the owner's own net worth, so a shared number never disagrees with it.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _

from ..ledger_read_service import LedgerReadService
from ..models import FamilyShare, LoanScheduleInstallment, NetWorthSnapshot
from . import catalog
from .presenters import avatar_tone, display_name, initials

_FREQUENCY_MONTHS = {'ANNUAL': 12, 'SEMI_ANNUAL': 6, 'QUARTERLY': 3, 'MONTHLY': 1}
_PAYMENTS_A_YEAR = {'ANNUAL': 1, 'SEMI_ANNUAL': 2, 'QUARTERLY': 4, 'MONTHLY': 12}


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
    type_code: str
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
    premium_yearly: Decimal | None = None  # insurance: what the premiums add up to in a year
    next_due: date | None = None         # insurance
    covers: str = ''                     # insurance: who it covers
    emi: Decimal | None = None           # loan
    value: Decimal | None = None         # what it adds to (or takes from) the family net worth; None if list only
    asset_class: str = ''                # the "What the family owns" group of an asset


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
        section=catalog.section_of(kind), type_label=catalog.type_label(account), type_code=account.account_type,
        name=name, show_amounts=show_amounts, currency=currency)
    if not show_amounts:
        return SharedItem(**base)
    extra = {}
    if kind in (catalog.CARD, catalog.LOAN):
        value = -abs(value)          # what is owed is negative, whichever way the valuation signs it
    extra['value'] = value
    extra['asset_class'] = catalog.asset_class_of(account.account_type) if value > 0 else ''
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
                premium_yearly=(policy.premium_amount * _PAYMENTS_A_YEAR[policy.premium_frequency]
                                if policy.premium_amount and policy.premium_frequency in _PAYMENTS_A_YEAR else None),
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


# ---- The whole family -------------------------------------------------------------------------------------------

ZERO = Decimal('0.00')
UPCOMING_DAYS = 30


@dataclass(frozen=True)
class MemberSummary:
    user_id: int
    name: str
    initials: str
    tone: str
    items: tuple
    owns: Decimal
    owes: Decimal
    counted: bool                         # False when their amounts are in another currency than the viewer's

    @property
    def net(self):
        return self.owns - self.owes


@dataclass(frozen=True)
class Upcoming:
    when: date
    kind: str                             # 'bill' or 'premium'
    name: str
    owner_name: str
    owner_initials: str
    owner_tone: str
    amount: Decimal | None = None


@dataclass(frozen=True)
class FamilySummary:
    currency: str
    owns: Decimal
    owes: Decimal
    members: tuple
    items: tuple
    allocation: tuple                     # ((key, label, amount, percent), ...), largest first
    history: tuple                        # ((month_end_date, net worth), ...) then today's point last
    since: date | None                    # the day the first counted item was shared
    counts: tuple                         # ((key, heading, {user_id: count}, total), ...)
    upcoming: tuple
    listed_only: int                      # shared without amounts, so not counted
    other_currency: int                   # shared with amounts in another currency, so not counted

    @property
    def net_worth(self):
        return self.owns - self.owes

    @property
    def counted_items(self):
        return sum(1 for i in self.items if i.value is not None)


def _signed(kind, value):
    return -abs(value) if kind in (catalog.CARD, catalog.LOAN) else value


def _month_ends(first, last):
    """Last days of every month from ``first``'s month up to the month before ``last``'s."""
    out, year, month = [], first.year, first.month
    while (year, month) < (last.year, last.month):
        out.append(date(year, month, calendar.monthrange(year, month)[1]))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _history(shares_by_owner, kinds, currency_of_owner, viewer_currency, today):
    """Month-end net worth of what is shared, counting each item only from the day it was shared.

    Only the stored month-end snapshots are used, never the daily ones, because a balance dropping on a given
    day can reveal a purchase. The snapshot totals are never used either: they include private accounts."""
    counted = [s for owner, rows in shares_by_owner.items() if currency_of_owner[owner] == viewer_currency
               for s in rows if s.level == FamilyShare.AMOUNTS]
    if not counted:
        return ()
    first = min(s.created_at.date() for s in counted)
    ends = _month_ends(first, today)
    if not ends:
        return ()
    owners = {s.owner_id for s in counted}
    snapshots = {}
    for snap in (NetWorthSnapshot.objects.filter(user_id__in=owners, as_of_date__gte=ends[0].replace(day=1),
                                                  as_of_date__lte=ends[-1])
                 .order_by('as_of_date').values('user_id', 'as_of_date', 'breakdown')):
        snapshots[(snap['user_id'], snap['as_of_date'].year, snap['as_of_date'].month)] = snap   # the latest of the month
    points = []
    for end in ends:
        total, seen = ZERO, False
        for share in counted:
            if share.created_at.date() > end:
                continue
            snap = snapshots.get((share.owner_id, end.year, end.month))
            raw = snap['breakdown'].get(str(share.account_id)) if snap else None
            if raw is None:
                continue
            seen = True
            total += _signed(kinds[share.account_id], Decimal(str(raw)))
        if seen:
            points.append((end, total))
    return tuple(points)


def _build_family(household, viewer):
    today = timezone.localdate()
    viewer_currency = viewer.profile.currency
    memberships = list(household.members.select_related('user', 'user__profile'))
    shares_all = list(FamilyShare.objects.filter(household=household, account__is_active=True)
                      .select_related('account'))
    shares_by_owner = {}
    for share in shares_all:
        shares_by_owner.setdefault(share.owner_id, []).append(share)
    kinds = {s.account_id: catalog.kind_of(s.account.account_type) for s in shares_all}

    members, all_items, upcoming = [], [], []
    currency_of_owner = {}
    other_currency = listed_only = 0
    for position, membership in enumerate(memberships):
        owner = membership.user
        currency_of_owner[owner.pk] = owner.profile.currency
        items = shared_items(owner, household)
        tone, inits = avatar_tone(position), initials(owner)
        same_currency = owner.profile.currency == viewer_currency
        owns = owes = ZERO
        for item in items:
            all_items.append(item)
            if not item.show_amounts:
                listed_only += 1
                continue
            if not same_currency:
                other_currency += 1
                continue
            if item.value > 0:
                owns += item.value
            else:
                owes += -item.value
            if item.next_bill and item.next_bill - today <= timedelta(days=UPCOMING_DAYS):
                upcoming.append(Upcoming(item.next_bill, 'bill', item.name, item.owner_name, inits, tone))
            if item.next_due and item.next_due - today <= timedelta(days=UPCOMING_DAYS):
                upcoming.append(Upcoming(item.next_due, 'premium', item.name, item.owner_name, inits, tone, item.premium))
        members.append(MemberSummary(
            user_id=owner.pk, name=display_name(owner), initials=inits, tone=tone, items=tuple(items),
            owns=owns, owes=owes, counted=same_currency))

    owns = sum((m.owns for m in members), ZERO)
    owes = sum((m.owes for m in members), ZERO)
    by_class = {}
    for item in all_items:
        if item.asset_class and item.currency == viewer_currency:
            by_class[item.asset_class] = by_class.get(item.asset_class, ZERO) + item.value
    total_assets = sum(by_class.values(), ZERO)
    allocation = tuple(
        (key, label, by_class[key], int(round(by_class[key] / total_assets * 100)) if total_assets else 0)
        for key, label in catalog.ASSET_CLASSES if by_class.get(key))
    allocation = tuple(sorted(allocation, key=lambda row: -row[2]))

    counts = []
    for key, heading, row_kinds in catalog.HOLDINGS_ROWS:
        per_member = {m.user_id: sum(1 for i in m.items if i.kind in row_kinds) for m in members}
        total = sum(per_member.values())
        if total:
            counts.append((key, heading, per_member, total))

    history = _history(shares_by_owner, kinds, currency_of_owner, viewer_currency, today)
    counted_shares = [s.created_at.date() for rows in shares_by_owner.values() for s in rows
                      if s.level == FamilyShare.AMOUNTS and currency_of_owner.get(s.owner_id) == viewer_currency]
    return FamilySummary(
        currency=viewer_currency, owns=owns, owes=owes, members=tuple(members), items=tuple(all_items),
        allocation=allocation, history=history, since=min(counted_shares) if counted_shares else None,
        counts=tuple(counts), upcoming=tuple(sorted(upcoming, key=lambda u: (u.when, u.name))),
        listed_only=listed_only, other_currency=other_currency)


def family_summary(household, viewer):
    """Everything the family pages show, for ``viewer``. Cached briefly per household version; every change to a
    share, a member or a plan bumps the version, so nothing unshared is ever read back from the cache."""
    ttl = getattr(settings, 'FAMILY_SUMMARY_CACHE_SECONDS', 60)
    key = f'family-summary:{household.pk}:{household.cache_version}:{viewer.profile.currency}:{timezone.localdate()}'
    if ttl:
        cached = cache.get(key)
        if cached is not None:
            return cached
    result = _build_family(household, viewer)
    if ttl:
        cache.set(key, result, ttl)
    return result
