"""Insurance audit: query counts, as-of valuation, FX, premium schedule boundaries, data migrations."""
import importlib
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses.account_valuation import (
    get_current_insurance_surrender,
    get_premiums_paid,
    premiums_paid_by_asset,
)
from expenses.forms import AccountForm
from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    Expense,
    PhysicalAsset,
    RecurringTransaction,
    UserProfile,
)


class InsuranceBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="insaudit", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.bank = Account.objects.create(user=self.user, name="Bank", account_type="SAVINGS_ACCOUNT",
                                           balance=Decimal("500000.00"))

    def policy(self, name, policy_type='SAVINGS', account_type='LIFE_INSURANCE', balance='0.00', **asset_kw):
        asset = PhysicalAsset.objects.create(user=self.user, name=name, asset_class="INSURANCE", currency="₹",
                                             policy_type=policy_type, **asset_kw)
        account = Account.objects.create(user=self.user, name=name, account_type=account_type, currency="₹",
                                         balance=Decimal(balance), linked_physical_asset=asset)
        return account, asset

    def premium(self, asset, amount, on=None, currency="₹"):
        return Expense.objects.create(user=self.user, account=self.bank, amount=Decimal(amount), currency=currency,
                                      category="Insurance", linked_physical_asset=asset, date=on or date.today())

    def count_queries(self, fn):
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return len(ctx)


class PremiumsPaidQueryCountTests(InsuranceBase):
    def test_premiums_paid_by_asset_is_one_query_for_any_number_of_policies(self):
        assets = [self.policy(f"P{i}")[1] for i in range(5)]
        for a in assets:
            self.premium(a, "1000.00")
        with self.assertNumQueries(1):
            result = premiums_paid_by_asset([a.id for a in assets])
        self.assertEqual({k: v['₹'] for k, v in result.items()}, {a.id: Decimal("1000.00") for a in assets})

    def test_net_worth_query_count_does_not_grow_with_policies(self):
        a1, s1 = self.policy("One")
        self.premium(s1, "1000.00")
        LedgerReadService.get_net_worth(self.user)          # warm caches
        base = self.count_queries(lambda: LedgerReadService.get_net_worth(self.user))
        for i in range(5):
            _, asset = self.policy(f"Extra {i}")
            self.premium(asset, "500.00")
        LedgerReadService.get_net_worth(self.user)
        grown = self.count_queries(lambda: LedgerReadService.get_net_worth(self.user))
        self.assertEqual(grown, base)

    def test_account_list_query_count_does_not_grow_with_protection_policies(self):
        self.client.force_login(self.user)
        _, first = self.policy("Term 0", 'PROTECTION', 'HEALTH_INSURANCE', balance='100.00')
        self.premium(first, "1000.00")
        url = reverse('account-list')
        self.client.get(url)
        base = self.count_queries(lambda: self.client.get(url))
        for i in range(1, 5):
            _, asset = self.policy(f"Term {i}", 'PROTECTION', 'HEALTH_INSURANCE', balance='100.00')
            self.premium(asset, "1000.00")
        self.client.get(url)
        grown = self.count_queries(lambda: self.client.get(url))
        self.assertLessEqual(grown, base + 1)       # the extra rows cost no queries of their own


class PremiumsPaidValueTests(InsuranceBase):
    def test_net_worth_as_of_ignores_later_premiums(self):
        account, asset = self.policy("Endowment")
        self.premium(asset, "1000.00", on=date.today() - timedelta(days=400))
        self.premium(asset, "1000.00", on=date.today() - timedelta(days=10))
        _, now = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(Decimal(str(now[account.id])), Decimal("2000.00"))
        _, then = LedgerReadService.get_net_worth(self.user, as_of=date.today() - timedelta(days=200))
        self.assertEqual(Decimal(str(then[account.id])), Decimal("1000.00"))

    def test_premiums_in_another_currency_are_converted(self):
        account, asset = self.policy("Foreign")
        self.premium(asset, "100.00", currency="$")
        self.premium(asset, "50.00")
        with patch('expenses.utils.get_exchange_rate', return_value=Decimal("80")) as rate:
            total = get_premiums_paid(account)
        rate.assert_called_once_with("$", "₹")
        self.assertEqual(total, Decimal("8050.00"))

    def test_only_this_policys_premiums_count(self):
        account, asset = self.policy("Mine")
        _, other = self.policy("Other")
        self.premium(asset, "700.00")
        self.premium(other, "9999.00")
        self.assertEqual(get_premiums_paid(account), Decimal("700.00"))

    def test_money_in_the_policy_account_adds_to_premiums_but_overdraft_does_not(self):
        account, asset = self.policy("Pot")
        self.premium(asset, "1000.00")
        self.assertEqual(get_premiums_paid(account, Decimal("250.00")), Decimal("1250.00"))
        self.assertEqual(get_premiums_paid(account, Decimal("-250.00")), Decimal("1000.00"))

    def test_recorded_surrender_value_wins_over_premiums(self):
        from expenses.models import AssetValuation
        account, asset = self.policy("Surrender")
        self.premium(asset, "1000.00")
        AssetValuation.objects.create(asset=asset, value=Decimal("1500.00"), as_of_date=date.today())
        self.assertEqual(get_current_insurance_surrender(account), Decimal("1500.00"))

    def test_policy_without_linked_asset_uses_ledger_balance_only(self):
        account = Account.objects.create(user=self.user, name="Bare", account_type="LIFE_INSURANCE", currency="₹")
        self.assertEqual(get_premiums_paid(account, Decimal("300.00")), Decimal("300.00"))
        self.assertEqual(get_current_insurance_surrender(account, Decimal("300.00")), Decimal("300.00"))


