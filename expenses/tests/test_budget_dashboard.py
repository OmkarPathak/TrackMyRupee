from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Category, Expense
from expenses.tests.budget_helpers import backdate_limits, legacy_page_ctx


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

        backdate_limits(self.user)   # the limits above have always applied
        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)

        ctx = legacy_page_ctx(response.context)
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

        backdate_limits(self.user)   # the limits above have always applied
        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)
        ctx = legacy_page_ctx(response.context)

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

        backdate_limits(self.user)   # the limits above have always applied
        response = self.client.get(reverse('budget'), {'year': 2026, 'month': 4})
        self.assertEqual(response.status_code, 200)
        ctx = legacy_page_ctx(response.context)

        self.assertEqual(ctx['total_budget'], Decimal('0.00'))
        self.assertEqual(ctx['budgeted_spent'], Decimal('0.00'))
        self.assertEqual(ctx['unbudgeted_spent'], Decimal('100.00'))
        self.assertEqual(ctx['total_spent'], Decimal('100.00'))
        self.assertEqual(ctx['total_remaining'], Decimal('0.00'))
        self.assertEqual(ctx['total_percentage'], 0.0)
        self.assertIsNone(ctx['pace_state'])
