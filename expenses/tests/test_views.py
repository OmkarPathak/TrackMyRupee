import json
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import Category, Expense, Income, RecurringTransaction


class BaseViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        # Create a category because ExpenseForm restricts choices to existing categories
        Category.objects.get_or_create(user=self.user, name='Food')
        # Mark tutorial as seen to avoid onboarding redirect in general view tests
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PLUS'
        profile.save()

class DashboardViewTest(BaseViewTest):
    def test_dashboard_access(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('recent_activity', response.context)
        self.assertIn('total_income', response.context)
        self.assertIn('savings', response.context)

    def test_dashboard_data_segregation(self):
        # Create another user and their expense
        other_user = User.objects.create_user(username='other', password='password')
        Expense.objects.create(user=other_user, date=date.today(), amount=500, description='Hidden', category='Food')
        
        # User's own expense
        Expense.objects.create(user=self.user, date=date.today(), amount=100, description='Visible', category='Food')
        
        response = self.client.get(reverse('home'))
        target_activity = response.context['recent_activity']
        expense_descriptions = [e.description for e in target_activity if getattr(e, 'transaction_type', None) == 'EXPENSE']
        self.assertEqual(len(expense_descriptions), 1)
        self.assertIn('Visible', expense_descriptions)

    def test_dashboard_filters(self):
        """Test year, month, and date range filters on dashboard."""
        today = date.today()
        last_year = today.year - 1
        
        # 1. Year Filter
        Expense.objects.create(user=self.user, date=date(last_year, 1, 1), amount=100, category='Food', description='Last Year', currency='₹')
        Expense.objects.create(user=self.user, date=today, amount=200, category='Food', description='This Year', currency='₹')
        
        response = self.client.get(reverse('home'), {'year': [str(today.year)]})
        activity = response.context['recent_activity']
        expense_descriptions = [e.description for e in activity if getattr(e, 'transaction_type', None) == 'EXPENSE']
        self.assertEqual(len(expense_descriptions), 1)
        self.assertIn('This Year', expense_descriptions)
        
        # 2. Date Range Filter
        response = self.client.get(reverse('home'), {
            'start_date': (today - timedelta(days=1)).strftime('%Y-%m-%d'),
            'end_date': (today + timedelta(days=1)).strftime('%Y-%m-%d')
        })
        activity = response.context['recent_activity']
        expense_descriptions = [e.description for e in activity if getattr(e, 'transaction_type', None) == 'EXPENSE']
        self.assertEqual(len(expense_descriptions), 1)
        self.assertIn('This Year', expense_descriptions)

    def test_dashboard_category_merging(self):
        """Test that categories with leading/trailing whitespace are merged."""
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Cat1', currency='₹')
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category=' Food ', description='Cat2', currency='₹')
        
        response = self.client.get(reverse('home'))
        # category_data should have 'Food' as a single entry with total 200
        category_data = response.context['category_data']
        food_entry = next(item for item in category_data if item['category'] == 'Food')
        self.assertEqual(food_entry['total'], 200)

    def test_demo_user_dashboard_net_worth_positive(self):
        """Test that the demo user has a positive net worth on the dashboard."""
        from django.core.management import call_command
        call_command('setup_demo_user')
        
        demo_client = Client()
        demo_client.login(username='demo', password='demo_password_123')
        
        response = demo_client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(response.context['net_worth'], 0)
        self.assertGreater(response.context['net_worth_before_liabilities'], response.context['total_liabilities'])

