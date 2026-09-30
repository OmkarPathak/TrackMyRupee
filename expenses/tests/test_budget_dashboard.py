from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Category, Expense
from expenses.views.dashboard import calculate_budget_pacing


class BudgetPacingHelperTest(TestCase):
    def test_pacing_current_month_on_track(self):
        # 30-day month (April 2026), day 15 (halfway). Total budget = 1000.
        # Expected spent = 500. Band = 5% of 1000 = 50.
        # Budgeted spent = 510 -> delta = +10, within +-50 -> on_track.
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('510.00'),
            year=2026,
            month=4,
            today=date(2026, 4, 15)
        )
        self.assertEqual(res['days_elapsed'], 15)
        self.assertEqual(res['days_in_month'], 30)
        self.assertEqual(res['expected_spent_to_date'], Decimal('500.00'))
        self.assertEqual(res['pace_delta'], Decimal('10.00'))
        self.assertEqual(res['pace_delta_abs'], Decimal('10.00'))
        self.assertEqual(res['projected_month_end'], Decimal('1020.00'))
        self.assertEqual(res['pace_state'], 'on_track')

    def test_pacing_current_month_ahead(self):
        # Spending faster than pace: spent 600 at day 15 (delta = +100 > +50)
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('600.00'),
            year=2026,
            month=4,
            today=date(2026, 4, 15)
        )
        self.assertEqual(res['pace_state'], 'ahead')
        self.assertEqual(res['pace_delta'], Decimal('100.00'))

    def test_pacing_current_month_under(self):
        # Spending slower than pace: spent 400 at day 15 (delta = -100 < -50)
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('400.00'),
            year=2026,
            month=4,
            today=date(2026, 4, 15)
        )
        self.assertEqual(res['pace_state'], 'under')
        self.assertEqual(res['pace_delta'], Decimal('-100.00'))
        self.assertEqual(res['pace_delta_abs'], Decimal('100.00'))

    def test_pacing_day_one(self):
        # Day 1 of April (30 days): expected = 1000 * 1 / 30 = 33.33
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('30.00'),
            year=2026,
            month=4,
            today=date(2026, 4, 1)
        )
        self.assertEqual(res['days_elapsed'], 1)
        self.assertEqual(res['expected_spent_to_date'], Decimal('33.33'))
        self.assertEqual(res['pace_state'], 'on_track')

    def test_pacing_past_month(self):
        # Past month: March 2026 when today is April 2026
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('900.00'),
            year=2026,
            month=3,
            today=date(2026, 4, 15)
        )
        self.assertEqual(res['days_elapsed'], 31)
        self.assertEqual(res['pace_state'], 'completed')

    def test_pacing_future_month(self):
        # Future month: May 2026 when today is April 2026
        res = calculate_budget_pacing(
            total_budget=Decimal('1000.00'),
            budgeted_spent=Decimal('0.00'),
            year=2026,
            month=5,
            today=date(2026, 4, 15)
        )
        self.assertEqual(res['days_elapsed'], 0)
        self.assertIsNone(res['pace_state'])

    def test_pacing_zero_budget(self):
        res = calculate_budget_pacing(
            total_budget=Decimal('0.00'),
            budgeted_spent=Decimal('50.00'),
            year=2026,
            month=4,
            today=date(2026, 4, 15)
        )
        self.assertIsNone(res['pace_state'])
        self.assertEqual(res['expected_spent_to_date'], Decimal('0.00'))


class BudgetDashboardViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='budget_tester', password='password123')
        profile = self.user.profile
        profile.currency = '₹'
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        self.client.login(username='budget_tester', password='password123')

    def test_like_for_like_budget_and_unbudgeted_spend(self):
        # Category A: limit 500
        cat_a = Category.objects.create(user=self.user, name='Groceries', limit=Decimal('500.00'))
        # Category B: no limit
        cat_b = Category.objects.create(user=self.user, name='Misc', limit=None)

        # Spend 300 in Groceries (budgeted)
        Expense.objects.create(
            user=self.user,
            category=cat_a.name,
            amount=Decimal('300.00'),
            base_amount=Decimal('300.00'),
            currency='₹',
            date=date(2026, 4, 10)
        )
        # Spend 400 in Misc (unbudgeted)
        Expense.objects.create(
            user=self.user,
            category=cat_b.name,
            amount=Decimal('400.00'),
            base_amount=Decimal('400.00'),
            currency='₹',
            date=date(2026, 4, 12)
        )

        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)

        ctx = response.context
        self.assertEqual(ctx['total_budget'], Decimal('500.00'))
        self.assertEqual(ctx['budgeted_spent'], Decimal('300.00'))
        self.assertEqual(ctx['unbudgeted_spent'], Decimal('400.00'))
        self.assertEqual(ctx['total_spent'], Decimal('700.00'))
        self.assertEqual(ctx['total_remaining'], Decimal('200.00'))
        self.assertEqual(ctx['over_budget_amount'], Decimal('0.00'))
        self.assertEqual(ctx['total_percentage'], 60.0)
        self.assertEqual(ctx['actual_total_percentage'], 60.0)

        # Content check
        content = response.content.decode('utf-8')
        # Main display shows budgeted_spent / total_budget
        self.assertIn('300', content)
        self.assertIn('500', content)
        # Note shows unbudgeted spent
        self.assertIn('Not in budget:', content)
        self.assertIn('400', content)

    def test_over_budget_calculation(self):
        cat = Category.objects.create(user=self.user, name='Dining', limit=Decimal('200.00'))
        Expense.objects.create(
            user=self.user,
            category=cat.name,
            amount=Decimal('260.00'),
            base_amount=Decimal('260.00'),
            currency='₹',
            date=date(2026, 4, 5)
        )

        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)
        ctx = response.context

        self.assertEqual(ctx['total_budget'], Decimal('200.00'))
        self.assertEqual(ctx['budgeted_spent'], Decimal('260.00'))
        self.assertEqual(ctx['unbudgeted_spent'], Decimal('0.00'))
        self.assertEqual(ctx['over_budget_amount'], Decimal('60.00'))
        self.assertEqual(ctx['total_remaining'], Decimal('0.00'))
        self.assertEqual(ctx['total_percentage'], 100.0)
        self.assertEqual(ctx['actual_total_percentage'], 130.0)

    def test_no_budgets_set(self):
        cat = Category.objects.create(user=self.user, name='Unbudgeted', limit=None)
        Expense.objects.create(
            user=self.user,
            category=cat.name,
            amount=Decimal('100.00'),
            base_amount=Decimal('100.00'),
            currency='₹',
            date=date(2026, 4, 5)
        )

        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)
        ctx = response.context

        self.assertEqual(ctx['total_budget'], Decimal('0.00'))
        self.assertEqual(ctx['budgeted_spent'], Decimal('0.00'))
        self.assertEqual(ctx['unbudgeted_spent'], Decimal('100.00'))
        self.assertEqual(ctx['total_spent'], Decimal('100.00'))
        self.assertEqual(ctx['total_remaining'], Decimal('0.00'))
        self.assertEqual(ctx['total_percentage'], 0.0)
        self.assertIsNone(ctx['pace_state'])
