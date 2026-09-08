from decimal import Decimal
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from expenses.models import Account, UserProfile, Holding, Valuation, PhysicalAsset, AssetValuation
from expenses.ledger_read_service import LedgerReadService
from expenses.account_types import resolve_category_selector

User = get_user_model()


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True
)
class NetWorthFilteringTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='filteruser', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': 'INR'})

        # Cash Account: ₹10,000
        self.bank_acc = Account.objects.create(
            user=self.user,
            name='Savings Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('10000.00'),
            currency='INR'
        )

        # Investment Account: Mutual Fund worth ₹50,000
        self.mf_acc = Account.objects.create(
            user=self.user,
            name='Mutual Funds Account',
            account_type='MUTUAL_FUND',
            balance=Decimal('0.00'),
            currency='INR'
        )
        self.holding = Holding.objects.create(
            account=self.mf_acc,
            instrument_name='Index Fund',
            units=Decimal('100.00'),
            avg_cost=Decimal('400.00'),
            currency='INR',
            is_active=True
        )
        Valuation.objects.create(
            holding=self.holding,
            as_of_date='2026-01-01',
            value=Decimal('50000.00'),
            cost_basis=Decimal('40000.00')
        )

        # Credit Card: Owed ₹5,000 (represented as negative balance)
        self.cc_acc = Account.objects.create(
            user=self.user,
            name='Credit Card',
            account_type='CREDIT_CARD',
            balance=Decimal('-5000.00'),
            currency='INR'
        )

        # Physical Asset: Real Estate worth ₹100,000
        self.re_asset = PhysicalAsset.objects.create(
            user=self.user,
            name='Apartment',
            asset_type='REAL_ESTATE',
            acquisition_cost=Decimal('80000.00'),
            currency='INR',
            is_active=True
        )
        AssetValuation.objects.create(
            physical_asset=self.re_asset,
            as_of_date='2026-01-01',
            value=Decimal('100000.00')
        )
        self.re_acc = Account.objects.create(
            user=self.user,
            name='Apartment Account',
            account_type='REAL_ESTATE',
            balance=Decimal('0.00'),
            currency='INR',
            linked_physical_asset=self.re_asset
        )

    def test_default_unfiltered_net_worth(self):
        """Unfiltered net worth = 10,000 (bank) + 50,000 (mf) - 5,000 (cc) + 100,000 (re) = 155,000."""
        total_nw, balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(total_nw, Decimal('155000.00'))
        self.assertEqual(len(balances), 4)

    def test_include_categories_group(self):
        """include_categories=['Investments'] should only include the MUTUAL_FUND account."""
        total_nw, balances = LedgerReadService.get_net_worth(
            self.user, include_categories=['Investments']
        )
        self.assertEqual(total_nw, Decimal('50000.00'))
        self.assertEqual(list(balances.keys()), [self.mf_acc.pk])
        self.assertEqual(balances[self.mf_acc.pk], Decimal('50000.00'))

    def test_include_categories_bare_code(self):
        """include_categories=['CREDIT_CARD'] should return negative balance of credit card."""
        total_nw, balances = LedgerReadService.get_net_worth(
            self.user, include_categories=['CREDIT_CARD']
        )
        self.assertEqual(total_nw, Decimal('-5000.00'))
        self.assertEqual(list(balances.keys()), [self.cc_acc.pk])
        self.assertEqual(balances[self.cc_acc.pk], Decimal('-5000.00'))

    def test_exclude_categories_group(self):
        """exclude_categories=['Physical Assets'] should exclude real estate account (155k - 100k = 55k)."""
        total_nw, balances = LedgerReadService.get_net_worth(
            self.user, exclude_categories=['Physical Assets']
        )
        self.assertEqual(total_nw, Decimal('55000.00'))
        self.assertNotIn(self.re_acc.pk, balances)
        self.assertIn(self.bank_acc.pk, balances)
        self.assertIn(self.mf_acc.pk, balances)
        self.assertIn(self.cc_acc.pk, balances)

    def test_invalid_category_selector_raises_value_error(self):
        """Passing an invalid category group or code must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            resolve_category_selector(['INVALID_CATEGORY'])
        self.assertIn("INVALID_CATEGORY", str(ctx.exception))

        with self.assertRaises(ValueError):
            LedgerReadService.get_net_worth(self.user, include_categories=['NonExistent'])

    def test_query_count_unfiltered_vs_filtered(self):
        """Category filtering happens in Python loop and adds zero DB queries."""
        with self.assertNumQueries(7):
            LedgerReadService.get_net_worth(self.user)

        with self.assertNumQueries(7):
            LedgerReadService.get_net_worth(self.user, include_categories=['Investments'])
