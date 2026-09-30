from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import Account, Category, Expense
from expenses.periods import calculate_budget_period_factor


class BudgetPeriodFactorTest(TestCase):
    def test_same_month_full_month(self):
        # September has 30 days
        factor = calculate_budget_period_factor(date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(factor, 1.0)

        # October has 31 days
        factor_oct = calculate_budget_period_factor(date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(factor_oct, 1.0)

        # February 2026 has 28 days
        factor_feb = calculate_budget_period_factor(date(2026, 2, 1), date(2026, 2, 28))
        self.assertEqual(factor_feb, 1.0)

    def test_same_month_partial(self):
        # 15 days out of 30 in September
        factor = calculate_budget_period_factor(date(2026, 9, 1), date(2026, 9, 15))
        self.assertEqual(factor, 0.5)

        # 10 days out of 31 in October
        factor_oct = calculate_budget_period_factor(date(2026, 10, 1), date(2026, 10, 10))
        self.assertAlmostEqual(factor_oct, round(10 / 31, 4), places=3)

    def test_full_year(self):
        # Jan 1 to Dec 31 = exactly 12.0
        factor = calculate_budget_period_factor(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(factor, 12.0)

    def test_three_full_months(self):
        # Jan 1 to Mar 31 = exactly 3.0
        factor = calculate_budget_period_factor(date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual(factor, 3.0)

    def test_invalid_ranges(self):
        self.assertEqual(calculate_budget_period_factor(None, date(2026, 1, 1)), 1.0)
        self.assertEqual(calculate_budget_period_factor(date(2026, 5, 1), None), 1.0)
        self.assertEqual(calculate_budget_period_factor(date(2026, 5, 10), date(2026, 5, 1)), 1.0)


class DashboardBudgetPeriodScalingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='budget_scaler', password='password123')
        self.profile = self.user.profile
        self.profile.has_seen_tutorial = True
        self.profile.consent_granted = True
        self.profile.salary_date = 25
        self.profile.save()

        self.account = Account.objects.create(
            user=self.user,
            name='Primary Checking',
            account_type='CHECKING',
            balance=Decimal('500000.00'),
        )

        # Category with monthly limit of 15,000 (like Home Allowance in screenshot)
        self.home_allowance = Category.objects.create(
            user=self.user,
            name='Home Allowance',
            limit=Decimal('15000.00'),
        )
        # Category with monthly limit of 10,000 (like Swara in screenshot)
        self.swara = Category.objects.create(
            user=self.user,
            name='Swara',
            limit=Decimal('10000.00'),
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_this_month_budget_limit_is_monthly(self):
        today = date.today()
        Expense.objects.create(
            user=self.user,
            account=self.account,
            category='Home Allowance',
            amount=Decimal('5000.00'),
            date=today,
        )

        response = self.client.get(reverse('home'), {'time_period': 'this_month'})
        self.assertEqual(response.status_code, 200)

        cat_limits = {c['name']: c for c in response.context['category_limits']}
        self.assertIn('Home Allowance', cat_limits)
        # In this_month, limit should equal raw monthly limit
        self.assertEqual(cat_limits['Home Allowance']['limit'], 15000.0)
        self.assertEqual(cat_limits['Home Allowance']['total'], 5000.0)
        self.assertAlmostEqual(cat_limits['Home Allowance']['used_percent'], round(5000.0 / 15000.0 * 100, 1))

    def test_this_year_budget_limit_is_scaled_to_year(self):
        # Expense of 250,000 across the year (like in user screenshot)
        Expense.objects.create(
            user=self.user,
            account=self.account,
            category='Home Allowance',
            amount=Decimal('250000.00'),
            date=date(date.today().year, 1, 15),
        )

        response = self.client.get(reverse('home'), {'time_period': 'this_year'})
        self.assertEqual(response.status_code, 200)

        cat_limits = {c['name']: c for c in response.context['category_limits']}
        # Year budget limit should be 15,000 * 12 = 180,000 (NOT 15,000)
        self.assertEqual(cat_limits['Home Allowance']['limit'], 180000.0)
        self.assertEqual(cat_limits['Home Allowance']['total'], 250000.0)
        # used_percent: 250,000 / 180,000 = 138.9% (NOT 1666.7%)
        self.assertAlmostEqual(cat_limits['Home Allowance']['used_percent'], 138.9, places=1)

    def test_last_3_months_budget_limit_is_scaled_to_90_days(self):
        Expense.objects.create(
            user=self.user,
            account=self.account,
            category='Swara',
            amount=Decimal('20000.00'),
            date=date.today() - timedelta(days=20),
        )

        response = self.client.get(reverse('home'), {'time_period': 'last_3_months'})
        self.assertEqual(response.status_code, 200)

        cat_limits = {c['name']: c for c in response.context['category_limits']}
        # 90 days is ~2.95 - 3.0 months -> ~29,500 - 30,000 (NOT 10,000)
        swara_limit = cat_limits['Swara']['limit']
        self.assertGreater(swara_limit, 29000.0)
        self.assertLess(swara_limit, 30500.0)
        # 20,000 against ~29,600 is well under 100% (around ~67%), NOT 200% over budget
        self.assertLess(cat_limits['Swara']['used_percent'], 75.0)

    def test_custom_date_range_budget_limit_scaled_by_days(self):
        # 10 days in current month
        start = date(2026, 9, 1)
        end = date(2026, 9, 10)
        Expense.objects.create(
            user=self.user,
            account=self.account,
            category='Home Allowance',
            amount=Decimal('3000.00'),
            date=date(2026, 9, 5),
        )

        response = self.client.get(reverse('home'), {
            'time_period': 'custom',
            'start_date': start.strftime('%Y-%m-%d'),
            'end_date': end.strftime('%Y-%m-%d'),
        })
        self.assertEqual(response.status_code, 200)

        cat_limits = {c['name']: c for c in response.context['category_limits']}
        # 10 days out of 30 in September = 1/3 of monthly limit = 15,000 * (10/30) = 5,000
        self.assertAlmostEqual(cat_limits['Home Allowance']['limit'], 5000.0, places=0)
        # 3,000 out of 5,000 = 60%
        self.assertAlmostEqual(cat_limits['Home Allowance']['used_percent'], 60.0, places=0)

    def test_salary_cycle_and_edit_button_always_visible_on_all_filters(self):
        """Ensure salary period text and pencil edit link are visible across all filters."""
        filter_scenarios = [
            {'time_period': 'this_month'},
            {'time_period': 'this_year'},
            {'time_period': 'last_3_months'},
            {'time_period': 'all'},
            {'time_period': 'custom', 'start_date': '2026-01-01', 'end_date': '2026-03-31'},
        ]

        edit_url = reverse('profile-settings')

        for params in filter_scenarios:
            with self.subTest(params=params):
                response = self.client.get(reverse('home'), params)
                self.assertEqual(response.status_code, 200)

                # Context always provides valid salary_cycle_start and salary_cycle_end
                self.assertIsNotNone(response.context['salary_cycle_start'])
                self.assertIsNotNone(response.context['salary_cycle_end'])
                self.assertIsInstance(response.context['salary_cycle_start'], date)
                self.assertIsInstance(response.context['salary_cycle_end'], date)

                content = response.content.decode('utf-8')
                # Edit button link is in HTML
                self.assertIn(edit_url, content)
                # Salary cycle text is in HTML
                self.assertIn('salary-cycle-subtle', content)
                self.assertIn('Salary cycle:', content)
