"""
tests/test_performance_net_worth.py
=====================================
Performance tests for get_net_worth().

Key invariant: query count must be BOUNDED and INDEPENDENT of the number of
accounts, holdings, assets, and currencies.

These tests verify the O(1) query guarantee using assertNumQueries.
The total query count should be the same regardless of scale (20 or 200 accounts).
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, AssetValuation, Holding, PhysicalAsset, Valuation


def _make_user(username):
    user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@test.com'})
    try:
        user.profile.currency = '₹'
        user.profile.save(update_fields=['currency'])
    except Exception:
        pass
    return user


def _make_account(user, name, account_type='SAVINGS_ACCOUNT', balance=Decimal('10000.00'), **kw):
    acc = Account.objects.create(user=user, name=name, account_type=account_type, balance=balance, currency='₹', **kw)
    LedgerPostingService.post_opening_balance(account=acc)
    return acc


def _add_holding(account, name='Fund', value=Decimal('5000.00')):
    h = Holding.objects.create(account=account, instrument_name=name, instrument_type='MF', currency='₹', is_active=True)
    Valuation.objects.create(holding=h, value=value, as_of_date=datetime.date.today())
    return h


def _add_physical_asset(user, name='Property'):
    asset = PhysicalAsset.objects.create(user=user, name=name, asset_class='REAL_ESTATE', acquisition_cost=Decimal('1000000.00'), currency='₹', is_active=True)
    AssetValuation.objects.create(asset=asset, value=Decimal('1000000.00'), as_of_date=datetime.date.today())
    return asset


# Exact query counts expected for get_net_worth call
MAX_QUERIES_EXTENDED = 7  # accounts + lines + opening + holdings + assets + goals + unlinked_loans
MAX_QUERIES_SIMPLE = 5    # accounts + lines + opening + goals + legacy_loans


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestQueryCountSmallFixture(TestCase):
    """20 accounts + 50 holdings + 5 assets — query count must be ≤ MAX_QUERIES_EXTENDED."""

    def setUp(self):
        self.user = _make_user('perf_small_user')

        # 20 savings accounts
        for i in range(20):
            _make_account(self.user, f'Savings {i}', account_type='SAVINGS_ACCOUNT')

        # 10 mutual fund accounts × 5 holdings each = 50 holdings
        for i in range(10):
            acc = _make_account(self.user, f'MF Account {i}', account_type='MUTUAL_FUND')
            for j in range(5):
                _add_holding(acc, name=f'Fund {i}-{j}')

        # 5 real estate accounts with linked assets
        for i in range(5):
            asset = _add_physical_asset(self.user, name=f'Property {i}')
            _make_account(self.user, f'RE Account {i}', account_type='REAL_ESTATE', linked_physical_asset=asset)

    def test_query_count_bounded(self):
        from expenses.ledger_read_service import LedgerReadService

        with self.assertNumQueries(MAX_QUERIES_EXTENDED):
            LedgerReadService.get_net_worth(self.user)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestQueryCountLargeFixture(TestCase):
    """200 accounts + 1000 holdings + 50 assets — same query count as small fixture."""

    def setUp(self):
        self.user = _make_user('perf_large_user')

        # 200 savings accounts (all types)
        for i in range(100):
            _make_account(self.user, f'Savings {i}', account_type='SAVINGS_ACCOUNT')
        for i in range(100):
            _make_account(self.user, f'FD {i}', account_type='FD')

        # 50 mutual fund accounts × 20 holdings each = 1000 holdings
        for i in range(50):
            acc = _make_account(self.user, f'MF {i}', account_type='MUTUAL_FUND')
            for j in range(20):
                _add_holding(acc, name=f'F{i}-{j}')

        # 50 real estate accounts with linked assets
        for i in range(50):
            asset = _add_physical_asset(self.user, name=f'Prop {i}')
            _make_account(self.user, f'RE {i}', account_type='REAL_ESTATE', linked_physical_asset=asset)

    def test_query_count_same_as_small(self):
        """Query count must be identical to the small fixture — proves O(1) scaling."""
        from expenses.ledger_read_service import LedgerReadService

        with self.assertNumQueries(MAX_QUERIES_EXTENDED):
            LedgerReadService.get_net_worth(self.user)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=False,
)
class TestQueryCountFlagOff(TestCase):
    """Flag off: limited query set (no Holding/AssetValuation/Loan schedule queries)."""

    def setUp(self):
        self.user = _make_user('perf_flagoff_user')
        for i in range(10):
            _make_account(self.user, f'Account {i}', account_type='SAVINGS_ACCOUNT')

    def test_query_count_bounded_flag_off(self):
        from expenses.ledger_read_service import LedgerReadService

        with self.assertNumQueries(MAX_QUERIES_SIMPLE):
            LedgerReadService.get_net_worth(self.user)
