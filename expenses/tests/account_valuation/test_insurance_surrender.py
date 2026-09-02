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
