from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.cache_utils import suppress_dashboard_cache_invalidation
from expenses.models import (
    Account,
    Expense,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    SubscriptionPlan,
    UserProfile,
)
from expenses.views.mixins import process_user_recurring_transactions


class TestLoanInterestRateNPlusOne(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='test_loan_perf', password='password')
        self.user.profile.tier = 'PRO'
        self.user.profile.consent_granted = True
        self.user.profile.save()
        self.account = Account.objects.create(
            user=self.user,
            name='Salary Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('100000.00'),
        )

    def test_loan_interest_rate_queries_do_not_scale_with_number_of_loans(self):
        """Item 2: Assert loan interest rate queries are pre-fetched in bulk rather than N queries."""
        today = date.today()
        loans = []
        for i in range(4):
            loan = Loan.objects.create(
                user=self.user,
                name=f'Loan {i}',
                loan_type='PERSONAL',
                repayment_type='EMI',
                initial_principal=Decimal('100000.00'),
                duration_months=12,
                start_date=today - timedelta(days=60),
            )
            # Create two rates to test ordering by -effective_date
            LoanInterestRate.objects.create(
                loan=loan,
                interest_rate=Decimal('12.00'),
                effective_date=today - timedelta(days=60),
            )
            LoanInterestRate.objects.create(
                loan=loan,
                interest_rate=Decimal('10.50'),
                effective_date=today - timedelta(days=10),
            )
            RecurringTransaction.objects.create(
                user=self.user,
                transaction_type='LOAN',
                loan=loan,
                account=self.account,
                amount=Decimal('5000.00'),
                description=f'Loan EMI {i}',
                frequency='MONTHLY',
                start_date=today - timedelta(days=30),
                is_active=True,
            )
            loans.append(loan)

        with CaptureQueriesContext(connection) as ctx:
            process_user_recurring_transactions(self.user, force=True, max_catchup=1)

        # Count queries against expenses_loaninterestrate
        interest_rate_queries = [
            q['sql'] for q in ctx.captured_queries
            if 'expenses_loaninterestrate' in q['sql'].lower()
        ]
        self.assertLessEqual(
            len(interest_rate_queries),
            1,
            f"Expected at most 1 query for LoanInterestRate, found {len(interest_rate_queries)}: {interest_rate_queries}",
        )
        # Verify repayments were successfully created for each loan with correct rates
        self.assertEqual(LoanRepayment.objects.filter(loan__in=loans).count(), 4)


class TestCatchUpCapAndCooldown(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='test_catchup_perf', password='password')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True)
        self.account = Account.objects.create(
            user=self.user,
            name='Daily Cash',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('100000.00'),
        )

    def test_request_catchup_capped_and_cooldown_not_set_if_backlog_remains(self):
        """Item 3: Overdue recurring transaction by 20 days is capped to max_catchup=2, and cooldown key is not set."""
        today = date.today()
        # 20 days ago
        start_date = today - timedelta(days=20)
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            account=self.account,
            amount=Decimal('100.00'),
            description='Daily Coffee',
            frequency='DAILY',
            start_date=start_date,
            is_active=True,
        )

        with patch('sys.argv', ['manage.py', 'runserver']):  # simulate non-test environment
            process_user_recurring_transactions(self.user, force=False, max_catchup=2)

        # Capped to exactly 2 expenses created in this run
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 2)

        # RT should still have backlog (last_processed_date is only start_date + 1 day)
        rt.refresh_from_db()
        self.assertLess(rt.last_processed_date, today)

        # Cooldown key must NOT be set because backlog remains
        cooldown_key = f'recurring_processed_{self.user.id}_{today}'
        self.assertIsNone(cache.get(cooldown_key))

        # A second run processes the next 2
        with patch('sys.argv', ['manage.py', 'runserver']):
            process_user_recurring_transactions(self.user, force=False, max_catchup=2)

        self.assertEqual(Expense.objects.filter(user=self.user).count(), 4)


