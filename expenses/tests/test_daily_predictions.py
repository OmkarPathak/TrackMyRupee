from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.daily_predictions_service import DailyPredictionsService
from expenses.models import SavingsGoal, UserProfile


class DailyPredictionsServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = UserProfile.objects.get(user=self.user)
        self.profile.currency = '₹'
        self.profile.save()

    def test_get_predictions_context_basic(self):
        today = date(2026, 6, 15)
        # Call the service
        ctx = DailyPredictionsService.get_predictions_context(
            user=self.user,
            today=today,
            net_worth=Decimal('15000.00'),
            avg_monthly_savings=Decimal('2500.00'),
            total_monthly_budget=Decimal('10000.00'),
            month_spent_so_far=Decimal('3000.00'),
            days_in_current_month=30,
            currency_symbol='₹',
            salary_cycle_active=False,
            salary_cycle_start=None,
            salary_cycle_end=None
        )
        
        self.assertIn('runway', ctx)
        self.assertIn('velocity', ctx)
        self.assertIn('net_worth_compact', ctx)
        
        # Velocity formatting checks
        self.assertEqual(ctx['velocity']['formatted'], '+₹2.5K / mo')
        self.assertTrue(ctx['velocity']['is_positive'])
        self.assertEqual(ctx['net_worth_compact'], '15,000')
        
    def test_velocity_negative(self):
        today = date(2026, 6, 15)
        ctx = DailyPredictionsService.get_predictions_context(
            user=self.user,
            today=today,
            net_worth=Decimal('100000.00'),
            avg_monthly_savings=Decimal('-500.00'),
            total_monthly_budget=Decimal('10000.00'),
            month_spent_so_far=Decimal('3000.00'),
            days_in_current_month=30,
            currency_symbol='₹',
            salary_cycle_active=False,
            salary_cycle_start=None,
            salary_cycle_end=None
        )
        self.assertFalse(ctx['velocity']['is_positive'])
        self.assertEqual(ctx['velocity']['formatted'], '-₹500 / mo')

    def test_with_active_savings_goal(self):
        # Create active savings goal
        goal = SavingsGoal.objects.create(
            user=self.user,
            name="Buy Laptop",
            target_amount=Decimal('50000.00'),
            current_amount=Decimal('10000.00'),
            currency='₹'
        )
        
        today = date(2026, 6, 15)
        ctx = DailyPredictionsService.get_predictions_context(
            user=self.user,
            today=today,
            net_worth=Decimal('20000.00'),
            avg_monthly_savings=Decimal('5000.00'),
            total_monthly_budget=Decimal('10000.00'),
            month_spent_so_far=Decimal('3000.00'),
            days_in_current_month=30,
            currency_symbol='₹',
            salary_cycle_active=False,
            salary_cycle_start=None,
            salary_cycle_end=None
        )
        
        self.assertEqual(ctx['velocity']['target_formatted'], '50,000')
        self.assertEqual(ctx['velocity']['target_name'], 'Buy Laptop')
        # (50000 - 20000) / 5000 = 6 months
        self.assertEqual(ctx['velocity']['months_to_hit'], 6)
