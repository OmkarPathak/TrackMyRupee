import json
from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    Expense,
    Income,
    OnboardingEvent,
    OnboardingState,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from expenses.onboarding_v2 import compute_cycle_range, get_checklist_items


class OnboardingV2Tests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testonboard', password='password123')
        self.profile = self.user.profile
        self.client = Client()
        self.client.login(username='testonboard', password='password123')
        self.url = reverse('onboarding')

    def test_cycle_range_computation(self):
        # 1. Today after payday (e.g., target 2026-05-28, payday 25)
        # Cycle should run 2026-05-25 to 2026-06-24
        start, end, days_left = compute_cycle_range(25, target_date=date(2026, 5, 28))
        self.assertEqual(start, date(2026, 5, 25))
        self.assertEqual(end, date(2026, 6, 24))
        self.assertEqual(days_left, (date(2026, 6, 25) - date(2026, 5, 28)).days)

        # 2. Today before payday (e.g., target 2026-05-10, payday 25)
        # Cycle started on 2026-04-25, ends 2026-05-24
        start, end, days_left = compute_cycle_range(25, target_date=date(2026, 5, 10))
        self.assertEqual(start, date(2026, 4, 25))
        self.assertEqual(end, date(2026, 5, 24))
        self.assertEqual(days_left, (date(2026, 5, 25) - date(2026, 5, 10)).days)

        # 3. Payday 31 in February non-leap year (e.g., Feb 2025 has 28 days)
        start, end, _ = compute_cycle_range(31, target_date=date(2025, 2, 10))
        # Started on Jan 31, ends Feb 27
        self.assertEqual(start, date(2025, 1, 31))
        self.assertEqual(end, date(2025, 2, 27))

        # 4. Payday 31 in February leap year (2024 has 29 days)
        start, end, _ = compute_cycle_range(31, target_date=date(2024, 2, 10))
        self.assertEqual(start, date(2024, 1, 31))
        self.assertEqual(end, date(2024, 2, 28))

        # 5. Last day of month (payday = 32 sentinel)
        start, end, days_left = compute_cycle_range(32, target_date=date(2026, 5, 15))
        # Started on April 30, ends May 30
        self.assertEqual(start, date(2026, 4, 30))
        self.assertEqual(end, date(2026, 5, 30))

        # 6. Year boundary (December to January)
        start, end, _ = compute_cycle_range(25, target_date=date(2026, 12, 28))
        self.assertEqual(start, date(2026, 12, 25))
        self.assertEqual(end, date(2027, 1, 24))

    def test_step1_persona_selection(self):
        payload = {'action': 'step1', 'persona': 'FREELANCER'}
        resp = self.client.post(self.url, json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.persona, 'FREELANCER')

        state = OnboardingState.objects.get(user=self.user)
        self.assertEqual(state.current_step, 2)
        self.assertEqual(state.persona, 'FREELANCER')
        self.assertFalse(state.step1_skipped)

    def test_step1_skip(self):
        payload = {'action': 'step1', 'skip': True}
        resp = self.client.post(self.url, json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.persona)

        state = OnboardingState.objects.get(user=self.user)
        self.assertTrue(state.step1_skipped)
        self.assertEqual(state.current_step, 2)

    def test_step2_atomic_and_balance_correctness(self):
        """
        CRITICAL requirement:
        User types balance 35,000 and salary 50,000.
        Account balance must display exactly 35,000, while salary income of 50,000 is logged.
        """
        payload = {
            'action': 'step2',
            'payday': 25,
            'salary_amount': '50,000',
            'balance_now': '35,000',
            'bank_choice': 'SBI',
            'auto_log_salary': True,
        }
        resp = self.client.post(self.url, json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        state = OnboardingState.objects.get(user=self.user)
        self.assertIsNotNone(state.account)
        self.assertIsNotNone(state.income)
        self.assertIsNotNone(state.recurring_transaction)

        # Check account name & type
        self.assertEqual(state.account.name, 'SBI Savings Account')
        self.assertEqual(state.account.account_type, 'SAVINGS_ACCOUNT')

        # Balance correctness
        self.assertEqual(state.account.balance, Decimal('35000.00'))
        self.assertEqual(state.income.amount, Decimal('50000.00'))
        self.assertEqual(state.recurring_transaction.amount, Decimal('50000.00'))

    def test_step2_idempotency_back_and_resubmit(self):
        """
        Submit step 2, go back, change values to 60,000 and HDFC, resubmit.
        Must update existing account/income/recurring objects, NEVER duplicate.
        """
        payload1 = {
            'action': 'step2',
            'payday': 25,
            'salary_amount': '50000',
            'balance_now': '35000',
            'bank_choice': 'SBI',
            'auto_log_salary': True,
        }
        self.client.post(self.url, json.dumps(payload1), content_type='application/json')
        self.assertEqual(Account.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 1)

        # Re-submit with changed values
        payload2 = {
            'action': 'step2',
            'payday': 28,
            'salary_amount': '60000',
            'balance_now': '40000',
            'bank_choice': 'HDFC',
            'auto_log_salary': True,
        }
        self.client.post(self.url, json.dumps(payload2), content_type='application/json')

        # Counts must STILL be 1 each (no duplicates)
        self.assertEqual(Account.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 1)

        acc = Account.objects.get(user=self.user)
        self.assertEqual(acc.name, 'HDFC Savings Account')
        self.assertEqual(acc.balance, Decimal('40000.00'))

        inc = Income.objects.get(user=self.user)
        self.assertEqual(inc.amount, Decimal('60000.00'))

    def test_step3_parse_dry_run_and_confirm(self):
        """
        Quick-add parser dry-run does NOT persist.
        Confirming step 3 persists exactly once into step 2's account.
        """
        # Step 2 setup first
        self.client.post(
            self.url,
            json.dumps({'action': 'step2', 'bank_choice': 'SBI', 'balance_now': '1000', 'salary_amount': '50000'}),
            content_type='application/json',
        )

        # Dry-run parse
        parse_resp = self.client.post(
            reverse('parse-expense'),
            json.dumps({'text': 'swiggy dinner 450'}),
            content_type='application/json',
        )
        self.assertEqual(parse_resp.status_code, 200)
        parse_data = parse_resp.json()
        self.assertTrue(parse_data['success'])
        self.assertEqual(parse_data['data']['amount'], '450.00')

        # Verify nothing persisted to database
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)

        # Confirm step 3
        confirm_resp = self.client.post(
            self.url,
            json.dumps({
                'action': 'step3_confirm',
                'amount': '450',
                'description': 'Swiggy dinner',
                'category': 'Food & Dining',
                'date': '2026-10-07',
            }),
            content_type='application/json',
        )
        self.assertEqual(confirm_resp.status_code, 200)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)
        exp = Expense.objects.get(user=self.user)
        self.assertEqual(exp.amount, Decimal('450.00'))
        self.assertEqual(exp.description, 'Swiggy dinner')

    def test_finish_onboarding_and_dashboard_checklist(self):
        """
        Completing onboarding marks tutorial completed and sets onboarding_completed_at.
        Checklist is displayed on dashboard for this new user.
        """
        resp = self.client.post(self.url, json.dumps({'action': 'finish'}), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.has_seen_tutorial)
        self.assertIsNotNone(self.profile.onboarding_completed_at)

        # Dashboard view should render with checklist active
        dash_resp = self.client.get(reverse('home'))
        self.assertEqual(dash_resp.status_code, 200)
        self.assertTrue(dash_resp.context.get('show_onboarding_checklist'))

    def test_skip_paths_missing_checklist_items(self):
        """
        If user skips step 2, missing items ('Set your payday', 'Add your salary', 'Add an account')
        are surfaced first in checklist.
        """
        state = OnboardingState.objects.create(user=self.user, step2_skipped=True)
        items, done_count, total = get_checklist_items(self.user)
        item_ids = [item['id'] for item in items]
        self.assertIn('set_payday', item_ids)
        self.assertIn('add_salary', item_ids)
        self.assertIn('add_account', item_ids)
        self.assertIn('setup_basics', item_ids)

    def test_analytics_privacy_safety(self):
        """
        OnboardingEvent never records sensitive amounts, balances, descriptions, or account names.
        """
        self.client.post(
            self.url,
            json.dumps({
                'action': 'step2',
                'salary_amount': '50000',
                'balance_now': '35000',
                'bank_choice': 'SBI',
            }),
            content_type='application/json',
        )
        events = OnboardingEvent.objects.filter(user=self.user)
        for ev in events:
            props = ev.properties
            self.assertNotIn('amount', props)
            self.assertNotIn('salary', props)
            self.assertNotIn('balance', props)
            self.assertNotIn('description', props)