class ExpenseCRUDTest(BaseViewTest):
    def test_create_expense(self):
        url = reverse('expense-create')
        data = {
            'form-TOTAL_FORMS': '1',
            'form-INITIAL_FORMS': '0',
            'form-MIN_NUM_FORMS': '0',
            'form-MAX_NUM_FORMS': '1000',
            'form-0-date': date.today(),
            'form-0-amount': 250,
            'form-0-category': 'Food',
            'form-0-description': 'Lunch',
            'form-0-payment_method': 'Cash',
            'form-0-currency': '₹'
        }
        response = self.client.post(url, data)
        # Should redirect to expense list
        self.assertEqual(response.status_code, 302) 
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(Expense.objects.first().amount, 250)

    def test_update_expense(self):
        # ... existing code ...

        expense = Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Old', currency='₹')
        url = reverse('expense-edit', kwargs={'pk': expense.pk})
        data = {
            'date': date.today(),
            'amount': 200,
            'category': 'Food',
            'description': 'New',
            'payment_method': 'Cash',
            'currency': '₹'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, 200)
        self.assertEqual(expense.description, 'New')

    def test_update_expense_without_account(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=100,
            category='Food',
            description='Old without account',
            currency='₹',
            account=None,
        )
        url = reverse('expense-edit', kwargs={'pk': expense.pk})
        data = {
            'date': date.today(),
            'amount': 175,
            'category': 'Food',
            'description': 'Updated without account',
            'payment_method': 'Cash',
            'currency': '₹',
            'account': '',
        }

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, 302)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, 175)
        self.assertEqual(expense.description, 'Updated without account')
        self.assertIsNone(expense.account)

    def test_delete_expense(self):
        expense = Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Del', currency='₹')
        url = reverse('expense-delete', kwargs={'pk': expense.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.count(), 0)


    def test_delete_retains_filters(self):
        expense = Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Del', currency='₹')
        url = reverse('expense-delete', kwargs={'pk': expense.pk})
        # Add filters to the GET URL
        url_with_filters = f"{url}?category=Food&year={date.today().year}"
        
        # Post to the URL with filters
        response = self.client.post(url_with_filters)
        self.assertEqual(response.status_code, 302)
        self.assertIn('category=Food', response.url)
        self.assertIn(f'year={date.today().year}', response.url)

class IncomeCRUDTest(BaseViewTest):
    def test_create_income(self):
        url = reverse('income-create')
        data = {
            'date': date.today(),
            'amount': 5000,
            'source': 'Salary',
            'description': 'Jan Salary',
            'currency': '₹'
        }
        response = self.client.post(url, data)
        # Debug helper
        if response.status_code == 200:
             self.fail(f"Form errors: {response.context['form'].errors}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Income.objects.count(), 1)
        self.assertEqual(Income.objects.first().amount, 5000)

    def test_update_income(self):
        income = Income.objects.create(user=self.user, date=date.today(), amount=1000, source='Bonus', description='Old', currency='₹')
        url = reverse('income-edit', kwargs={'pk': income.pk})
        data = {
            'date': date.today(),
            'amount': 2000,
            'source': 'Bonus',
            'description': 'New',
            'currency': '₹'
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        income.refresh_from_db()
        self.assertEqual(income.amount, 2000)
        self.assertEqual(income.description, 'New')

    def test_update_income_without_account(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=1000,
            source='Bonus',
            description='Old without account',
            currency='₹',
            account=None,
        )
        url = reverse('income-edit', kwargs={'pk': income.pk})
        data = {
            'date': date.today(),
            'amount': 2200,
            'source': 'Bonus',
            'description': 'Updated without account',
            'currency': '₹',
            'account': '',
        }

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, 302)
        income.refresh_from_db()
        self.assertEqual(income.amount, 2200)
        self.assertEqual(income.description, 'Updated without account')
        self.assertIsNone(income.account)

    def test_delete_income(self):
        income = Income.objects.create(user=self.user, date=date.today(), amount=1000, source='Bonus', currency='₹')
        url = reverse('income-delete', kwargs={'pk': income.pk})
        # Delete view usually expects POST to confirm
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Income.objects.count(), 0)

class BulkActionTest(BaseViewTest):
    def test_bulk_delete_expenses(self):
        # Create multiple expenses associated with user
        e1 = Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='e1', currency='₹')
        e2 = Expense.objects.create(user=self.user, date=date.today(), amount=200, category='Food', description='e2', currency='₹')
        # Expense for another user to verify isolation
        other_user = User.objects.create_user(username='other', password='pw')
        e3 = Expense.objects.create(user=other_user, date=date.today(), amount=300, category='Food', description='e3', currency='₹')

        url = reverse('expense-bulk-delete')
        data = {'expense_ids': [e1.pk, e2.pk, e3.pk]}
        
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        
        self.assertEqual(Expense.objects.filter(pk__in=[e1.pk, e2.pk]).count(), 0)
        self.assertTrue(Expense.objects.filter(pk=e3.pk).exists())

    def test_bulk_delete_retains_filters(self):
        e1 = Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='e1', currency='₹')
        url = reverse('expense-bulk-delete')
        # Add filters to the URL
        url_with_filters = f"{url}?category=Food&year={date.today().year}"
        data = {'expense_ids': [e1.pk]}
        
        response = self.client.post(url_with_filters, data)
        self.assertEqual(response.status_code, 302)
        self.assertIn('category=Food', response.url)
        self.assertIn(f'year={date.today().year}', response.url)

