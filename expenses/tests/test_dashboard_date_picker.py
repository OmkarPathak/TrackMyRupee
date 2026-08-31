from datetime import date
from django.test import Client, TestCase
from django.contrib.auth.models import User
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

    def test_dashboard_last_month_date_range_render(self):
        # Request dashboard with Last Month date range: start_date=2026-07-01 and end_date=2026-07-31
        response = self.client.get(reverse('home') + '?start_date=2026-07-01&end_date=2026-07-31')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify context receives the exact requested start_date string
        self.assertEqual(response.context['start_date'], '2026-07-01')
        self.assertEqual(response.context['end_date'], '2026-07-31')
        
        # Verify hidden inputs have exact requested dates and not March 1, 2026
        self.assertIn('value="2026-07-01"', content)
        self.assertIn('value="2026-07-31"', content)
        
        # Verify formatted trigger button label in HTML
        self.assertIn('01 Jul 2026 - 31 Jul 2026', content)

    def test_dashboard_default_date_range_render(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify default trigger text when no range query params are set
        self.assertIn('Select Date Range', content)
