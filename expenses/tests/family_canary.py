"""Leak-test harness for family sharing ("canaries").

A canary is a unique marker string planted in data that a family member must never see: a transaction's
description, a category, a note, a private account's name, a policy number. Every family page, JSON reply,
email, notification and cache entry is then searched for the markers. One marker in any of them is a leak.

    world = CanaryWorld.seed(owner)          # plants the markers in the owner's data
    ...                                       # a member opens family pages
    world.assert_clean(response, *mail.outbox)

Phase 0 ships the harness and tests it on its own. From Phase 1 the family tests plant a world for the owner
and run it against every page that another household member can open.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from expenses.models import (
    Account,
    Expense,
    Income,
    PhysicalAsset,
)

# Each marker is shouted, hyphenated and unlikely to occur in any page by accident.
MARKERS = {
    'transaction': 'CANARY-TXN-DESCRIPTION-7Q3F',
    'category': 'CANARY-CATEGORY-2K9X',
    'income': 'CANARY-INCOME-NOTE-5M1B',
    'private_account': 'CANARY-PRIVATE-ACCOUNT-8H4D',
    'policy_number': 'CANARY-POLICY-NO-3Z6T',
    'second_transaction': 'CANARY-TXN-DESCRIPTION-9W2L',
}


def _text_of(item):
    """The searchable text of a response, an email, a notification, a string or a plain structure."""
    if item is None:
        return ''
    if isinstance(item, str):
        return item
    if isinstance(item, bytes):
        return item.decode('utf-8', 'replace')
    if hasattr(item, 'content') and hasattr(item, 'status_code'):          # a Django response
        parts = [item.content.decode('utf-8', 'replace')]
        parts.extend(f'{k}: {v}' for k, v in item.headers.items())
        parts.extend(f'{name}={morsel.value}' for name, morsel in item.cookies.items())
        return '\n'.join(parts)
    if hasattr(item, 'subject') and hasattr(item, 'body'):                  # an outbound email
        alternatives = ''.join(str(a[0]) for a in getattr(item, 'alternatives', []))
        return f'{item.subject}\n{item.body}\n{alternatives}'
    if hasattr(item, 'title') and hasattr(item, 'message'):                 # a Notification row
        return f'{item.title}\n{item.message}\n{getattr(item, "link", "")}\n{getattr(item, "metadata", "")}'
    if isinstance(item, dict):
        return '\n'.join(f'{_text_of(k)} {_text_of(v)}' for k, v in item.items())
    if isinstance(item, (list, tuple, set)):
        return '\n'.join(_text_of(i) for i in item)
    return str(item)


@dataclass
class CanaryWorld:
    owner: object
    markers: dict = field(default_factory=lambda: dict(MARKERS))
    private_account: Account | None = None
    bank_account: Account | None = None
    card_account: Account | None = None
    insurance_account: Account | None = None
    policy: PhysicalAsset | None = None

    @classmethod
    def seed(cls, owner, *, currency='₹'):
        """Plant the markers in ``owner``'s data and return the world."""
        world = cls(owner=owner)
        m = world.markers
        today = date.today()

        world.private_account = Account.objects.create(
            user=owner, name=m['private_account'], account_type='SAVINGS_ACCOUNT',
            balance=Decimal('11111.00'), currency=currency)
        world.bank_account = Account.objects.create(
            user=owner, name='Joint Savings', account_type='SAVINGS_ACCOUNT',
            balance=Decimal('50000.00'), currency=currency)
        world.card_account = Account.objects.create(
            user=owner, name='Everyday Card', account_type='CREDIT_CARD',
            balance=Decimal('-4000.00'), currency=currency, credit_limit=Decimal('100000.00'),
            credit_card_billing_day=18)
        world.policy = PhysicalAsset.objects.create(
            user=owner, name='Term Cover', asset_class='INSURANCE', currency=currency,
            policy_number=m['policy_number'], sum_assured=Decimal('5000000.00'),
            premium_amount=Decimal('12000.00'), premium_frequency='YEARLY', policy_type='PROTECTION')
        world.insurance_account = Account.objects.create(
            user=owner, name='Term Cover', account_type='LIFE_INSURANCE',
            balance=Decimal('0.00'), currency=currency, linked_physical_asset=world.policy)

        for account, key in ((world.bank_account, 'transaction'), (world.private_account, 'second_transaction')):
            Expense.objects.create(
                user=owner, account=account, date=today, amount=Decimal('250.00'), currency=currency,
                description=m[key], category=m['category'])
        Income.objects.create(
            user=owner, account=world.bank_account, date=today, amount=Decimal('900.00'), currency=currency,
            source='Salary', description=m['income'])
        return world

    # -- checking ---------------------------------------------------------------------------------

    def leaked(self, *items):
        """The marker names found anywhere in ``items`` (responses, emails, notifications, strings...)."""
        haystack = '\n'.join(_text_of(i) for i in items)
        return sorted(name for name, value in self.markers.items() if value in haystack)

    def assert_clean(self, *items, allow=()):
        """Fail, naming each marker, if any appears in ``items``. ``allow`` names markers that may appear."""
        found = [name for name in self.leaked(*items) if name not in allow]
        if found:
            raise AssertionError(
                'Private data leaked into something a family member can see: '
                + ', '.join(f'{name} ({self.markers[name]})' for name in found))
