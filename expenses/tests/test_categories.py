from decimal import Decimal
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.filters import CATEGORY_LIST_FILTERS
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

    def test_category_list_filter_search(self):
        """Filtering categories by search query returns matching categories."""
        self.user.profile.tier = 'PRO'
        self.user.profile.save()

        Category.objects.create(user=self.user, name='Groceries')
        Category.objects.create(user=self.user, name='Utilities')
        Category.objects.create(user=self.user, name='Travel')

        response = self.client.get(f"{self.url}?search=Gro")
        self.assertEqual(response.status_code, 200)
        names = [c.name for c in response.context['categories']]
        self.assertEqual(names, ['Groceries'])
        self.assertEqual(response.context['applied_state']['search'], 'Gro')

    def test_category_list_filter_budget_status(self):
        """Filtering by budget_status separates budgeted and unbudgeted categories."""
        self.user.profile.tier = 'PRO'
        self.user.profile.save()

        Category.objects.create(user=self.user, name='Budgeted Food', limit=Decimal('5000.00'))
        Category.objects.create(user=self.user, name='Unbudgeted Fun', limit=None)

        # 1. Budgeted
        resp_budgeted = self.client.get(f"{self.url}?budget_status=budgeted")
        self.assertEqual(resp_budgeted.status_code, 200)
        budgeted_names = [c.name for c in resp_budgeted.context['categories']]
        self.assertIn('Budgeted Food', budgeted_names)
        self.assertNotIn('Unbudgeted Fun', budgeted_names)

        # 2. Unbudgeted
        resp_unbudgeted = self.client.get(f"{self.url}?budget_status=unbudgeted")
        self.assertEqual(resp_unbudgeted.status_code, 200)
        unbudgeted_names = [c.name for c in resp_unbudgeted.context['categories']]
        self.assertIn('Unbudgeted Fun', unbudgeted_names)
        self.assertNotIn('Budgeted Food', unbudgeted_names)

    def test_category_list_sort(self):
        """Sorting categories by name and budget limit works as expected."""
        self.user.profile.tier = 'PRO'
        self.user.profile.save()

        Category.objects.create(user=self.user, name='Beta', limit=Decimal('5000.00'))
        Category.objects.create(user=self.user, name='Alpha', limit=Decimal('1000.00'))
        Category.objects.create(user=self.user, name='Gamma', limit=Decimal('2000.00'))
        Category.objects.create(user=self.user, name='Delta', limit=None)

        # 1. Name A-Z (default)
        resp_name_asc = self.client.get(f"{self.url}?sort=name_asc")
        names_asc = [c.name for c in resp_name_asc.context['categories']]
        self.assertEqual(names_asc, ['Alpha', 'Beta', 'Delta', 'Gamma'])

        # 2. Name Z-A
        resp_name_desc = self.client.get(f"{self.url}?sort=name_desc")
        names_desc = [c.name for c in resp_name_desc.context['categories']]
        self.assertEqual(names_desc, ['Gamma', 'Delta', 'Beta', 'Alpha'])

        # 3. Limit desc (nulls last)
        resp_limit_desc = self.client.get(f"{self.url}?sort=limit_desc")
        limit_desc_names = [c.name for c in resp_limit_desc.context['categories']]
        self.assertEqual(limit_desc_names, ['Beta', 'Gamma', 'Alpha', 'Delta'])

        # 4. Limit asc (nulls last)
        resp_limit_asc = self.client.get(f"{self.url}?sort=limit_asc")
        limit_asc_names = [c.name for c in resp_limit_asc.context['categories']]
        self.assertEqual(limit_asc_names, ['Alpha', 'Gamma', 'Beta', 'Delta'])

    def test_category_list_filter_context_and_toolbar(self):
        """Category list renders filter toolbar and passes config and state in context."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['filter_config'], CATEGORY_LIST_FILTERS)
        self.assertIn('applied_state', response.context)
        self.assertContains(response, 'id="tmr-toolbar-category"')
        self.assertContains(response, 'tmr_filter.css')
        self.assertContains(response, 'tmr_filter.js')

    def test_category_filter_options_api(self):
        """Filter options API supports page=category with budget_status."""
        api_url = reverse('filter-options-api') + '?page=category&filter=budget_status'
        response = self.client.get(api_url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['page'], 'category')
        self.assertEqual(data['filter'], 'budget_status')
        opt_values = [opt['value'] for opt in data['options']]
        self.assertIn('budgeted', opt_values)
        self.assertIn('unbudgeted', opt_values)


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