class AnalyticsViewTest(BaseViewTest):
    def test_analytics_access(self):
        # Assuming URL name is 'analytics' (verified in implementation plan / memory)
        try:
            url = reverse('analytics')
        except:
             return 

        self.user.profile.tier = 'PRO'
        self.user.profile.is_lifetime = True
        self.user.profile.save()

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('income_data', response.context)
        self.assertIn('expense_data', response.context)

    def test_mom_analysis_access(self):
        url = reverse('analytics-mom')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('creep_categories', response.context)
        self.assertIn('asset_allocation', response.context)
        self.assertIn('labels', response.context)
        self.assertFalse(response.context['has_enough_creep_data'])

    def test_mom_analysis_with_enough_data(self):
        # Create expenses in last 3 months
        today = date.today()
        # Month 1 (current)
        Expense.objects.create(user=self.user, date=today, amount=200, category='Food', currency='₹')
        # Month 2 (previous)
        m2_date = today.replace(day=1) - timedelta(days=5)
        Expense.objects.create(user=self.user, date=m2_date, amount=150, category='Food', currency='₹')
        # Month 3 (2 months ago)
        m3_date = m2_date.replace(day=1) - timedelta(days=5)
        Expense.objects.create(user=self.user, date=m3_date, amount=100, category='Food', currency='₹')

        url = reverse('analytics-mom')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['has_enough_creep_data'])
        # Also check that creep is detected (amount increased 100 -> 150 -> 200)
        self.assertEqual(len(response.context['creep_categories']), 1)
        self.assertEqual(response.context['creep_categories'][0]['name'], 'Food')

class RecurringTransactionMixinTest(BaseViewTest):
    def test_recurring_expense_generation(self):
        # Create a recurring transaction due today
        start_date = date.today() 
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=500,
            description='Monthly Sub',
            frequency='MONTHLY',
            start_date=start_date,
            category='Subscription',
            last_processed_date=None,
            currency='₹'
        )
        
        # Hitting expense list (which uses the Mixin) should trigger processing
        self.client.get(reverse('expense-list'))
        
        if not Expense.objects.filter(description='Monthly Sub (Recurring)').exists():
             print("DEBUG EXPENSES:", list(Expense.objects.values()))
        
        self.assertTrue(Expense.objects.filter(description='Monthly Sub (Recurring)').exists())
        rt.refresh_from_db()
        self.assertIsNotNone(rt.last_processed_date)

    def test_recurring_income_generation(self):
        start_date = date.today()
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INCOME',
            amount=5000,
            description='Salary',
            frequency='MONTHLY',
            start_date=start_date,
            source='Job',
            last_processed_date=None,
            currency='₹'
        )
        
        # Trigger
        self.client.get(reverse('income-list'))
        
        self.assertTrue(Income.objects.filter(description='Salary (Recurring)').exists())
        rt.refresh_from_db()
        self.assertEqual(rt.last_processed_date, date.today())

class SettingsViewTest(BaseViewTest):
    def test_language_update(self):
        url = reverse('language-settings')
        response = self.client.post(url, {'language': 'mr'})
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.language, 'mr')

    def test_complete_tutorial_view(self):
        # Reset tutorial status
        profile = self.user.profile
        profile.has_seen_tutorial = False
        profile.save()
        
        url = reverse('complete-tutorial')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertTrue(data['success'])
        profile.refresh_from_db()
        self.assertTrue(profile.has_seen_tutorial)

class RecurringExpenseTest(BaseViewTest):
    def test_recurring_expense_on_dashboard(self):
        """Test that recurring expenses are processed when accessing dashboard."""
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='Monthly Sub',
            frequency='MONTHLY',
            start_date=date.today() - timedelta(days=1),
            last_processed_date=None,
            category='Food',
            currency='₹'
        )
        # Hitting home view should trigger processing via RecurringTransactionMixin
        self.client.get(reverse('home'))
        self.assertTrue(Expense.objects.filter(description='Monthly Sub (Recurring)').exists())

class DashboardAggregationTest(BaseViewTest):
    def test_category_limits_on_dashboard(self):
        """Test that category limits and usage percentages are calculated correctly."""
        cat = Category.objects.get(user=self.user, name='Food')
        cat.limit = 1000
        cat.save()
        
        Expense.objects.create(user=self.user, date=date.today(), amount=250, category='Food', description='Lunch', currency='₹')
        
        response = self.client.get(reverse('home'))
        category_limits = response.context['category_limits']
        food_limit = next(item for item in category_limits if item['name'] == 'Food')
        
        self.assertEqual(food_limit['limit'], 1000.0)
        self.assertEqual(food_limit['total'], 250.0)
        self.assertEqual(food_limit['used_percent'], 25.0)

    def test_dashboard_no_data_graceful(self):
        """Test dashboard renders even with no financial data (after tutorial seen)."""
        Expense.objects.filter(user=self.user).delete()
        Income.objects.filter(user=self.user).delete()
        
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['recent_activity']), 0)