class PolicyTypeMigrationTests(InsuranceBase):
    def test_health_policies_become_protection_and_others_stay(self):
        health, h_asset = self.policy("Health", 'SAVINGS', 'HEALTH_INSURANCE')
        life, l_asset = self.policy("Life", 'SAVINGS', 'LIFE_INSURANCE')
        migration = importlib.import_module('expenses.migrations.0111_health_policies_are_protection')
        migration.mark_health_policies(django_apps, None)
        h_asset.refresh_from_db()
        l_asset.refresh_from_db()
        self.assertEqual(h_asset.policy_type, 'PROTECTION')
        self.assertEqual(l_asset.policy_type, 'SAVINGS')
        migration.mark_health_policies(django_apps, None)       # safe to run twice
        h_asset.refresh_from_db()
        self.assertEqual(h_asset.policy_type, 'PROTECTION')

    def test_premium_link_migration_links_only_matching_unlinked_expenses(self):
        _, asset = self.policy("Linkme")
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type='INSURANCE_PREMIUM', physical_asset=asset, account=self.bank,
            amount=Decimal("1000.00"), frequency='YEARLY', start_date=date.today(), description="Linkme Premium",
            category="Insurance",
        )
        matching = Expense.objects.create(user=self.user, account=self.bank, amount=Decimal("1000.00"), currency="₹",
                                          category="Insurance", date=date.today(), description=f"{rt.description} (Recurring)")
        other = Expense.objects.create(user=self.user, account=self.bank, amount=Decimal("1000.00"), currency="₹",
                                       category="Insurance", date=date.today(), description="Something else")
        migration = importlib.import_module('expenses.migrations.0110_link_recurring_premium_expenses')
        migration.link_premium_expenses(django_apps, None)
        matching.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(matching.linked_physical_asset_id, asset.id)
        self.assertIsNone(other.linked_physical_asset_id)


class PremiumScheduleBoundaryTests(InsuranceBase):
    def rt(self, frequency, start):
        return RecurringTransaction(frequency=frequency, start_date=start)

    def test_future_start_has_no_past_premium(self):
        today = timezone.localdate()
        self.assertIsNone(AccountForm._latest_premium_due_on_or_before(self.rt('YEARLY', today + timedelta(days=5)), today))

    def test_start_today_is_the_latest_due(self):
        today = timezone.localdate()
        self.assertEqual(AccountForm._latest_premium_due_on_or_before(self.rt('YEARLY', today), today), today)

    def test_latest_due_for_each_frequency(self):
        today = date(2026, 10, 17)
        start = date(2024, 3, 10)
        self.assertEqual(AccountForm._latest_premium_due_on_or_before(self.rt('YEARLY', start), today), date(2026, 3, 10))
        self.assertEqual(AccountForm._latest_premium_due_on_or_before(self.rt('SEMIANNUALLY', start), today), date(2026, 9, 10))
        self.assertEqual(AccountForm._latest_premium_due_on_or_before(self.rt('QUARTERLY', start), today), date(2026, 9, 10))
        self.assertEqual(AccountForm._latest_premium_due_on_or_before(self.rt('MONTHLY', start), today), date(2026, 10, 10))

    def test_very_old_start_terminates(self):
        today = date(2026, 10, 17)
        result = AccountForm._latest_premium_due_on_or_before(self.rt('MONTHLY', date(1990, 1, 5)), today)
        self.assertLessEqual(result, today)


