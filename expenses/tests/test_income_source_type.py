import importlib
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.models import Account, Expense, Income

migration_module = importlib.import_module('expenses.migrations.0064_map_income_sources')
map_source_to_type = migration_module.map_source_to_type
from expenses.services import SalaryAnalysisService


class IncomeSourceTypeTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        # UserProfile is created automatically via signal in post_save
        if not hasattr(self.user, 'profile'):
            from expenses.models import UserProfile
            UserProfile.objects.create(user=self.user)
        self.account = Account.objects.create(user=self.user, name='Cash', currency='₹', balance=Decimal('10000.00'))

    def test_fuzzy_matching_logic(self):
        # Verify fuzzy matching mapping functions
        self.assertEqual(map_source_to_type("Salary from Google"), "Salary")
        self.assertEqual(map_source_to_type("Freelance Consulting Gig"), "Freelance / Consulting")
        self.assertEqual(map_source_to_type("My Shopify Business"), "Business")
        self.assertEqual(map_source_to_type("Dividend payout from Apple"), "Investment Returns")
        self.assertEqual(map_source_to_type("Apartment Rent"), "Rental Income")
        self.assertEqual(map_source_to_type("Cashback on Credit Card"), "Cashback & Rewards")
        self.assertEqual(map_source_to_type("Reimbursement from office trip"), "Refund / Reimbursement")
        self.assertEqual(map_source_to_type("Something completely random"), "Other")

    def test_savings_rate_calculations(self):
        # Create incomes
        # 1. Earned Income: Salary
        Income.objects.create(
            user=self.user, date=date(2026, 7, 1), amount=Decimal('50000.00'),
            source_type='Salary', source='Salary', account=self.account
        )
        # 2. Excluded Income: Cashback
        Income.objects.create(
            user=self.user, date=date(2026, 7, 1), amount=Decimal('2000.00'),
            source_type='Cashback & Rewards', source='Cashback', account=self.account
        )
        # 3. One-off Income: Investment Returns
        Income.objects.create(
            user=self.user, date=date(2026, 7, 1), amount=Decimal('10000.00'),
            source_type='Investment Returns', source='Stock dividends', account=self.account
        )

        # Create expense
        Expense.objects.create(user=self.user, date=date(2026, 7, 5), amount=Decimal('20000.00'), category='Rent')

        # Total Income = 50000 + 2000 + 10000 = 62000
        # Savings = Total Income - Expense = 62000 - 20000 = 42000
        # Savings Rate Denominator = Total Income - Cashback = 62000 - 2000 = 60000
        # Savings Rate = (42000 / 60000) * 100 = 70%

        # Test calculate_salary_cycle_metrics
        metrics = SalaryAnalysisService.calculate_salary_cycle_metrics(self.user, date(2026, 7, 31))
        self.assertEqual(metrics['total_income'], 62000.00)
        self.assertEqual(metrics['savings'], 42000.00)
        self.assertEqual(metrics['savings_rate'], 70.00)

    def test_form_validation_and_source_fallback(self):
        from expenses.forms import IncomeForm
        form_data = {
            'date': date(2026, 7, 10),
            'amount': Decimal('150.00'),
            'currency': '₹',
            'account': self.account.pk,
            'source_type': 'Business',
            'description': 'Sales revenue',
        }
        form = IncomeForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid())
        income = form.save(commit=False)
        income.user = self.user
        income.save()
        
        self.assertEqual(income.source, 'Business')
        self.assertEqual(income.description, 'Sales revenue')

    def test_income_list_source_type_filter(self):
        # Create different source_type incomes in the current month
        today = date.today()
        Income.objects.create(user=self.user, date=today.replace(day=10), amount=Decimal('100.00'), source_type='Salary', source='Salary', account=self.account)
        Income.objects.create(user=self.user, date=today.replace(day=11), amount=Decimal('200.00'), source_type='Business', source='Business', account=self.account)
        
        self.client.force_login(self.user)
        from django.urls import reverse
        url = reverse('income-list')
        
        # Test default (both returned)
        response = self.client.get(url)
        self.assertEqual(len(response.context['incomes']), 2)
        
        # Test filtered by source_type=Business
        response = self.client.get(url, {'source_type': 'Business'})
        self.assertEqual(len(response.context['incomes']), 1)
        self.assertEqual(response.context['incomes'][0].source_type, 'Business')

    def test_income_list_group_filtering_and_sparkline(self):
        # Create incomes of different high-level groups in the current month
        today = date.today()
        # 1. Earned
        Income.objects.create(user=self.user, date=today.replace(day=1), amount=Decimal('1000.00'), source_type='Salary', source='Salary', account=self.account)
        # 2. Passive
        Income.objects.create(user=self.user, date=today.replace(day=2), amount=Decimal('500.00'), source_type='Investment Returns', source='Dividends', account=self.account)
        # 3. One-off
        Income.objects.create(user=self.user, date=today.replace(day=3), amount=Decimal('100.00'), source_type='Cashback & Rewards', source='Cashback', account=self.account)
        
        self.client.force_login(self.user)
        from django.urls import reverse
        url = reverse('income-list')
        
        # Test default load and category totals in context
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['earned_total'], Decimal('1000.00'))
        self.assertEqual(response.context['passive_total'], Decimal('500.00'))
        self.assertEqual(response.context['one_off_total'], Decimal('100.00'))
        
        # Test sparkline data presence
        self.assertIn('sparkline_path', response.context)
        self.assertIn('sparkline_data', response.context)
        self.assertEqual(len(response.context['sparkline_data']), 6)
        
        # Test filtering by group = EARNED
        response = self.client.get(url, {'income_group': 'EARNED'})
        self.assertEqual(len(response.context['incomes']), 1)
        self.assertEqual(response.context['incomes'][0].source_type, 'Salary')
        
        # Test filtering by group = PASSIVE
        response = self.client.get(url, {'income_group': 'PASSIVE'})
        self.assertEqual(len(response.context['incomes']), 1)
        self.assertEqual(response.context['incomes'][0].source_type, 'Investment Returns')
        
        # Test filtering by group = ONE_OFF
        response = self.client.get(url, {'income_group': 'ONE_OFF'})
        self.assertEqual(len(response.context['incomes']), 1)
        self.assertEqual(response.context['incomes'][0].source_type, 'Cashback & Rewards')
