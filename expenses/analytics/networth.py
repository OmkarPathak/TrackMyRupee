"""Net worth at the end of each step of a period, and what it is made of.

``LedgerReadService.get_net_worth`` already knows how to value every account on any past day; this module calls
it for a handful of dates (never more than ``MAX_POINTS``) and keeps the answers in the cache until something
the net worth depends on changes (the same signals that clear the other dashboard caches).
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from ..account_types import ACCOUNT_TYPES, is_liability
from ..ledger_read_service import LedgerReadService

MAX_POINTS = 12
CACHE_SECONDS = 600
ZERO = Decimal('0')

# (key, label, the app's account groups it is made of); anything unmatched but owed counts as a loan or card
GROUPS = (
    ('cash', _('Cash & bank'), ('Cash & Bank',)),
    ('fixed', _('Fixed income'), ('Fixed-Income',)),
    ('invest', _('Investments'), ('Investments',)),
    ('property', _('Property & insurance'), ('Physical Assets', 'Insurance')),
    ('debt', _('Loans & cards'), ('Short-Term Credit', 'Long-Term Loans')),
)
# the old catch-all account types
_LEGACY = {'CASH': 'cash', 'BANK': 'cash', 'INVESTMENT': 'invest', 'FIXED_DEPOSIT': 'fixed', 'OTHER': 'property'}
OTHER_KEY = 'other'                                           # savings-goal reserves and loans not tied to an account

_GROUP_OF: dict[str, str] = {}
for _key, _title, _names in GROUPS:
    for _group, _choices in ACCOUNT_TYPES:
        if _group in _names:
            for _code, _display in _choices:
                _GROUP_OF.setdefault(_code, _key)
_GROUP_OF.update(_LEGACY)


def group_of(account_type: str) -> str:
    return _GROUP_OF.get(account_type) or ('debt' if is_liability(account_type) else 'property')


def group_labels() -> dict[str, str]:
    labels = {key: str(title) for key, title, _names in GROUPS}
    labels[OTHER_KEY] = str(_('Other'))
    return labels


@dataclass(frozen=True)
class NetWorthPoint:
    day: date
    total: Decimal
    groups: dict[str, Decimal]
    held: dict[str, Decimal]                  # the same, counting only accounts with a positive balance

    @property
    def idle(self) -> Decimal:
        """Money sitting in bank accounts: only what you hold, so an overdrawn account does not shrink it."""
        return self.held.get('cash', ZERO)

    @property
    def growing(self) -> Decimal:
        return self.held.get('invest', ZERO) + self.held.get('fixed', ZERO)


def _cache_key(user_id: int) -> str:
    return f'analytics_nw_points_{user_id}'


def _point(user, accounts, as_of: date, today: date) -> NetWorthPoint:
    if as_of >= today:
        total, balances = LedgerReadService.get_net_worth(user)
        as_of = today
    else:
        total, balances = LedgerReadService.get_net_worth(user, as_of=as_of)
    groups: dict[str, Decimal] = {}
    held: dict[str, Decimal] = {}
    for account in accounts:
        value = Decimal(str(balances.get(account.pk, ZERO)))
        liability = is_liability(account.account_type)
        if liability:
            value = -abs(value)                                # what is owed always reduces net worth
        key = group_of(account.account_type)
        groups[key] = groups.get(key, ZERO) + value
        if not liability and value > 0:
            held[key] = held.get(key, ZERO) + value
    groups[OTHER_KEY] = Decimal(str(total)) - sum(groups.values(), ZERO)
    return NetWorthPoint(day=as_of, total=Decimal(str(total)), groups=groups, held=held)


def net_worth_points(user, days: Iterable[date], today: date) -> list[NetWorthPoint]:
    """The net worth on each of ``days`` (the last may be today), computing only the ones not already cached."""
    days = list(days)
    stored: dict[str, dict] = cache.get(_cache_key(user.id)) or {}
    accounts = None
    out: list[NetWorthPoint] = []
    for day in days:
        key = min(day, today).isoformat()
        hit = stored.get(key)
        if hit is None:
            if accounts is None:
                accounts = list(user.accounts.filter(is_active=True))
            point = _point(user, accounts, day, today)
            hit = {'total': str(point.total), 'groups': {k: str(v) for k, v in point.groups.items()},
                   'held': {k: str(v) for k, v in point.held.items()}}
            stored[key] = hit
        out.append(NetWorthPoint(day=min(day, today), total=Decimal(hit['total']),
                                 groups={k: Decimal(v) for k, v in hit['groups'].items()},
                                 held={k: Decimal(v) for k, v in hit.get('held', {}).items()}))
    cache.set(_cache_key(user.id), stored, CACHE_SECONDS)
    return out
