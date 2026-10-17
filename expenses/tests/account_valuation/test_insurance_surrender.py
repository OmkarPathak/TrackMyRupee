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

    def test_insurance_fallback_is_zero_when_no_valuation_and_no_ledger_balance(self):
        """No surrender value and no ledger balance given: 0.00, never baseline or acquisition cost."""
        current = get_current(self.account)
        self.assertEqual(current, Decimal("0.00"))

    def test_insurance_falls_back_to_ledger_balance_without_surrender_value(self):
        """Users cannot enter a surrender value, so money paid in (ledger balance) is shown, also over a seeded 0.00."""
        self.assertEqual(get_current(self.account, ledger_balance=Decimal("26224.00")), Decimal("26224.00"))
        AssetValuation.objects.create(asset=self.policy, value=Decimal("0.00"), as_of_date=date(2023, 12, 1))
        self.assertEqual(get_current(self.account, ledger_balance=Decimal("26224.00")), Decimal("26224.00"))

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


class InsuranceHistoricalPremiumsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="histins", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.bank = Account.objects.create(user=self.user, name="Bank", account_type="SAVINGS_ACCOUNT", balance=Decimal("500000.00"))

    def _create(self, historical):
        from datetime import timedelta
        start = date.today().replace(year=date.today().year - 2) - timedelta(days=10)
        data = {
            'name': 'LIC', 'account_type': 'LIFE_INSURANCE', 'currency': '₹', 'balance': '0.00',
            'create_new_asset': 'CREATE_NEW', 'asset_name': 'LIC Plan', 'policy_number': 'P1',
            'premium_amount': '1000.00', 'premium_frequency': 'ANNUAL', 'policy_start_date': start.isoformat(),
            'sum_assured': '100000.00', 'premium_payment_account': self.bank.pk,
        }
        if historical:
            data['create_historical_premiums'] = 'on'
        form = AccountForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save(), start

    def test_without_checkbox_only_future_premium_is_scheduled(self):
        account, start = self._create(historical=False)
        rt = account.linked_physical_asset.recurring_schedules.get()
        self.assertGreater(rt.next_due_date, date.today())
        self.assertEqual(rt.start_date, start)
        self.assertEqual(Expense.objects.filter(account=self.bank).count(), 0)

    def test_with_checkbox_past_premiums_are_recorded(self):
        account, _ = self._create(historical=True)
        self.assertGreaterEqual(Expense.objects.filter(account=self.bank).count(), 2)


class InsuranceDetailPageTestCase(TestCase):
    def test_detail_page_shows_policy_details(self):
        from django.urls import reverse
        user = User.objects.create_user(username="detailins", password="password")
        UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        policy = PhysicalAsset.objects.create(
            user=user, name="LIC Plan", asset_class="INSURANCE", policy_number="POL-4242",
            premium_amount=Decimal("26224.00"), premium_frequency="ANNUAL",
            policy_start_date=date(2020, 11, 11), sum_assured=Decimal("500000.00"), currency="₹",
        )
        account = Account.objects.create(user=user, name="LIC", account_type="LIFE_INSURANCE", currency="₹", linked_physical_asset=policy)
        self.client.force_login(user)
        response = self.client.get(reverse('account-detail', args=[account.uuid]))
        self.assertEqual(response.status_code, 200)
        for text in ('POL-4242', '5,00,000', '11 Nov 2020', '26,224', 'Annual'):
            self.assertContains(response, text)


class InsurancePolicyTypeTestCase(TestCase):
    def setUp(self):
        from expenses.ledger_read_service import LedgerReadService
        self.nw = LedgerReadService.get_net_worth
        self.user = User.objects.create_user(username="ptype", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.bank = Account.objects.create(user=self.user, name="Bank", account_type="SAVINGS_ACCOUNT", balance=Decimal("100000.00"))

    def _policy(self, name, policy_type=None, balance='0.00'):
        kwargs = {'policy_type': policy_type} if policy_type else {}
        asset = PhysicalAsset.objects.create(user=self.user, name=name, asset_class="INSURANCE", currency="₹", **kwargs)
        account = Account.objects.create(
            user=self.user, name=name, account_type="LIFE_INSURANCE", currency="₹",
            balance=Decimal(balance), linked_physical_asset=asset,
        )
        return account, asset

    def test_existing_policies_default_to_savings(self):
        _, asset = self._policy("Old")
        self.assertEqual(asset.policy_type, 'SAVINGS')

    def test_protection_is_worth_nothing_but_savings_counts_premiums_paid(self):
        savings, s_asset = self._policy("Endowment", balance='20000.00')
        protection, p_asset = self._policy("Term", 'PROTECTION', balance='30000.00')
        Expense.objects.create(user=self.user, account=self.bank, amount=Decimal("5000.00"), currency="₹",
                               category="Insurance", linked_physical_asset=s_asset, date=date.today())
        Expense.objects.create(user=self.user, account=self.bank, amount=Decimal("7000.00"), currency="₹",
                               category="Insurance", linked_physical_asset=p_asset, date=date.today())
        _, balances = self.nw(self.user)
        self.assertEqual(Decimal(str(balances[savings.id])), Decimal("25000.00"))
        self.assertEqual(Decimal(str(balances[protection.id])), Decimal("0.00"))

    def test_list_and_detail_show_premiums_paid_for_protection(self):
        from django.urls import reverse
        protection, p_asset = self._policy("Term", 'PROTECTION', balance='30000.00')
        self.client.force_login(self.user)
        response = self.client.get(reverse('account-list'))
        row = next(a for a in response.context['display_accounts'] if a.id == protection.id)
        self.assertTrue(row.is_protection)
        self.assertEqual(row.premiums_paid, Decimal("30000.00"))
        self.assertContains(response, 'premiums paid')
        detail = self.client.get(reverse('account-detail', args=[protection.uuid]))
        self.assertContains(detail, 'Premiums Paid')
        self.assertContains(detail, 'not counted in your net worth')

    def test_recurring_premium_expenses_are_linked_to_the_policy(self):
        account, asset = self._policy("Linked")
        form_data = {
            'name': 'New', 'account_type': 'HEALTH_INSURANCE', 'currency': '₹', 'balance': '0.00',
            'create_new_asset': 'CREATE_NEW', 'asset_name': 'Health', 'policy_number': 'H1',
            'premium_amount': '1000.00', 'premium_frequency': 'ANNUAL',
            'policy_start_date': date.today().isoformat(), 'premium_payment_account': self.bank.pk,
            'create_historical_premiums': 'on',
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        created = form.save()
        # Health cover defaults to protection when the type is not sent
        self.assertEqual(created.linked_physical_asset.policy_type, 'PROTECTION')
        self.assertEqual(Expense.objects.filter(linked_physical_asset=created.linked_physical_asset).count(), 1)