class TestDashboardCacheInvalidationSuppression(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='test_suppress_perf', password='password')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True)
        self.account = Account.objects.create(
            user=self.user,
            name='Main Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('100000.00'),
        )

    def test_cache_delete_many_suppressed_during_batch_block(self):
        """Item 4: suppress_dashboard_cache_invalidation suppresses intermediate deletes."""
        today = date.today()
        for i in range(3):
            RecurringTransaction.objects.create(
                user=self.user,
                transaction_type='EXPENSE',
                account=self.account,
                amount=Decimal('50.00'),
                description=f'Expense {i}',
                frequency='DAILY',
                start_date=today,
                is_active=True,
            )

        with patch('django.core.cache.cache.delete_many') as mock_delete_many:
            with suppress_dashboard_cache_invalidation(invalidate_on_exit=False):
                process_user_recurring_transactions(self.user, force=True, max_catchup=2)

            # Inside the block with invalidate_on_exit=False, delete_many should NOT have been called
            mock_delete_many.assert_not_called()

        # If invalidate_on_exit=True, delete_many should be called exactly once
        with patch('django.core.cache.cache.delete_many') as mock_delete_many:
            with suppress_dashboard_cache_invalidation(user_id=self.user.id, invalidate_on_exit=True):
                # Save an expense
                Expense.objects.create(
                    user=self.user,
                    amount=Decimal('10.00'),
                    date=today,
                    description='Single item',
                )
            mock_delete_many.assert_called_once()


class TestPricingPerformanceAndCaching(TestCase):
    def setUp(self):
        cache.clear()
        SubscriptionPlan.objects.all().delete()
        SubscriptionPlan.objects.create(
            tier='PLUS', duration='MONTHLY', name='Plus Monthly', price=Decimal('99.00'), is_active=True
        )
        SubscriptionPlan.objects.create(
            tier='PLUS', duration='YEARLY', name='Plus Yearly', price=Decimal('999.00'), is_active=True
        )
        SubscriptionPlan.objects.create(
            tier='PRO', duration='MONTHLY', name='Pro Monthly', price=Decimal('199.00'), is_active=True
        )
        SubscriptionPlan.objects.create(
            tier='PRO', duration='YEARLY', name='Pro Yearly', price=Decimal('1999.00'), is_active=True
        )
        self.user = User.objects.create_user(username='test_pricing_user', password='password')
        self.user.profile.tier = 'PRO'
        self.user.profile.save()

    def test_pricing_view_executes_single_subscription_plan_query(self):
        """Item 7: SubscriptionPlan query is deduplicated to 1 query instead of 2."""
        client = Client()
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse('pricing'))
        self.assertEqual(response.status_code, 200)

        plan_queries = [
            q['sql'] for q in ctx.captured_queries
            if 'expenses_subscriptionplan' in q['sql'].lower()
        ]
        self.assertEqual(
            len(plan_queries),
            1,
            f"Expected exactly 1 SubscriptionPlan query, got {len(plan_queries)}: {plan_queries}",
        )

    def test_anonymous_pricing_request_is_cached(self):
        """Item 8: Second anonymous request to /pricing/ hits cache and executes zero plan queries."""
        client = Client()
        # First request populates cache
        resp1 = client.get(reverse('pricing'))
        self.assertEqual(resp1.status_code, 200)

        # Second request hits cache
        with CaptureQueriesContext(connection) as ctx:
            resp2 = client.get(reverse('pricing'))
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp1.content, resp2.content)

        # Should issue 0 database queries on warm cache
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_authenticated_pricing_request_bypasses_anonymous_cache(self):
        """Item 8: Authenticated user is not served from anonymous cache and reflects user status."""
        client = Client()
        # Seed anonymous cache
        client.get(reverse('pricing'))

        # Login and request
        client.force_login(self.user)
        resp = client.get(reverse('pricing'))
        self.assertEqual(resp.status_code, 200)
        # Should reflect that Pro is user's active tier
        self.assertContains(resp, "Current Plan")

    def test_authenticated_pricing_renders_sidebar(self):
        """Pricing page includes sidebar for authenticated users, but omits it for anonymous users."""
        client = Client()
        anon_resp = client.get(reverse('pricing'))
        self.assertNotContains(anon_resp, 'id="sidebar"')
        self.assertNotContains(anon_resp, 'has-sidebar')

        client.force_login(self.user)
        auth_resp = client.get(reverse('pricing'))
        self.assertContains(auth_resp, 'id="sidebar"')
        self.assertContains(auth_resp, 'has-sidebar')
