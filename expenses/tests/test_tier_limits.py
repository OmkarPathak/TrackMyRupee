import json
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
        if limit == -1: return # Unlimited
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
        if limit == -1: return # Unlimited
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
        if limit == -1: return
        # User starts with default categories via signals
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
        if limit == -1: return
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
        """Free user should respect recurring transaction limits."""
        limit = PLAN_DETAILS['FREE']['limits']['recurring_transactions']
        if limit == -1: return
        acc = Account.objects.create(user=self.user, name="Main", balance=1000)
        
        # Fill up to limit
        for i in range(limit):
             RecurringTransaction.objects.create(
                user=self.user, account=acc, description=f"Rec {i}", 
                amount=100, category="Test", frequency="MONTHLY", start_date=date.today()
            )
            
        # Try one more
        response = self.client.post(reverse('recurring-create'), {
            'account': acc.id, 'description': 'Limit Exceeded Rec', 'amount': '100',
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
        if limit == -1: return
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
        if limit == -1: return
        today = date.today()
        for i in range(limit):
            Expense.objects.create(
                user=self.user, date=today, amount=Decimal("10.00"),
                description=f"Expense {i}", category="Food"
            )
        
        data = {
            'key': 'limit-1', 'date': today.strftime('%Y-%m-%d'), 'amount': '20.00',
            'description': 'Limit Exceeded Expense', 'category': 'Food', 'currency': '₹',
            'payment_method': 'Cash',
        }
        response = self.client.post(reverse('expense-composer-save'), json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['code'], 'limit')
        self.assertEqual(response.json()['upgrade_url'], reverse('pricing'))
        self.assertEqual(Expense.objects.filter(user=self.user, date__year=today.year, date__month=today.month).count(), limit)

    def test_net_worth_locked_status(self):
        """Dashboard should reflect net worth locked status from PLAN_DETAILS."""
        from finance_tracker.plans import PLAN_DETAILS
        
        for tier in ['FREE', 'PLUS', 'PRO']:
            self.profile.tier = tier
            if tier != 'FREE':
                self.profile.subscription_end_date = timezone.now() + timedelta(days=30)
            else:
                self.profile.subscription_end_date = None
            self.profile.save()
            
            response = self.client.get(reverse('home'))
            expected_locked = not PLAN_DETAILS[tier]['limits']['net_worth']
            self.assertEqual(response.context['is_net_worth_locked'], expected_locked, f"Net worth locked status mismatch for tier {tier}")
