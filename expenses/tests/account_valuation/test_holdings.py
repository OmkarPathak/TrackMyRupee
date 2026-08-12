from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.account_valuation import get_baseline, get_current
from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Holding, Transfer, Valuation


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_READ_COHORT_PERCENT=100,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestHoldingsStrategy(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_holdings_user', password='password')
        self.bank_account = Account.objects.create(
            user=self.user,
            name='Bank Savings',
            account_type='CASH',
            balance=Decimal('200000.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=self.bank_account)

        self.account = Account.objects.create(
            user=self.user,
            name='ICICI Direct Demat',
            account_type='DEMAT',
            balance=Decimal('1200.00'),  # uninvested cash sitting in ledger
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=self.account)

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

        # Cost basis = 50 * 100 = 5000.00
        # Ledger balance = 1200.00
        # Net uninvested cash = max(0, 1200 - 5000) = 0.00
        # Current value = 5000.00
        current = get_current(self.account)
        self.assertEqual(current, Decimal('5000.00'))

    def test_latest_valuation_and_additive_cash_fix(self):
        """
        Test latest valuation per holding + uninvested ledger cash balance.
        Fund A: 120.5 units @ 42.10 (cost basis 5,073.05), latest valuation 6,150
        Fund B: 300 units @ 18.00 (cost basis 5,400.00), no valuation (fallback 5,400)
        Total cost basis = 10,473.05.
        Real transfer funded into account = 11,673.05 (10,473.05 cost basis + 1,200 uninvested cash)
        Uninvested ledger cash = max(0, 11,673.05 - 10,473.05) = 1,200.00
        Baseline = 5,073.05 + 5,400.00 = 10,473.05
        Current = 6,150 + 5,400 + 1,200 = 12,750.00
        """
        demat_acc = Account.objects.create(
            user=self.user,
            name='Zerodha Demat',
            account_type='DEMAT',
            balance=Decimal('0.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=demat_acc)

        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=demat_acc,
            amount=Decimal('11673.05'),
            date='2026-08-01',
            description='Fund investment',
        )

        h_a = Holding.objects.create(
            account=demat_acc,
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
            account=demat_acc,
            instrument_name='Fund B',
            instrument_type='MF',
            units=Decimal('300.000000'),
            avg_cost=Decimal('18.00'),
            currency='₹',
            is_active=True,
        )

        baseline = get_baseline(demat_acc)
        current = get_current(demat_acc)

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

        # Only uninvested ledger cash balance remains in current (1200.00 - 0 = 1200.00)
        self.assertEqual(baseline, Decimal('0.00'))
        self.assertEqual(current, Decimal('1200.00'))

    def test_realistic_lifecycle_no_double_counting(self):
        """
        Create an Account, post a real Transfer for ₹1,00,000 (ledger balance ₹1,00,000),
        create a Holding with cost basis ₹1,00,000. Assert get_current returns ₹1,00,000, NOT ₹2,00,000.
        """
        demat_acc = Account.objects.create(
            user=self.user,
            name='Groww Demat',
            account_type='DEMAT',
            balance=Decimal('0.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=demat_acc)

        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=demat_acc,
            amount=Decimal('100000.00'),
            date='2026-08-01',
            description='Fund investment',
        )

        h = Holding.objects.create(
            account=demat_acc,
            instrument_name='Nifty ETF',
            instrument_type='ETF',
            units=Decimal('100.000000'),
            avg_cost=Decimal('1000.00'),  # cost basis = 100,000
            currency='₹',
            is_active=True,
        )

        current_val = get_current(demat_acc)
        self.assertEqual(current_val, Decimal('100000.00'))

    def test_partial_investment_surfaces_pending_cash(self):
        """
        Post Transfer for ₹1,00,000, holding cost basis ₹95,000.
        Assert get_current returns holdings_current_value + ₹5,000.
        """
        demat_acc = Account.objects.create(
            user=self.user,
            name='Paytm Money Demat',
            account_type='DEMAT',
            balance=Decimal('0.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=demat_acc)

        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=demat_acc,
            amount=Decimal('100000.00'),
            date='2026-08-01',
            description='Fund investment',
        )

        h = Holding.objects.create(
            account=demat_acc,
            instrument_name='Flexi Cap MF',
            instrument_type='MF',
            units=Decimal('95.000000'),
            avg_cost=Decimal('1000.00'),  # cost basis = 95,000
            currency='₹',
            is_active=True,
        )
        Valuation.objects.create(
            holding=h,
            value=Decimal('98000.00'),  # current market value = 98,000
        )

        current_val = get_current(demat_acc)
        # 98,000 market value + max(0, 100,000 - 95,000) uninvested cash = 103,000.00
        self.assertEqual(current_val, Decimal('103000.00'))

    def test_zero_holdings_baseline_case(self):
        """
        An account with no holdings and a real ledger balance returns full balance as current.
        """
        demat_acc = Account.objects.create(
            user=self.user,
            name='New Demat No Holdings',
            account_type='DEMAT',
            balance=Decimal('0.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=demat_acc)

        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=demat_acc,
            amount=Decimal('50000.00'),
            date='2026-08-01',
            description='Fresh cash deposit',
        )

        current_val = get_current(demat_acc)
        self.assertEqual(current_val, Decimal('50000.00'))

    def test_get_net_worth_matches_direct_account_valuation(self):
        """
        Verify get_net_worth produces the same number as account_valuation.get_current / get_baseline.
        """
        demat_acc = Account.objects.create(
            user=self.user,
            name='Consolidated Test Demat',
            account_type='DEMAT',
            balance=Decimal('0.00'),
            currency='₹',
        )
        LedgerPostingService.post_opening_balance(account=demat_acc)

        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=demat_acc,
            amount=Decimal('100000.00'),
            date='2026-08-01',
            description='Fund investment',
        )
        h = Holding.objects.create(
            account=demat_acc,
            instrument_name='Sensex ETF',
            instrument_type='ETF',
            units=Decimal('100.000000'),
            avg_cost=Decimal('900.00'),  # cost basis = 90,000
            currency='₹',
            is_active=True,
        )
        Valuation.objects.create(
            holding=h,
            value=Decimal('95000.00'),
        )

        expected_current = get_current(demat_acc)
        expected_baseline = get_baseline(demat_acc)

        _, nw_accrued = LedgerReadService.get_net_worth(self.user)
        self.assertIn(demat_acc.id, nw_accrued)
        self.assertEqual(nw_accrued[demat_acc.id], expected_current)

        # Baseline check (extended=True, toggle off -> show_accrued_balance = False)
        demat_acc.show_accrued_balance = False
        demat_acc.save()
        _, nw_baseline = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(nw_baseline[demat_acc.id], expected_baseline)
