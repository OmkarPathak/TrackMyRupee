from datetime import date, timedelta

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from expenses.models import Category, Expense, Income, RecurringTransaction, UserProfile


class ExpenseModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        
    def test_expense_creation(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date(2023, 1, 1),
            amount=100.50,
            description='Test Expense',
            category='Food'
        )
        self.assertEqual(str(expense), "2023-01-01 - Test Expense - 100.5")
        
    def test_expense_category_strip_whitespace(self):
        # The save method strips whitespace from category
        expense = Expense.objects.create(
            user=self.user,
            date=date(2023, 1, 1),
            amount=100,
            description='Test',
            category='  Food  '
        )
        self.assertEqual(expense.category, 'Food')

class IncomeModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')

    def test_income_source_strip_whitespace(self):
        income = Income.objects.create(
            user=self.user,
            date=date(2023, 1, 1),
            amount=5000,
            source='  Salary  '
        )
        self.assertEqual(income.source, 'Salary')

class RecurringTransactionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.rt = RecurringTransaction(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='Rent',
            frequency='MONTHLY',
            start_date=date(2023, 1, 31)
        )

    def test_get_next_date_monthly_end_of_month(self):
        # Jan 31 -> Feb 28 (or 29)
        next_date = RecurringTransaction.get_next_date(date(2023, 1, 31), 'MONTHLY')
        self.assertEqual(next_date, date(2023, 2, 28))
    
    def test_get_next_date_monthly_leap_year(self):
        # Jan 31 2024 -> Feb 29 2024
        next_date = RecurringTransaction.get_next_date(date(2024, 1, 31), 'MONTHLY')
        self.assertEqual(next_date, date(2024, 2, 29))

    def test_get_next_date_yearly_leap_year(self):
        # Feb 29 2024 -> Feb 28 2025
        next_date = RecurringTransaction.get_next_date(date(2024, 2, 29), 'YEARLY')
        self.assertEqual(next_date, date(2025, 2, 28))

    def test_get_next_date_daily(self):
        next_date = RecurringTransaction.get_next_date(date(2023, 1, 1), 'DAILY')
        self.assertEqual(next_date, date(2023, 1, 2))

    def test_get_next_date_weekly(self):
        next_date = RecurringTransaction.get_next_date(date(2023, 1, 1), 'WEEKLY')
        self.assertEqual(next_date, date(2023, 1, 8))

    def test_duplicate_source_fk_validation_raises_error(self):
        cat = Category.objects.create(user=self.user, name='Dividend Income')
        rt1 = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INCOME',
            amount=500,
            description='Monthly Dividend 1',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            source_fk=cat,
        )
        rt2 = RecurringTransaction(
            user=self.user,
            transaction_type='INCOME',
            amount=700,
            description='Monthly Dividend 2',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            source_fk=cat,
        )
        with self.assertRaises(ValidationError) as ctx:
            rt2.full_clean()
        self.assertIn('source_fk', ctx.exception.message_dict)

    def test_legacy_source_backward_compatibility(self):
        rt_legacy = RecurringTransaction(
            user=self.user,
            transaction_type='INCOME',
            amount=1000,
            description='Legacy Income',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            source='Legacy String Source',
            source_fk=None,
        )
        rt_legacy.full_clean()
        rt_legacy.save()
        self.assertEqual(rt_legacy.source, 'Legacy String Source')
        self.assertIsNone(rt_legacy.source_fk)

    def test_save_syncs_source_from_source_fk(self):
        cat = Category.objects.create(user=self.user, name='Interest Source')
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INCOME',
            amount=250,
            description='Interest Payment',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            source_fk=cat,
        )
        self.assertEqual(rt.source, 'Interest Source')


class UserProfileTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        # UserProfile is usually created via signal, but if not we create manually
        # Assuming signal exists or manual creation if test environment bypasses signals
        if not hasattr(self.user, 'profile'):
             UserProfile.objects.create(user=self.user)
        self.profile = self.user.profile

    def test_default_tier(self):
        self.assertEqual(self.profile.tier, 'FREE')
        self.assertFalse(self.profile.is_pro)
        self.assertFalse(self.profile.is_plus)

    def test_pro_tier_logic(self):
        self.profile.tier = 'PRO'
        self.profile.subscription_end_date = timezone.now() + timedelta(days=30)
        self.profile.save()
        self.assertTrue(self.profile.is_pro)
        self.assertTrue(self.profile.is_plus)

    def test_expired_subscription(self):
        self.profile.tier = 'PRO'
        self.profile.subscription_end_date = timezone.now() - timedelta(days=1)
        self.profile.save()
        self.assertFalse(self.profile.is_pro)
        # Assuming is_plus also checks expiration logic similarly
        self.assertFalse(self.profile.is_plus)
