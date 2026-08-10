from decimal import Decimal
from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.models import Account, Holding, Valuation


class TestHoldingsStrategy(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_holdings_user', password='password')
        self.account = Account.objects.create(
            user=self.user,
            name='ICICI Direct Demat',
            account_type='DEMAT',
            balance=Decimal('1200.00'),  # uninvested cash sitting in ledger
            currency='₹',
        )

    def test_holdings_baseline_calculation(self):
        """Test baseline = Σ (units × avg_cost) across active holdings."""
        h1 = Holding.objects.create(
            account=self.account,
            instrument_name='TATA Motors',
            instrument_type='STOCK',
            units=Decimal('10.000000'),
            avg_cost=Decimal('500.00'),
            currency='₹',
            is_active=True,
        )
        h2 = Holding.objects.create(
            account=self.account,
            instrument_name='Nifty 50 Index Fund',
            instrument_type='MF',
            units=Decimal('100.000000'),
            avg_cost=Decimal('150.00'),
            currency='₹',
            is_active=True,
        )

        # Baseline = (10*500) + (100*150) = 5000 + 15000 = 20000.00
        baseline = get_baseline(self.account)
        self.assertEqual(baseline, Decimal('20000.00'))

    def test_cost_basis_fallback_for_zero_valuations(self):
        """Test holding with zero Valuation rows falls back to units × avg_cost, not ₹0."""
        h1 = Holding.objects.create(
            account=self.account,
            instrument_name='Newly Purchased MF',
            instrument_type='MF',
            units=Decimal('50.000000'),
            avg_cost=Decimal('100.00'),
            currency='₹',
            is_active=True,
        )
        self.assertEqual(h1.valuations.count(), 0)

        # Current value (without ledger cash) for this holding fallback = 50 * 100 = 5000.00
        # Plus uninvested ledger balance (1200.00) = 6200.00
        current = get_current(self.account)
        self.assertEqual(current, Decimal('6200.00'))

    def test_latest_valuation_and_additive_cash_fix(self):
        """
        Test latest valuation per holding + uninvested ledger cash balance.
        SPEC §2.2 worked example:
        Fund A: 120.5 units @ 42.10, latest valuation 6,150
        Fund B: 300 units @ 18.00, no valuation (fallback 5,400)
        Uninvested ledger cash: 1,200
        Baseline = 5,073.05 + 5,400.00 = 10,473.05
        Current = 6,150 + 5,400 + 1,200 = 12,750.00
        """
        h_a = Holding.objects.create(
            account=self.account,
            instrument_name='Fund A',
            instrument_type='MF',
            units=Decimal('120.500000'),
            avg_cost=Decimal('42.10'),
            currency='₹',
            is_active=True,
        )
        Valuation.objects.create(
            holding=h_a,
            value=Decimal('6150.00'),
        )

        h_b = Holding.objects.create(
            account=self.account,
            instrument_name='Fund B',
            instrument_type='MF',
            units=Decimal('300.000000'),
            avg_cost=Decimal('18.00'),
            currency='₹',
            is_active=True,
        )

        baseline = get_baseline(self.account)
        current = get_current(self.account)

        self.assertEqual(baseline, Decimal('10473.05'))
        self.assertEqual(current, Decimal('12750.00'))

    def test_fully_redeemed_holding_excluded(self):
        """Test fully redeemed holding (units=0, is_active=False) is excluded from sums."""
        h1 = Holding.objects.create(
            account=self.account,
            instrument_name='Sold Holding',
            instrument_type='STOCK',
            units=Decimal('0.000000'),
            avg_cost=Decimal('500.00'),
            currency='₹',
            is_active=False,
        )
        baseline = get_baseline(self.account)
        current = get_current(self.account)

        # Only uninvested ledger cash balance remains in current (1200.00)
        self.assertEqual(baseline, Decimal('0.00'))
        self.assertEqual(current, Decimal('1200.00'))
