from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.forms import AccountForm
from expenses.models import Account, AssetValuation, PhysicalAsset, UserProfile


class PhysicalValuationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testphysicalval", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})

        self.asset = PhysicalAsset.objects.create(
            user=self.user,
            name="Pune Apartment",
            asset_class="REAL_ESTATE",
            acquisition_cost=Decimal("6500000.00"),
            acquisition_date=date(2019, 4, 1),
            currency="₹",
        )
        AssetValuation.objects.create(
            asset=self.asset,
            value=Decimal("6500000.00"),
            as_of_date=date(2019, 4, 1),
        )

        self.account = Account.objects.create(
            user=self.user,
            name="Pune Flat Account",
            account_type="REAL_ESTATE",
            currency="₹",
            linked_physical_asset=self.asset,
        )

    def test_baseline_and_current_physical_valuation(self):
        """SPEC §2.5: Baseline is acquisition cost, current is latest AssetValuation."""
        baseline = get_baseline(self.account)
        current = get_current(self.account)
        self.assertEqual(baseline, Decimal("6500000.00"))
        self.assertEqual(current, Decimal("6500000.00"))

        # Post a new valuation
        AssetValuation.objects.create(
            asset=self.asset,
            value=Decimal("9200000.00"),
            as_of_date=date(2023, 10, 1),
        )

        # Baseline stays at acquisition cost, current updates to 92,00,000
        self.assertEqual(get_baseline(self.account), Decimal("6500000.00"))
        self.assertEqual(get_current(self.account), Decimal("9200000.00"))

    def test_atomic_account_physical_asset_creation_form(self):
        """SPEC §1 Decision 3 & §2.5: Atomic creation of Account + PhysicalAsset via AccountForm."""
        form_data = {
            'name': 'Honda City Car',
            'account_type': 'VEHICLE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'Honda City VMT',
            'acquisition_cost': '1200000.00',
            'acquisition_date': '2022-05-15',
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        self.assertIsNotNone(account.linked_physical_asset)
        asset = account.linked_physical_asset
        self.assertEqual(asset.name, 'Honda City VMT')
        self.assertEqual(asset.acquisition_cost, Decimal('1200000.00'))

        # Verify initial AssetValuation was seeded
        latest_val = asset.valuations.first()
        self.assertIsNotNone(latest_val)
        self.assertEqual(latest_val.value, Decimal('1200000.00'))
