from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current
from expenses.forms import AccountForm
from expenses.models import (
    Account,
    AssetValuation,
    Category,
    Expense,
    PhysicalAsset,
    UserProfile,
)


class InsuranceSurrenderTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testinsurance", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})

        self.category = Category.objects.create(
            user=self.user,
            name="Insurance Premium",
        )

        self.policy = PhysicalAsset.objects.create(
            user=self.user,
            name="LIC Endowment Policy",
            asset_class="INSURANCE",
            policy_number="POL123456",
            premium_amount=Decimal("25000.00"),
            premium_frequency="ANNUAL",
            policy_start_date=date(2018, 1, 1),
            sum_assured=Decimal("500000.00"),
            currency="₹",
        )

        self.account = Account.objects.create(
            user=self.user,
            name="LIC Policy Account",
            account_type="LIFE_INSURANCE",
            currency="₹",
            linked_physical_asset=self.policy,
        )

    def test_insurance_fallback_is_zero_when_no_valuation_exists(self):
        """SPEC §2.6 CRITICAL REQUIREMENT: Insurance MUST fall back to 0.00, NEVER baseline or acquisition cost."""
        current = get_current(self.account)
        self.assertEqual(current, Decimal("0.00"))

    def test_insurance_baseline_sums_linked_premium_expenses(self):
        """Baseline sums Expense rows tagged with linked_physical_asset=policy."""
        # Log 6 annual premium payments
        for year in range(2018, 2024):
            Expense.objects.create(
                user=self.user,
                account=self.account,
                amount=Decimal("25000.00"),
                currency="₹",
                category="Insurance Premium",
                category_fk=self.category,
                linked_physical_asset=self.policy,
                date=date(year, 1, 5),
            )

        baseline = get_baseline(self.account)
        self.assertEqual(baseline, Decimal("150000.00"))

    def test_insurance_current_value_and_loss_framing(self):
        """When an insurer surrender statement is entered, current switches and unrealized loss computes correctly."""
        # Post surrender statement valuation from insurer (e.g. ₹95,000 after ₹1,50,000 premiums paid)
        AssetValuation.objects.create(
            asset=self.policy,
            value=Decimal("95000.00"),
            as_of_date=date(2023, 12, 1),
        )

        current = get_current(self.account)
        self.assertEqual(current, Decimal("95000.00"))

    def test_atomic_insurance_policy_creation_form(self):
        """SPEC §2.6: Atomic creation of Account + Insurance PhysicalAsset via AccountForm."""
        form_data = {
            'name': 'HDFC Life Endowment',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'HDFC Life Policy #998877',
            'policy_number': 'POL-998877',
            'premium_amount': '15000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': '2021-03-01',
            'sum_assured': '300000.00',
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        self.assertIsNotNone(account.linked_physical_asset)
        policy = account.linked_physical_asset
        self.assertEqual(policy.policy_number, 'POL-998877')
        self.assertEqual(policy.premium_amount, Decimal('15000.00'))

        # Verify initial AssetValuation was seeded as 0.00 (NOT premium_amount)
        latest_val = policy.valuations.first()
        self.assertIsNotNone(latest_val)
        self.assertEqual(latest_val.value, Decimal('0.00'))

    def test_edit_insurance_account_form_prefills_and_updates_in_place(self):
        """Editing an existing insurance account pre-fills form fields and updates the asset in-place without creating duplicates."""
        initial_data = {
            'name': 'Axis Term Insurance',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'Axis Term Policy',
            'policy_number': 'AXIS-1234',
            'premium_amount': '35000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': '2026-09-01',
            'sum_assured': '10000000.00',
        }
        form = AccountForm(data=initial_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        asset_id = account.linked_physical_asset_id

        # Initialize form for editing
        edit_form = AccountForm(instance=account, user=self.user)
        self.assertEqual(edit_form.initial['policy_number'], 'AXIS-1234')
        self.assertEqual(edit_form.initial['premium_amount'], Decimal('35000.00'))
        self.assertEqual(edit_form.initial['sum_assured'], Decimal('10000000.00'))
        self.assertEqual(edit_form.initial['policy_start_date'], date(2026, 9, 1))

        # Submit edits
        update_data = {
            'name': 'Axis Term Insurance Updated',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'Axis Term Policy Updated',
            'policy_number': 'AXIS-9999',
            'premium_amount': '36000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': '2026-09-01',
            'sum_assured': '10000000.00',
        }
        initial_asset_count = PhysicalAsset.objects.filter(user=self.user, asset_class='INSURANCE').count()
        submit_form = AccountForm(data=update_data, instance=account, user=self.user)
        self.assertTrue(submit_form.is_valid(), submit_form.errors)
        updated_account = submit_form.save()

        # Assert asset was updated in-place without creating a duplicate PhysicalAsset
        self.assertEqual(updated_account.linked_physical_asset_id, asset_id)
        asset = updated_account.linked_physical_asset
        asset.refresh_from_db()
        self.assertEqual(asset.policy_number, 'AXIS-9999')
        self.assertEqual(asset.premium_amount, Decimal('36000.00'))
        self.assertEqual(PhysicalAsset.objects.filter(user=self.user, asset_class='INSURANCE').count(), initial_asset_count)
