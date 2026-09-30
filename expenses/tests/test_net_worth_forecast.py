from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Expense, Income


class NetWorthForecastTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')
        self.profile = self.user.profile
        self.profile.has_seen_tutorial = True
        self.profile.save()
        self.client.force_login(self.user)
        
        # Create a base account with balance
        self.account = Account.objects.create(
            user=self.user,
            name="Main Bank",
            currency="INR",
            balance=Decimal('100000.00'),
            account_type='SAVINGS'
        )

    def _create_history(self, income_amt, expense_amt, months=3):
        """Helper to create historical data for N months."""
        today = date.today()
        for i in range(1, months + 1):
            # Calculate past month
            year = today.year
            month = today.month - i
            while month < 1:
                month += 12
                year -= 1
            d = date(year, month, 1)
            
            Income.objects.create(user=self.user, date=d, amount=income_amt, source='Salary', base_amount=income_amt)
            Expense.objects.create(user=self.user, date=d, amount=expense_amt, category='Food', base_amount=expense_amt)

    def test_forecast_positive_trend(self):
        """Test forecast with positive savings (Income > Expense)."""
        # 3 months of 10k savings each
        self._create_history(Decimal('50000.00'), Decimal('40000.00'), months=3)
        
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        
        self.assertNotIn('net_worth_forecasts', response.context)
        self.assertNotIn('projected_3m_growth', response.context)
        
        trend = response.context['net_worth_trend']
        # Current NW is 100,000. Avg savings is 10,000.
        # Month 1 projection should be 110,000; Month 3 should be 130,000
        self.assertEqual(trend[-3], 110000.0)
        self.assertEqual(trend[-1], 130000.0)

    def test_forecast_negative_trend(self):
        """Test forecast with negative savings (Expense > Income)."""
        # 3 months of 5k deficit each
        self._create_history(Decimal('30000.00'), Decimal('35000.00'), months=3)
        
        response = self.client.get(reverse('home'))
        self.assertNotIn('net_worth_forecasts', response.context)
        self.assertNotIn('projected_3m_growth', response.context)
        
        trend = response.context['net_worth_trend']
        # Current NW is 100,000. Avg savings is -5,000.
        self.assertEqual(trend[-3], 95000.0)

    def test_forecast_no_history(self):
        """Test forecast for new user with no history."""
        response = self.client.get(reverse('home'))
        self.assertNotIn('net_worth_forecasts', response.context)
        self.assertNotIn('projected_3m_growth', response.context)
        
        trend = response.context['net_worth_trend']
        # Should show 3 months of current net worth (100,000) with 0 change
        self.assertEqual(trend[-3], 100000.0)
        self.assertEqual(trend[-1], 100000.0)

    def test_forecast_context_keys(self):
        """Ensure dead forecast context keys are removed and sparkline arrays are present."""
        self._create_history(Decimal('1000.00'), Decimal('500.00'), months=1)
        
        response = self.client.get(reverse('home'))
        self.assertNotIn('net_worth_forecasts', response.context)
        self.assertNotIn('projected_3m_growth', response.context)
        self.assertIn('net_worth_trend', response.context)
        self.assertIn('net_worth_labels', response.context)
        self.assertEqual(len(response.context['net_worth_trend']), len(response.context['net_worth_labels']))
