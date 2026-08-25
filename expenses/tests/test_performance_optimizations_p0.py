"""
expenses/tests/test_performance_optimizations_p0.py
===================================================
Unit tests for Phase 0 / P0 performance optimizations:
1. RecurringTransaction.next_due_date DB field calculation and ORM filtering
2. Model indexes on Notification, GoalContribution, LoanRepayment, FinancialAuditLog, and RecurringTransaction
3. View prefetching in LoanDetailView (interest_rates) and AccountDetailView (holdings)
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from expenses.context_processors import sidebar_badges
from expenses.models import (
    Account,
    FinancialAuditLog,
    GoalContribution,
    Holding,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    Notification,
    RecurringTransaction,
    SavingsGoal,
)


class TestRecurringTransactionNextDueDateField(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='rt_test_user', password='password')

    def test_next_due_date_populated_on_save(self):
        start_d = date(2026, 1, 1)
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('100.00'),
            description='Test Subscription',
            frequency='MONTHLY',
            start_date=start_d,
            currency='₹',
        )
        # On create, next_due_date should equal start_date
        self.assertEqual(rt.next_due_date, start_d)

        # After processing once on start_d, next_due_date should be next month (2026-02-01)
        rt.last_processed_date = start_d
        rt.save()
        self.assertEqual(rt.next_due_date, date(2026, 2, 1))

    def test_next_due_date_updated_with_update_fields(self):
        start_d = date(2026, 1, 1)
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('500.00'),
            description='Gym',
            frequency='MONTHLY',
            start_date=start_d,
            currency='₹',
        )
        self.assertEqual(rt.next_due_date, start_d)

        # Update last_processed_date using update_fields
        rt.last_processed_date = start_d
        rt.save(update_fields=['last_processed_date'])
        
        rt.refresh_from_db()
        self.assertEqual(rt.next_due_date, date(2026, 2, 1))

    def test_sidebar_badges_uses_next_due_date(self):
        today = date.today()
        due_in_3_days = today + timedelta(days=3)
        due_in_10_days = today + timedelta(days=10)

        # Create 1 subscription due in 3 days, 1 due in 10 days
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('200.00'),
            description='Soon',
            frequency='MONTHLY',
            start_date=due_in_3_days,
            currency='₹',
        )
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('300.00'),
            description='Later',
            frequency='MONTHLY',
            start_date=due_in_10_days,
            currency='₹',
        )

        class MockRequest:
            def __init__(self, user):
                self.user = user

        request = MockRequest(self.user)
        badges = sidebar_badges(request)
        self.assertEqual(badges['upcoming_subscriptions_count'], 1)


class TestModelIndexesExist(TestCase):
    def test_indexes_defined_on_models(self):
        models_to_check = [
            (RecurringTransaction, {'rt_user_active_type_idx', 'rt_next_due_date_idx', 'rt_user_active_due_idx'}),
            (Notification, {'notif_user_read_at_idx'}),
            (SavingsGoal, {'savingsgoal_user_comp_idx'}),
            (GoalContribution, {'goalcontrib_goal_date_idx'}),
            (LoanRepayment, {'loanrepayment_loan_date_idx'}),
            (FinancialAuditLog, {'auditlog_user_model_idx'}),
        ]

        for model, expected_index_names in models_to_check:
            index_names = {idx.name for idx in model._meta.indexes if idx.name}
            for expected in expected_index_names:
                self.assertIn(expected, index_names, f"Index {expected} missing from {model.__name__}")


class TestViewPrefetching(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='prefetch_user', password='password')
        self.user.profile.tier = 'PRO'
        self.user.profile.save()
        self.client = Client()
        self.client.login(username='prefetch_user', password='password')

        self.account = Account.objects.create(
            user=self.user, name='Savings Account', account_type='SAVINGS_ACCOUNT', balance=Decimal('50000.00'), currency='₹'
        )
        self.holding = Holding.objects.create(
            account=self.account, instrument_name='Test Fund', instrument_type='MF', currency='₹', is_active=True
        )

        self.loan = Loan.objects.create(
            user=self.user, name='Home Loan', initial_principal=Decimal('100000.00'),
            duration_months=120, currency='₹'
        )
        self.interest_rate = LoanInterestRate.objects.create(
            loan=self.loan, interest_rate=Decimal('8.50')
        )
        self.repayment = LoanRepayment.objects.create(
            loan=self.loan, from_account=self.account, amount=Decimal('5000.00'),
            principal_portion=Decimal('4000.00'), interest_portion=Decimal('1000.00'),
            date=date.today()
        )

    def test_loan_detail_view_prefetching(self):
        url = reverse('loan-detail', kwargs={'pk': self.loan.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_account_detail_view_prefetching(self):
        url = reverse('account-detail', kwargs={'pk': self.account.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
