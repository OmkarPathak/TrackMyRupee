from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import Category, Expense, RecurringTransaction
from finance_tracker.plans import PLAN_DETAILS


class CategoryListViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cattestuser', password='password')
        self.client = Client()
        self.client.login(username='cattestuser', password='password')
        self.url = reverse('category-list')
        # Clean up any default categories created by signals
        Category.objects.filter(user=self.user).delete()

    def test_category_list_context_under_limit(self):
        """When user is below limit, reached_limit and nudge_at_limit should be False."""
        self.user.profile.tier = 'FREE'
        self.user.profile.save()

        # 0 categories is below limit of 3
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['reached_limit'])
        self.assertFalse(response.context['nudge_at_limit'])
        self.assertNotContains(response, "You've reached your limit")

    def test_category_list_context_at_limit(self):
        """When user reaches limit, reached_limit and nudge_at_limit should both be True and banner shown."""
        self.user.profile.tier = 'FREE'
        self.user.profile.save()
        limit = PLAN_DETAILS['FREE']['limits']['budget_categories']

        for i in range(limit):
            Category.objects.create(user=self.user, name=f'Category {i}')

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['reached_limit'])
        self.assertTrue(response.context['nudge_at_limit'])
        self.assertEqual(response.context['reached_limit'], response.context['nudge_at_limit'])
        self.assertContains(response, f"You've reached your limit of {limit} categories.")

    def test_category_list_context_pro_tier_unlimited(self):
        """For PRO tier (limit == -1), reached_limit is False and no nudge banner appears."""
        self.user.profile.tier = 'PRO'
        self.user.profile.save()

        for i in range(10):
            Category.objects.create(user=self.user, name=f'Category {i}')

        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['reached_limit'])
        self.assertNotIn('nudge_at_limit', response.context)
        self.assertNotContains(response, "You've reached your limit")


class CategoryUpdateViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='catupdateuser', password='password')
        self.other_user = User.objects.create_user(username='othercatuser', password='password')
        self.client = Client()
        self.client.login(username='catupdateuser', password='password')
        # Clean up any default categories created by signals
        Category.objects.filter(user__in=[self.user, self.other_user]).delete()

    def test_category_rename_cascades_to_expense_and_recurring_and_autoposts(self):
        """Renaming a category updates Expense and RecurringTransaction records and ensures auto-post uses new name."""
        from datetime import date
        from expenses.views.mixins import process_user_recurring_transactions

        # Create category
        cat = Category.objects.create(user=self.user, name='OldFood')

        # Create expense with old category name
        exp = Expense.objects.create(
            user=self.user,
            amount=Decimal('50.00'),
            date=date.today(),
            category='OldFood',
            category_fk=cat,
            description='Lunch'
        )

        # Create recurring transaction with old category name
        rt = RecurringTransaction.objects.create(
            user=self.user,
            description='Daily Lunch Subscription',
            transaction_type='EXPENSE',
            amount=Decimal('25.00'),
            frequency='DAILY',
            start_date=date.today(),
            category='OldFood',
            category_fk=cat,
            is_active=True,
        )

        # Create another user's expense and recurring transaction with same old category name
        other_cat = Category.objects.create(user=self.other_user, name='OldFood')
        other_exp = Expense.objects.create(
            user=self.other_user,
            amount=Decimal('70.00'),
            date=date.today(),
            category='OldFood',
            category_fk=other_cat,
            description='Other user lunch'
        )
        other_rt = RecurringTransaction.objects.create(
            user=self.other_user,
            description='Other user lunch recurring',
            transaction_type='EXPENSE',
            amount=Decimal('35.00'),
            frequency='DAILY',
            start_date=date.today(),
            category='OldFood',
            category_fk=other_cat,
            is_active=True,
        )

        # Post update to rename category
        edit_url = reverse('category-edit', kwargs={'pk': cat.pk})
        response = self.client.post(edit_url, {'name': 'NewFood', 'icon': 'bi-tag', 'limit': ''})
        self.assertEqual(response.status_code, 302)

        # Assert self.user records were updated
        cat.refresh_from_db()
        self.assertEqual(cat.name, 'NewFood')

        exp.refresh_from_db()
        self.assertEqual(exp.category, 'NewFood')

        rt.refresh_from_db()
        self.assertEqual(rt.category, 'NewFood')

        # Assert other user's records were untouched
        other_exp.refresh_from_db()
        self.assertEqual(other_exp.category, 'OldFood')
        other_rt.refresh_from_db()
        self.assertEqual(other_rt.category, 'OldFood')

        # Now test auto-posting of recurring transaction
        # Before process_user_recurring_transactions, delete today's expense created above so auto-post won't skip
        Expense.objects.filter(user=self.user).delete()
        process_user_recurring_transactions(self.user, force=True)

        new_auto_expenses = Expense.objects.filter(user=self.user, description='Daily Lunch Subscription (Recurring)')
        self.assertTrue(new_auto_expenses.exists())
        self.assertEqual(new_auto_expenses.first().category, 'NewFood')