class InsuranceFormFlowTests(InsuranceBase):
    def data(self, **extra):
        start = date.today() - timedelta(days=500)
        data = {
            'name': 'LIC', 'account_type': 'LIFE_INSURANCE', 'currency': '₹', 'balance': '0.00',
            'create_new_asset': 'CREATE_NEW', 'asset_name': 'LIC Plan', 'policy_number': 'P1',
            'premium_amount': '1000.00', 'premium_frequency': 'ANNUAL', 'policy_start_date': start.isoformat(),
            'sum_assured': '100000.00', 'premium_payment_account': self.bank.pk,
        }
        data.update(extra)
        return data

    def test_resaving_an_existing_policy_does_not_duplicate_schedule_or_premiums(self):
        form = AccountForm(data=self.data(create_historical_premiums='on'), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        premiums = Expense.objects.filter(linked_physical_asset=account.linked_physical_asset).count()
        self.assertGreaterEqual(premiums, 1)
        again = AccountForm(data=self.data(create_historical_premiums='on', premium_amount='1000.00'),
                            instance=account, user=self.user)
        self.assertTrue(again.is_valid(), again.errors)
        again.save()
        self.assertEqual(RecurringTransaction.objects.filter(physical_asset=account.linked_physical_asset).count(), 1)
        self.assertEqual(Expense.objects.filter(linked_physical_asset=account.linked_physical_asset).count(), premiums)

    def test_changing_policy_type_on_edit_updates_the_asset(self):
        form = AccountForm(data=self.data(policy_type='SAVINGS'), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        edit = AccountForm(data=self.data(policy_type='PROTECTION'), instance=account, user=self.user)
        self.assertTrue(edit.is_valid(), edit.errors)
        edit.save()
        account.linked_physical_asset.refresh_from_db()
        self.assertEqual(account.linked_physical_asset.policy_type, 'PROTECTION')

    def test_invalid_policy_type_is_rejected(self):
        form = AccountForm(data=self.data(policy_type='NOPE'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('policy_type', form.errors)

    def test_policy_type_defaults_by_account_type_when_not_sent(self):
        form = AccountForm(data=self.data(account_type='HEALTH_INSURANCE'), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().linked_physical_asset.policy_type, 'PROTECTION')
        life = AccountForm(data=self.data(name='LIC2', asset_name='LIC2 Plan'), user=self.user)
        self.assertTrue(life.is_valid(), life.errors)
        self.assertEqual(life.save().linked_physical_asset.policy_type, 'SAVINGS')


class InsuranceDetailEdgeTests(InsuranceBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_detail_without_schedule_omits_next_premium_and_paid_from(self):
        account, _ = self.policy("NoSchedule", policy_number="N-1", premium_amount=Decimal("1000.00"),
                                 premium_frequency="ANNUAL")
        response = self.client.get(reverse('account-detail', args=[account.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'N-1')
        self.assertNotContains(response, 'Next Premium:')
        self.assertNotContains(response, 'Paid From:')

    def test_detail_with_schedule_shows_next_premium_and_paid_from(self):
        account, asset = self.policy("Scheduled", policy_number="S-1", premium_amount=Decimal("1000.00"),
                                     premium_frequency="ANNUAL")
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='INSURANCE_PREMIUM', physical_asset=asset, account=self.bank,
            amount=Decimal("1000.00"), frequency='YEARLY', start_date=date.today() + timedelta(days=30),
            description="Scheduled Premium", category="Insurance",
        )
        response = self.client.get(reverse('account-detail', args=[account.uuid]))
        self.assertContains(response, 'Next Premium:')
        self.assertContains(response, 'Paid From:')

    def test_savings_policy_detail_shows_current_balance_not_premiums_paid_label(self):
        account, asset = self.policy("Savings")
        self.premium(asset, "1000.00")
        response = self.client.get(reverse('account-detail', args=[account.uuid]))
        self.assertContains(response, 'Current Balance')
        self.assertNotContains(response, 'not counted in your net worth')

    def test_other_users_policy_detail_is_404(self):
        account, _ = self.policy("Mine")
        other = User.objects.create_user(username="intruder", password="password")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('account-detail', args=[account.uuid])).status_code, 404)

    def test_protection_policy_adds_nothing_to_the_net_worth_total(self):
        account, asset = self.policy("Health", 'PROTECTION', 'HEALTH_INSURANCE')
        self.premium(asset, "5000.00")
        total, balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(Decimal(str(balances[account.id])), Decimal("0.00"))
        self.assertEqual(Decimal(str(total)), Decimal(str(balances[self.bank.id])))


class PolicyTypeFormPageTests(InsuranceBase):
    def test_policy_type_select_is_refreshed_when_account_type_picks_a_default(self):
        """The styled select only redraws its label on a change event, so the page must fire one."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('account-create'))
        self.assertContains(response, "policyType.dispatchEvent(new Event('change'")
        self.assertContains(response, 'id="id_policy_type"')
        self.assertContains(response, 'id="id_create_historical_premiums"')


class InsuranceEditSubmitTests(InsuranceBase):
    """Submit the edit page the way a browser does (POST through the view, not just the form)."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        start = date.today() - timedelta(days=500)
        self.start = start
        form = AccountForm(data={
            'name': 'LIC', 'account_type': 'LIFE_INSURANCE', 'currency': '₹', 'balance': '0.00',
            'create_new_asset': 'CREATE_NEW', 'asset_name': 'LIC Plan', 'policy_number': 'P1',
            'premium_amount': '1000.00', 'premium_frequency': 'ANNUAL', 'policy_start_date': start.isoformat(),
            'sum_assured': '100000.00', 'premium_payment_account': self.bank.pk, 'policy_type': 'SAVINGS',
        }, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.account = form.save()
        self.asset = self.account.linked_physical_asset

    def post(self, **overrides):
        data = {
            'name': 'LIC', 'account_type': 'LIFE_INSURANCE', 'currency': '₹', 'balance': '0.00',
            'create_new_asset': 'CREATE_NEW', 'asset_name': 'LIC Plan', 'policy_number': 'P1',
            'premium_amount': '1000.00', 'premium_frequency': 'ANNUAL', 'policy_start_date': self.start.isoformat(),
            'sum_assured': '100000.00', 'premium_payment_account': self.bank.pk, 'policy_type': 'SAVINGS',
        }
        data.update(overrides)
        return self.client.post(reverse('account-edit', args=[self.account.uuid]), data)

    def test_edit_page_loads_with_current_policy_details(self):
        response = self.client.get(reverse('account-edit', args=[self.account.uuid]))
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form.initial['policy_type'], 'SAVINGS')
        self.assertEqual(form.initial['policy_number'], 'P1')

    def test_post_updates_policy_in_place_and_redirects(self):
        response = self.post(policy_number='P2', premium_amount='1500.00', policy_type='PROTECTION',
                             sum_assured='200000.00')
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.assertEqual((self.asset.policy_number, self.asset.premium_amount, self.asset.policy_type,
                          self.asset.sum_assured), ('P2', Decimal('1500.00'), 'PROTECTION', Decimal('200000.00')))
        self.assertEqual(PhysicalAsset.objects.filter(user=self.user, asset_class='INSURANCE').count(), 1)
        rt = RecurringTransaction.objects.get(physical_asset=self.asset)
        self.assertEqual(rt.amount, Decimal('1500.00'))
        self.assertEqual(RecurringTransaction.objects.filter(physical_asset=self.asset).count(), 1)

    def test_switching_to_protection_zeroes_net_worth_value_after_submit(self):
        Expense.objects.create(user=self.user, account=self.bank, amount=Decimal('1000.00'), currency='₹',
                               category='Insurance', linked_physical_asset=self.asset, date=date.today())
        _, before = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(Decimal(str(before[self.account.id])), Decimal('1000.00'))
        self.assertEqual(self.post(policy_type='PROTECTION').status_code, 302)
        from django.core.cache import cache
        cache.clear()
        _, after = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(Decimal(str(after[self.account.id])), Decimal('0.00'))

    def test_invalid_post_rerenders_with_errors_and_changes_nothing(self):
        response = self.post(policy_type='BOGUS', policy_number='CHANGED')
        self.assertEqual(response.status_code, 200)
        self.assertIn('policy_type', response.context['form'].errors)
        self.asset.refresh_from_db()
        self.assertEqual((self.asset.policy_number, self.asset.policy_type), ('P1', 'SAVINGS'))

    def test_resubmitting_without_changes_does_not_duplicate_premiums(self):
        self.assertEqual(self.post(create_historical_premiums='on').status_code, 302)
        count = Expense.objects.filter(linked_physical_asset=self.asset).count()
        self.assertEqual(self.post(create_historical_premiums='on').status_code, 302)
        self.assertEqual(Expense.objects.filter(linked_physical_asset=self.asset).count(), count)
        self.assertEqual(RecurringTransaction.objects.filter(physical_asset=self.asset).count(), 1)

    def test_another_users_policy_cannot_be_edited(self):
        other = User.objects.create_user(username='editintruder', password='password')
        self.client.force_login(other)
        self.assertEqual(self.post(policy_number='HACK').status_code, 404)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.policy_number, 'P1')
