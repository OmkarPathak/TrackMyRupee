from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import RequestFactory, TestCase
from django.urls import reverse

from expenses.filters.definitions import RECURRING_FILTERS, filter_income_groups
from expenses.filters.engine import apply_filter_config
from expenses.models import (
    INCOME_GROUP_TYPES,
    Account,
    Expense,
    Income,
    RecurringTransaction,
    UserProfile,
)
from expenses.recurring_utils import (
    calculate_recurring_equivalents,
    get_recurring_month_occurrence_amount,
    get_recurring_monthly_equivalent,
    get_recurring_yearly_equivalent,
)
from expenses.views.income import IncomeListView, _create_recurring_from_income
from expenses.views.recurring import RecurringTransactionListView


class IncomeAndRecurringFindingsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='findinguser', password='password123')
        if not hasattr(self.user, 'profile'):
            self.profile = UserProfile.objects.create(user=self.user, currency='₹')
        else:
            self.profile = self.user.profile
            self.profile.currency = '₹'
            self.profile.save()

        self.account_cash = Account.objects.create(user=self.user, name='Cash Account', balance=Decimal('50000.00'))
        self.account_bank = Account.objects.create(user=self.user, name='Bank Account', balance=Decimal('100000.00'))
        self.factory = RequestFactory()

    # -------------------------------------------------------------------------
    # 1. Recurring Transaction Currency Display Bug
    # -------------------------------------------------------------------------
    def test_item1_recurring_delete_currency_display_uses_base_amount(self):
        """
        Asserts that deleting a recurring transaction priced in a foreign currency
        (e.g., USD with base_amount in INR) displays the yearly saving in base currency
        (base_amount * multiplier), not amount * multiplier.
        """
        # User base currency is INR ('₹')
        # Recurring transaction created in USD ($100), exchange_rate = 83.0 -> base_amount = 8300.00
        with patch('expenses.models.get_exchange_rate', return_value=Decimal('83.00')), \
             patch('expenses.utils.get_exchange_rate', return_value=Decimal('83.00')):
            rt = RecurringTransaction.objects.create(
                user=self.user,
                transaction_type='EXPENSE',
                currency='$',
                amount=Decimal('100.00'),
                exchange_rate=Decimal('83.00'),
                base_amount=Decimal('8300.00'),
                frequency='MONTHLY',
                start_date=date(2026, 1, 1),
                description='US Server Hosting',
                account=self.account_bank,
                is_active=True,
            )
            # Expected yearly saving in base currency: 8300 * 12 = 99,600 (not 100 * 12 = 1,200)
            self.assertEqual(rt.yearly_equivalent, Decimal('99600.00'))

            self.client.login(username='findinguser', password='password123')
            delete_url = reverse('recurring-delete', kwargs={'pk': rt.pk})
            response = self.client.post(delete_url, follow=True)
            self.assertEqual(response.status_code, 200)

            messages = list(get_messages(response.wsgi_request))
            self.assertTrue(len(messages) > 0)
            success_msg = str(messages[0])
            self.assertIn('99,600', success_msg)
            self.assertNotIn('1,200', success_msg)
            self.assertIn('₹', success_msg)

    # -------------------------------------------------------------------------
    # 2. Dashboard Cash-flow Forecast Gap and Multi-site Centralization
    # -------------------------------------------------------------------------
    def test_item2_centralized_recurring_equivalents_all_seven_frequencies(self):
        """
        Tests calculate_recurring_equivalents for all seven frequencies using exact multipliers:
        DAILY (30/365), WEEKLY (4/52), BIWEEKLY (2/26), MONTHLY (1/12),
        QUARTERLY (/3, *4), SEMIANNUALLY (/6, *2), YEARLY (/12, *1).
        """
        amt = Decimal('1200.00')

        # DAILY
        m, y = calculate_recurring_equivalents('DAILY', amt)
        self.assertEqual(m, Decimal('36000.00'))
        self.assertEqual(y, Decimal('438000.00'))

        # WEEKLY
        m, y = calculate_recurring_equivalents('WEEKLY', amt)
        self.assertEqual(m, Decimal('4800.00'))
        self.assertEqual(y, Decimal('62400.00'))

        # BIWEEKLY
        m, y = calculate_recurring_equivalents('BIWEEKLY', amt)
        self.assertEqual(m, Decimal('2400.00'))
        self.assertEqual(y, Decimal('31200.00'))

        # MONTHLY
        m, y = calculate_recurring_equivalents('MONTHLY', amt)
        self.assertEqual(m, Decimal('1200.00'))
        self.assertEqual(y, Decimal('14400.00'))

        # QUARTERLY
        m, y = calculate_recurring_equivalents('QUARTERLY', amt)
        self.assertEqual(m, Decimal('400.00'))
        self.assertEqual(y, Decimal('4800.00'))

        # SEMIANNUALLY
        m, y = calculate_recurring_equivalents('SEMIANNUALLY', amt)
        self.assertEqual(m, Decimal('200.00'))
        self.assertEqual(y, Decimal('2400.00'))

        # YEARLY
        m, y = calculate_recurring_equivalents('YEARLY', amt)
        self.assertEqual(m, Decimal('100.00'))
        self.assertEqual(y, Decimal('1200.00'))

    def test_item2_forecast_occurrence_calculation(self):
        """
        Tests get_recurring_month_occurrence_amount across all frequencies, dates, and bounds.
        """
        # Weekly item starting 2026-10-01 (Thu) with amount 500
        # In Oct 2026 (31 days): occurrences on Oct 1, 8, 15, 22, 29 = 5 occurrences -> 2500
        rt_weekly = RecurringTransaction(
            frequency='WEEKLY',
            base_amount=Decimal('500.00'),
            start_date=date(2026, 10, 1),
        )
        amt_oct = get_recurring_month_occurrence_amount(rt_weekly, 2026, 10)
        self.assertEqual(amt_oct, Decimal('2500.00'))

        # In Nov 2026 (starts Nov 1 Sun, first Thu is Nov 5, then 12, 19, 26) = 4 occurrences -> 2000
        amt_nov = get_recurring_month_occurrence_amount(rt_weekly, 2026, 11)
        self.assertEqual(amt_nov, Decimal('2000.00'))

        # Quarterly item starting 2026-08-15:
        # Aug 2026 (month_diff=0) -> recurs
        # Oct 2026 (month_diff=2) -> 0
        # Nov 2026 (month_diff=3) -> recurs
        rt_quarterly = RecurringTransaction(
            frequency='QUARTERLY',
            base_amount=Decimal('3000.00'),
            start_date=date(2026, 8, 15),
        )
        self.assertEqual(get_recurring_month_occurrence_amount(rt_quarterly, 2026, 10), Decimal('0.00'))
        self.assertEqual(get_recurring_month_occurrence_amount(rt_quarterly, 2026, 11), Decimal('3000.00'))

        # Daily item: full monthly equivalent
        rt_daily = RecurringTransaction(
            frequency='DAILY',
            base_amount=Decimal('50.00'),
            start_date=date(2026, 1, 1),
        )
        self.assertEqual(get_recurring_month_occurrence_amount(rt_daily, 2026, 10), Decimal('1500.00'))

    def test_item2_weekly_recurring_in_dashboard_forecast_and_recurring_list(self):
        """
        Exercises the shared logic from all calling sites:
        (a) Recurring Transactions page total
        (b) Delete savings
        (c) Dashboard 6-month forecast context (verifies weekly expense is included).
        """
        # Create weekly recurring expense of 1000
        today = date.today()
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('1000.00'),
            base_amount=Decimal('1000.00'),
            frequency='WEEKLY',
            start_date=today.replace(day=1),
            description='Weekly Groceries',
            account=self.account_cash,
            is_active=True,
        )

        self.client.login(username='findinguser', password='password123')

        # (a) Recurring Transactions list page total
        resp_list = self.client.get(reverse('recurring-list'))
        self.assertEqual(resp_list.status_code, 200)
        self.assertEqual(resp_list.context['total_monthly_cost'], Decimal('4000.00'))
        self.assertEqual(resp_list.context['total_yearly_cost'], Decimal('52000.00'))

        # (b) Model properties
        self.assertEqual(rt.monthly_equivalent, Decimal('4000.00'))
        self.assertEqual(rt.yearly_equivalent, Decimal('52000.00'))

        # (c) Dashboard 6-month forecast context
        self.profile.tier = 'PRO'
        self.profile.save()
        resp_dash = self.client.get(reverse('analytics'))
        self.assertEqual(resp_dash.status_code, 200)
        forecast_expenses = resp_dash.context.get('forecast_expenses')
        self.assertIsNotNone(forecast_expenses)
        self.assertEqual(len(forecast_expenses), 6)
        # Verify that forecast expenses reflect the weekly recurring commitment across the 6 months (> 0)
        for val in forecast_expenses:
            self.assertGreater(val, 0)

    # -------------------------------------------------------------------------
    # 3. Centralize Earned/Passive/One-off Income Grouping
    # -------------------------------------------------------------------------
    def test_item3_centralized_income_groups_drift_prevention(self):
        """
        Tests that mutating/extending INCOME_GROUP_TYPES immediately reflects in both
        IncomeListView stats and the filter_income_groups filter engine.
        """
        # Create income with a custom source type
        custom_type = 'Custom Consulting Bonus'
        Income.objects.create(
            user=self.user,
            date=date(2026, 9, 1),
            amount=Decimal('20000.00'),
            base_amount=Decimal('20000.00'),
            source='Client Project',
            source_type=custom_type,
            account=self.account_bank,
        )

        # Before adding to mapping, filter_income_groups for EARNED won't match it
        qs = Income.objects.filter(user=self.user)
        filtered_before = filter_income_groups(qs, ['EARNED'])
        self.assertEqual(filtered_before.count(), 0)

        # Patch INCOME_GROUP_TYPES to include the new custom type under EARNED
        new_mapping = {
            'EARNED': list(INCOME_GROUP_TYPES['EARNED']) + [custom_type],
            'PASSIVE': list(INCOME_GROUP_TYPES['PASSIVE']),
            'ONE_OFF': list(INCOME_GROUP_TYPES['ONE_OFF']),
        }
        with patch.dict('expenses.models.INCOME_GROUP_TYPES', new_mapping, clear=True):
            # 1. Filter engine reflects it
            filtered_after = filter_income_groups(qs, ['EARNED'])
            self.assertEqual(filtered_after.count(), 1)

            # 2. IncomeListView get_context_data aggregation reflects it
            request = self.factory.get('/income/list/')
            request.user = self.user
            view = IncomeListView()
            view.request = request
            view.kwargs = {}
            view.object_list = qs
            ctx = view.get_context_data()
            self.assertEqual(ctx['earned_total'], Decimal('20000.00'))

    # -------------------------------------------------------------------------
    # 4. Extract Duplicated add_to_recurring Logic in Income Views
    # -------------------------------------------------------------------------
    def test_item4_create_recurring_from_income_create_and_update_views(self):
        """
        Tests that _create_recurring_from_income creates a new recurring transaction
        when add_to_recurring is True, avoids duplicates if one already exists,
        and works identically from IncomeCreateView and IncomeUpdateView.
        """
        self.client.login(username='findinguser', password='password123')

        # 1. Create Income with add_to_recurring = True
        create_data = {
            'date': '2026-09-01',
            'amount': '75000.00',
            'currency': '₹',
            'source': 'Salary',
            'source_type': 'Salary',
            'account': str(self.account_bank.id),
            'add_to_recurring': 'on',
            'frequency': 'MONTHLY',
            'description': 'Direct Salary Deposit',
        }
        resp = self.client.post(reverse('income-create'), create_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        # Assert recurring transaction was created
        rt_qs = RecurringTransaction.objects.filter(user=self.user, transaction_type='INCOME', is_active=True)
        self.assertEqual(rt_qs.count(), 1)
        rt = rt_qs.first()
        self.assertEqual(rt.amount, Decimal('75000.00'))
        self.assertEqual(rt.frequency, 'MONTHLY')

        # 2. Create another Income with SAME source and add_to_recurring = True
        # Should NOT duplicate recurring transaction
        create_data_2 = {
            'date': '2026-10-01',
            'amount': '75000.00',
            'currency': '₹',
            'source': 'Salary',
            'source_type': 'Salary',
            'account': str(self.account_bank.id),
            'add_to_recurring': 'on',
            'frequency': 'MONTHLY',
            'description': 'Next Month Salary',
        }
        resp2 = self.client.post(reverse('income-create'), create_data_2, follow=True)
        self.assertEqual(resp2.status_code, 200)
        # Recurring count must still be 1
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='INCOME', is_active=True).count(), 1)

        # 3. Update view test with new source and add_to_recurring = True
        income_obj = Income.objects.create(
            user=self.user,
            date=date(2026, 9, 15),
            amount=Decimal('10000.00'),
            currency='₹',
            source='Freelance / Consulting',
            source_type='Freelance / Consulting',
            account=self.account_cash,
        )
        update_data = {
            'date': '2026-09-15',
            'amount': '12000.00',
            'currency': '₹',
            'source': 'Freelance / Consulting',
            'source_type': 'Freelance / Consulting',
            'account': str(self.account_cash.id),
            'add_to_recurring': 'on',
            'frequency': 'MONTHLY',
            'description': 'Freelance Retainer',
        }
        resp_update = self.client.post(reverse('income-edit', kwargs={'pk': income_obj.pk}), update_data, follow=True)
        self.assertEqual(resp_update.status_code, 200)
        self.assertTrue(RecurringTransaction.objects.filter(user=self.user, source='Freelance / Consulting', is_active=True).exists())

    # -------------------------------------------------------------------------
    # 5. Wire RecurringTransactionListView Through Shared Filter System
    # -------------------------------------------------------------------------
    def test_item5_recurring_transaction_filter_config_integration(self):
        """
        Tests that RecurringTransactionListView correctly routes through apply_filter_config
        for search (description & category), category, frequency, status, account (3-way OR),
        and transaction_type.
        """
        r1 = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('299.00'),
            base_amount=Decimal('299.00'),
            description='Hotstar VIP',
            category='Entertainment',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            is_active=True,
            account=self.account_bank,
        )
        r2 = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='TRANSFER',
            amount=Decimal('5000.00'),
            base_amount=Decimal('5000.00'),
            description='Auto Savings Transfer',
            category='Savings',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            is_active=True,
            from_account=self.account_bank,
            to_account=self.account_cash,
        )
        r3 = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('1000.00'),
            base_amount=Decimal('1000.00'),
            description='Old Cloud Storage',
            category='Cloud',
            frequency='YEARLY',
            start_date=date(2025, 1, 1),
            is_active=False,
            account=self.account_cash,
        )

        self.client.login(username='findinguser', password='password123')

        # 1. Search filter (description)
        resp = self.client.get(reverse('recurring-list') + '?search=Hotstar')
        self.assertEqual(resp.status_code, 200)
        active = resp.context['active_subs']
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].description, 'Hotstar VIP')

        # 2. Search filter (category)
        resp = self.client.get(reverse('recurring-list') + '?search=Savings')
        self.assertEqual(resp.status_code, 200)
        active = resp.context['active_subs']
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].description, 'Auto Savings Transfer')

        # 3. Category filter
        resp = self.client.get(reverse('recurring-list') + '?category=Entertainment')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['active_subs']), 1)

        # 4. Frequency filter
        resp = self.client.get(reverse('recurring-list') + '?frequency=YEARLY&status=cancelled')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.context['cancelled_subs']), 1)
        self.assertEqual(resp.context['cancelled_subs'][0].description, 'Old Cloud Storage')

        # 5. Status filter
        resp_act = self.client.get(reverse('recurring-list') + '?status=active')
        self.assertEqual(resp_act.status_code, 200)
        self.assertEqual(len(resp_act.context['cancelled_subs']), 0)
        self.assertEqual(len(resp_act.context['active_subs']), 2)

        # 6. Account 3-way OR filter (matches to_account on transfer r2)
        resp_acc = self.client.get(reverse('recurring-list') + f'?account={self.account_cash.id}&status=active')
        self.assertEqual(resp_acc.status_code, 200)
        self.assertEqual(len(resp_acc.context['active_subs']), 1)
        self.assertEqual(resp_acc.context['active_subs'][0].description, 'Auto Savings Transfer')

        # 7. Transaction type filter
        resp_type = self.client.get(reverse('recurring-list') + '?transaction_type=TRANSFER')
        self.assertEqual(resp_type.status_code, 200)
        self.assertEqual(len(resp_type.context['active_subs']), 1)
        self.assertEqual(resp_type.context['active_subs'][0].transaction_type, 'TRANSFER')

    # -------------------------------------------------------------------------
    # 6. Fix N+1 in Recurring-Lock Checking
    # -------------------------------------------------------------------------
    def test_item6_recurring_lock_batch_evaluation_matches_per_item(self):
        """
        Tests that computing lock status for active_subs via list indexing
        produces identical results to UserProfile.is_recurring_locked(sub)
        across multiple limit configurations (limit=0, limit=2, limit=5, limit=-1).
        """
        # Create 5 recurring transactions in strict creation order
        subs = []
        base_time = date(2026, 1, 1)
        for i in range(5):
            s = RecurringTransaction.objects.create(
                user=self.user,
                transaction_type='EXPENSE',
                amount=Decimal('100.00'),
                base_amount=Decimal('100.00'),
                description=f'Sub {i+1}',
                frequency='MONTHLY',
                start_date=base_time + timedelta(days=i),
                is_active=True,
                account=self.account_bank,
            )
            subs.append(s)

        # Test limit scenarios: 0, 2, 5, -1 (unlimited)
        test_limits = [0, 2, 5, -1]
        for lim in test_limits:
            with patch('finance_tracker.plans.get_limit', return_value=lim):
                # 1. Ground truth from UserProfile.is_recurring_locked
                expected_locked = [self.profile.is_recurring_locked(s) for s in subs]

                # 2. View context evaluation
                request = self.factory.get('/recurring/')
                request.user = self.user
                view = RecurringTransactionListView()
                view.request = request
                view.object_list = RecurringTransaction.objects.filter(user=self.user)
                ctx = view.get_context_data()

                actual_active_subs = ctx['active_subs']
                actual_locked = [s.is_locked for s in actual_active_subs]

                self.assertEqual(
                    actual_locked,
                    expected_locked,
                    f"Mismatch in lock status for tier limit {lim}: actual={actual_locked}, expected={expected_locked}"
                )
