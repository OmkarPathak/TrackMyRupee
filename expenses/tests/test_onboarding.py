from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from expenses.models import UserProfile, Income, Expense, Category, Account, RecurringTransaction
from datetime import date
import json

class OnboardingViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='onboarduser', password='password')
        self.client = Client()
        self.client.login(username='onboarduser', password='password')
        self.url = reverse('onboarding')

    def test_onboarding_access_unauthenticated(self):
        """Unauthenticated users should be redirected to login, not crash."""
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_onboarding_redirection_new_user(self):
        """New users should be redirected to onboarding from home."""
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('onboarding'), response.url)

    def test_onboarding_redirection_existing_user(self):
        """Users who have seen tutorial should be redirected away."""
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

    def test_onboarding_step_accounts(self):
        data = {
            'step': 'accounts',
            'accounts': [
                {'name': 'SBI Savings Account', 'type': 'BANK', 'balance': 1000},
                {'name': 'ICICI Coral Credit Card', 'type': 'CREDIT_CARD', 'balance': 0}
            ]
        }
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 2)
        self.assertFalse(self.user.profile.has_seen_tutorial)

    def test_onboarding_step_income(self):
        # Need an account first
        acc = Account.objects.create(user=self.user, name='SBI Savings Account', balance=0)
        data = {'step': 'income', 'amount': 50000, 'source': 'Salary', 'account_id': acc.id}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.get(user=self.user).amount, 50000)

    def test_onboarding_step_expense(self):
        # Need an account first
        acc = Account.objects.create(user=self.user, name='Cash', balance=1000)
        data = {'step': 'expense', 'amount': 1200, 'description': 'Big Bazaar', 'category': 'Groceries', 'account_id': acc.id}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)

    def test_onboarding_step_recurring(self):
        data = {
            'step': 'recurring',
            'recurring': [
                {'description': 'Rent', 'amount': 12000, 'category': 'Rent', 'frequency': 'MONTHLY', 'type': 'EXPENSE'},
                {'description': 'Netflix', 'amount': 499, 'category': 'Subscriptions', 'frequency': 'MONTHLY', 'type': 'EXPENSE'}
            ]
        }
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 2)

    def test_onboarding_finish(self):
        data = {'step': 'finish'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.has_seen_tutorial)

    def test_onboarding_skip(self):
        data = {'step': 'skip'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.has_seen_tutorial)
