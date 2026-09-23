from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Category, Expense, UserProfile


class DashboardDatePickerTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='picker_user', email='picker@example.com', password='password123')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)

        self.account = Account.objects.create(user=self.user, name='Main Account', balance=5000)
        self.category, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(user=self.user, amount=150, account=self.account, category='Food', description='Groceries', date='2026-08-01')
        self.client.login(username='picker_user', password='password123')

    def test_dashboard_custom_date_range_render(self):
        # Request dashboard with Custom date range: start_date=2026-07-01 and end_date=2026-07-31
        response = self.client.get(reverse('home') + '?start_date=2026-07-01&end_date=2026-07-31')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify context receives the exact requested start_date and end_date
        self.assertEqual(response.context['start_date'], '2026-07-01')
        self.assertEqual(response.context['end_date'], '2026-07-31')
        self.assertEqual(response.context['applied_state']['time_period'], 'custom')
        self.assertEqual(response.context['applied_state']['start_date'], '2026-07-01')
        self.assertEqual(response.context['applied_state']['end_date'], '2026-07-31')
        
        # Verify unified filter toolbar renders with page key
        self.assertIn('data-page-key="dashboard"', content)
        self.assertIn('2026-07-01 &ndash; 2026-07-31', content)

    def test_dashboard_default_date_range_render(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify default time period is this_month
        self.assertEqual(response.context['applied_state']['time_period'], 'this_month')
        self.assertIn('data-page-key="dashboard"', content)
        self.assertIn('This month', content)
        
        # Verify sort button is omitted on dashboard (supports_sort=False)
        self.assertNotIn('tmr-sort-btn', content)

    def test_dashboard_category_filter(self):
        Expense.objects.create(user=self.user, amount=200, account=self.account, category='Entertainment', description='Cinema', date='2026-08-01')
        response = self.client.get(reverse('home') + '?category=Food&start_date=2026-07-01&end_date=2026-08-31')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['applied_state']['filters']['category'], ['Food'])
        
        # Verify only Food expenses are included in category breakdown
        category_data = response.context['category_data']
        cat_names = [item['category'] for item in category_data]
        self.assertIn('Food', cat_names)
        self.assertNotIn('Entertainment', cat_names)

    def test_dashboard_time_period_presets(self):
        for preset in ['last_month', 'last_3_months', 'this_year', 'all']:
            response = self.client.get(reverse('home') + f'?time_period={preset}')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['applied_state']['time_period'], preset)
