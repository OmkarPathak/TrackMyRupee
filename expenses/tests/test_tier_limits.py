from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    Category,
    Expense,
    RecurringTransaction,
    UserProfile,
)
from finance_tracker.plans import PLAN_DETAILS


class TierLimitTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="freetester", password="password")
        # Ensure profile exists (signals might be skipped in some test setups)
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.tier = "FREE"
        self.profile.has_seen_tutorial = True
        self.profile.save()
        
        self.client = Client()
        self.client.login(username="freetester", password="password")

    def test_account_limit_enforced_free(self):
        """Free user should not be able to create more than the configured account limit."""
        limit = PLAN_DETAILS['FREE']['limits']['accounts']
        for i in range(limit):
            Account.objects.create(user=self.user, name=f"Acc {i}", balance=100)
        
        self.assertEqual(self.user.accounts.count(), limit)
        
        response = self.client.post(reverse('account-create'), {
            'name': 'Limit Exceeded Account', 'account_type': 'BANK', 'balance': '100', 'currency': '₹'
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(self.user.accounts.count(), limit)

    def test_account_limit_enforced_plus(self):
        """Plus user should not be able to create more than the configured account limit."""
        self.profile.tier = "PLUS"
        self.profile.subscription_end_date = timezone.now().date() + timedelta(days=30)
        self.profile.save()

        limit = PLAN_DETAILS['PLUS']['limits']['accounts']
        for i in range(limit):
            Account.objects.create(user=self.user, name=f"Acc {i}", balance=100)
        
        self.assertEqual(self.user.accounts.count(), limit)
        
        response = self.client.post(reverse('account-create'), {
            'name': 'Limit Exceeded Account', 'account_type': 'BANK', 'balance': '100', 'currency': '₹'
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(self.user.accounts.count(), limit)

    def test_category_limit_enforced_free(self):
        """Free user should not be able to create more than the configured category limit."""
        limit = PLAN_DETAILS['FREE']['limits']['budget_categories']
        # User starts with 3 default categories (Food, Shopping, Bills) via signals
        current_count = Category.objects.filter(user=self.user).count()
        
        # Add categories up to limit
        for i in range(limit - current_count):
            Category.objects.create(user=self.user, name=f"Cat {i}")

        self.assertEqual(Category.objects.filter(user=self.user).count(), limit)
        
        response = self.client.post(reverse('category-create'), {'name': 'Limit Exceeded Cat'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(Category.objects.filter(user=self.user).count(), limit)

    def test_category_limit_enforced_plus(self):
        """Plus user should be capped at the configured category limit."""
        self.profile.tier = "PLUS"
        self.profile.subscription_end_date = timezone.now().date() + timedelta(days=30)
        self.profile.save()
        
        limit = PLAN_DETAILS['PLUS']['limits']['budget_categories']
        current_count = Category.objects.filter(user=self.user).count()

        # Add categories up to limit
        for i in range(limit - current_count):
            Category.objects.create(user=self.user, name=f"Plus Extra {i}")
        
        self.assertEqual(Category.objects.filter(user=self.user).count(), limit)
        
        response = self.client.post(reverse('category-create'), {'name': 'Limit Exceeded Cat'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(Category.objects.filter(user=self.user).count(), limit)

    def test_recurring_limit_enforced_free(self):
        """Free user should not be able to create any recurring transactions."""
        acc = Account.objects.create(user=self.user, name="Main", balance=1000)
        response = self.client.post(reverse('recurring-create'), {
            'account': acc.id, 'description': 'Monthly Rent', 'amount': '5000',
            'category': 'Rent', 'frequency': 'MONTHLY', 'start_date': date.today()
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)

    def test_recurring_limit_enforced_plus(self):
        """Plus user should be capped at the configured recurring transaction limit."""
        self.profile.tier = "PLUS"
        self.profile.subscription_end_date = timezone.now().date() + timedelta(days=30)
        self.profile.save()
        acc = Account.objects.create(user=self.user, name="Main", balance=1000)

        limit = PLAN_DETAILS['PLUS']['limits']['recurring_transactions']
        for i in range(limit):
            RecurringTransaction.objects.create(
                user=self.user, account=acc, description=f"Rec {i}", 
                amount=100, category="Test", frequency="MONTHLY", start_date=date.today()
            )
        
        response = self.client.post(reverse('recurring-create'), {
            'account': acc.id, 'description': 'Limit Exceeded Rec', 'amount': '100',
            'category': 'Test', 'frequency': 'MONTHLY', 'start_date': date.today()
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, is_active=True).count(), limit)

    def test_expense_monthly_limit_enforced(self):
        """Free user should not be able to create more than the configured monthly expense limit."""
        limit = PLAN_DETAILS['FREE']['limits']['expenses_per_month']
        today = date.today()
        for i in range(limit):
            Expense.objects.create(
                user=self.user, date=today, amount=Decimal("10.00"),
                description=f"Expense {i}", category="Food"
            )
        
        data = {
            'form-TOTAL_FORMS': '1', 'form-INITIAL_FORMS': '0', 'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000', 'form-0-date': today.strftime('%Y-%m-%d'),
            'form-0-amount': '20.00', 'form-0-description': 'Limit Exceeded Expense',
            'form-0-category': 'Food', 'form-0-currency': '₹', 'form-0-payment_method': 'Cash'
        }
        response = self.client.post(reverse('expense-create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        self.assertEqual(Expense.objects.filter(user=self.user, date__year=today.year, date__month=today.month).count(), limit)

    def test_net_worth_locked_for_free_user(self):
        """Dashboard should indicate net worth is locked for free users."""
        response = self.client.get(reverse('home'))
        self.assertTrue(response.context['is_net_worth_locked'])

    def test_net_worth_unlocked_for_plus_user(self):
        """Dashboard should NOT indicate net worth is locked for plus users."""
        self.profile.tier = "PLUS"
        self.profile.subscription_end_date = timezone.now().date() + timedelta(days=30)
        self.profile.save()
        response = self.client.get(reverse('home'))
        self.assertFalse(response.context['is_net_worth_locked'])
