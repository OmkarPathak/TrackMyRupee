import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import (
    Account,
    Expense,
    Income,
    RecurringTransaction,
)


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

    def test_onboarding_step_accounts_updates_existing_by_name(self):
        existing = Account.objects.create(
            user=self.user,
            name='SBI Savings Account',
            account_type='BANK',
            balance=0,
            currency='₹',
        )
        data = {
            'step': 'accounts',
            'accounts': [
                {'name': 'SBI Savings Account', 'type': 'BANK', 'balance': 2500},
                {'name': 'Cash Wallet', 'type': 'CASH', 'balance': 300},
            ],
        }

        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)

        existing.refresh_from_db()
        self.assertEqual(existing.balance, 2500)
        self.assertEqual(Account.objects.filter(user=self.user, name='SBI Savings Account').count(), 1)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 2)

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

    def test_onboarding_step_persona(self):
        data = {'step': 'persona', 'persona': 'SALARIED'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.persona, 'SALARIED')

    def test_onboarding_step_salary_setup(self):
        self.user.profile.persona = 'SALARIED'
        self.user.profile.save()
        data = {'step': 'salary_setup', 'salary_date': 25}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.salary_date, 25)

    def test_onboarding_dismiss_checklist(self):
        data = {'step': 'dismiss_checklist'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.dismissed_onboarding_checklist)

    @patch('expenses.views.auth.Expense.objects.create')
    def test_onboarding_currency_conversion_failure_returns_clean_error(self, mock_create):
        """Currency conversion or domain validation error returns user-friendly error message."""
        mock_create.side_effect = RuntimeError("Failed to fetch exchange rate for USD to INR")
        data = {'step': 'expense', 'amount': 100, 'description': 'Coffee', 'category': 'Food'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertFalse(res_data['success'])
        self.assertIn("currency conversion failed or data is invalid", res_data['error'])
        self.assertNotIn("USD to INR", res_data['error'])

    @patch('expenses.views.auth.Expense.objects.create')
    def test_onboarding_unexpected_exception_returns_generic_message_without_leaking(self, mock_create):
        """Unexpected internal exceptions return a generic safe message without leaking internals."""
        mock_create.side_effect = Exception("psycopg2.OperationalError: password authentication failed for user 'postgres'")
        data = {'step': 'expense', 'amount': 100, 'description': 'Coffee', 'category': 'Food'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        res_data = response.json()
        self.assertFalse(res_data['success'])
        self.assertEqual(res_data['error'], "Something went wrong, please try again.")
        self.assertNotIn("psycopg2", res_data['error'])
        self.assertNotIn("postgres", res_data['error'])

    def test_onboarding_empty_amount_does_not_raise_conversion_syntax_error(self):
        """Empty string or formatted currency input does not crash with Decimal ConversionSyntax."""
        # Test income with empty string
        data = {'step': 'income', 'amount': '', 'source': 'Freelance'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        income = Income.objects.filter(user=self.user, source='Freelance').first()
        self.assertIsNotNone(income)
        self.assertEqual(income.amount, Decimal('0.00'))

        # Test accounts with formatted strings containing commas and currency symbols
        data_acc = {
            'step': 'accounts',
            'accounts': [
                {'name': 'Checking', 'type': 'CHECKING_ACCOUNT', 'balance': '₹ 15,000.50'},
                {'name': 'EmptyBal', 'type': 'SAVINGS_ACCOUNT', 'balance': ''},
            ]
        }
        res_acc = self.client.post(self.url, json.dumps(data_acc), content_type='application/json')
        self.assertEqual(res_acc.status_code, 200)
        acc = Account.objects.get(user=self.user, name='Checking')
        self.assertEqual(acc.balance, Decimal('15000.50'))
        acc_empty = Account.objects.get(user=self.user, name='EmptyBal')
        self.assertEqual(acc_empty.balance, Decimal('0.00'))

        # Test expense with empty string
        data_exp = {'step': 'expense', 'amount': '', 'description': 'Snack'}
        res_exp = self.client.post(self.url, json.dumps(data_exp), content_type='application/json')
        self.assertEqual(res_exp.status_code, 200)
        exp = Expense.objects.filter(user=self.user, description='Snack').first()
        self.assertIsNotNone(exp)
        self.assertEqual(exp.amount, Decimal('0.00'))
