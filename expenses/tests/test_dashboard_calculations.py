"""Known-answer tests for the dashboard: totals, savings, categories, trends, payment split,
month-over-month comparison and filters."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account, CapitalEvent, Category, Expense, FXRate, Income, Loan, LoanRepayment, Transfer, UserProfile,
)

D = Decimal


def today():
    return timezone.localdate()


def first_of_month():
    return today().replace(day=1)


def last_month_day():
    return first_of_month() - datetime.timedelta(days=1)


class DashBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='dash-calc', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        Account.objects.filter(user=self.user).delete()
        Category.objects.filter(user=self.user).delete()
        for name in ('Food', 'Transport', 'Large Purchase'):
            Category.objects.create(user=self.user, name=name)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('1000000'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000000'), currency='₹')
        self.client.force_login(self.user)
        cache.clear()

    def income(self, amount, when=None, source_type='Salary', account=None, **kw):
        return Income.objects.create(user=self.user, date=when or today(), amount=D(str(amount)), source=source_type,
                                     source_type=source_type, currency='₹', account=account or self.cash, **kw)

    def expense(self, amount, category='Food', when=None, account=None, method='Cash', **kw):
        return Expense.objects.create(user=self.user, date=when or today(), amount=D(str(amount)), category=category,
                                      currency='₹', account=account or self.cash, payment_method=method,
                                      description=kw.pop('description', category), **kw)

    def home(self, query=''):
        response = self.client.get(reverse('home') + query)
        self.assertEqual(response.status_code, 200)
        return response.context

    def loan_repayment(self, principal='9000', interest='1000', when=None, account=None):
        loan = Loan.objects.filter(user=self.user).first() or Loan.objects.create(
            user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('100000'), duration_months=12,
            start_date=first_of_month(), currency='₹')
        return LoanRepayment.objects.create(
            loan=loan, date=when or today(), amount=D(principal) + D(interest), principal_portion=D(principal),
            interest_portion=D(interest), from_account=account or self.cash)


class TestHeadlineNumbers(DashBase):
    def setUp(self):
        super().setUp()
        self.income(50000)
        self.income(2000, source_type='Cashback & Rewards')
        self.expense(3000, 'Food')
        self.expense(1000, 'Transport', method='UPI')
        self.loan_repayment()
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('5000'), currency='₹', subtype='other',
                                    account=self.cash, exclude_from_averages=False)
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('20000'), currency='₹', subtype='other',
                                    account=self.cash)  # default: kept out of averages
        self.ctx = self.home()

    def test_income_expenses_and_savings(self):
        self.assertEqual(self.ctx['total_income'], D('52000.00'))
        # 4,000 expenses + 1,000 loan interest (principal is not spending) + 5,000 counted capital event
        self.assertEqual(self.ctx['total_expenses'], D('10000.00'))
        self.assertEqual(self.ctx['savings'], D('42000.00'))
        self.assertEqual(self.ctx['hero_metrics']['savings_amount'], D('42000.00'))

    def test_savings_rate_leaves_cashback_out_of_the_denominator(self):
        # 42,000 saved out of 50,000 of real income (the 2,000 cashback is left out of the denominator)
        self.assertEqual(self.ctx['hero_metrics']['savings_rate'], 84.0)

    def test_category_breakdown_includes_loan_interest_and_sorts_by_amount(self):
        self.assertEqual(self.ctx['categories'], ['Food', 'Transport', 'Loan Interest'])
        self.assertEqual(self.ctx['category_amounts'], [3000.0, 1000.0, 1000.0])

    def test_payment_method_split(self):
        self.assertEqual(dict(zip(self.ctx['payment_labels'], self.ctx['payment_data'])), {'Cash': 3000.0, 'UPI': 1000.0})

    def test_top_expenses(self):
        self.assertEqual(self.ctx['top_amounts'], [3000.0, 1000.0])

    def test_daily_trend_is_one_bar_for_today(self):
        self.assertEqual(self.ctx['trend_datasets'][0]['data'], [4000.0])

    def test_income_vs_expense_series_use_the_same_definition(self):
        self.assertEqual(self.ctx['ie_income_data'], [52000.0])
        self.assertEqual(self.ctx['ie_expense_data'], [10000.0])
        self.assertEqual(self.ctx['ie_savings_data'], [42000.0])


class TestCategoriesAndBudgets(DashBase):
    def test_budget_status_and_usage_for_the_month(self):
        Category.objects.filter(user=self.user, name='Food').update(limit=D('5000'))
        self.expense(4500, 'Food')
        row = {c['name']: c for c in self.home()['category_limits']}['Food']
        self.assertEqual((row['limit'], row['used_percent'], row['status']), (5000.0, 90.0, 'limit'))

    def test_capital_event_not_excluded_from_budget_lands_in_its_category(self):
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('7000'), currency='₹',
                                    subtype='large_purchase', account=self.cash, exclude_from_budget=False)
        ctx = self.home()
        self.assertIn('Large Purchase', ctx['categories'])
        self.assertEqual(dict(zip(ctx['categories'], ctx['category_amounts']))['Large Purchase'], 7000.0)
        # kept out of averages by default, so it does not change "spent"
        self.assertEqual(ctx['total_expenses'], D('0.00'))

    def test_more_than_five_categories_group_into_others(self):
        for index, amount in enumerate((600, 500, 400, 300, 200, 100, 50), start=1):
            name = f'Cat{index}'
            Category.objects.create(user=self.user, name=name)
            self.expense(amount, name)
        ctx = self.home()
        self.assertEqual(len(ctx['categories']), 6)
        self.assertEqual(ctx['categories'][-1], 'Others')
        self.assertEqual(ctx['category_amounts'][-1], 150.0)
        self.assertEqual(sum(ctx['category_amounts']), 2150.0)


class TestCurrencyAndFilters(DashBase):
    def test_foreign_currency_expense_counts_in_base_currency(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=D('1000'),
                                     currency='$')
        Expense.objects.create(user=self.user, date=today(), amount=D('10'), currency='$', category='Food',
                               account=usd, description='x')
        self.assertEqual(self.home()['total_expenses'], D('800.00'))

    def test_account_filter_limits_every_total_to_that_account(self):
        self.income(10000, account=self.cash)
        self.income(3000, account=self.bank)
        self.expense(400, account=self.cash)
        self.expense(700, account=self.bank)
        self.loan_repayment('900', '100', account=self.bank)
        ctx = self.home(f'?account={self.bank.id}')
        self.assertEqual(ctx['total_income'], D('3000.00'))
        self.assertEqual(ctx['total_expenses'], D('800.00'))   # 700 + 100 interest
        self.assertEqual(ctx['savings'], D('2200.00'))

    def test_category_and_payment_method_filters_apply_to_spending(self):
        self.expense(400, 'Food', method='Cash')
        self.expense(700, 'Transport', method='UPI')
        self.assertEqual(self.home('?category=Food')['total_expenses'], D('400.00'))
        self.assertEqual(self.home('?payment_method=UPI')['total_expenses'], D('700.00'))

    def test_last_month_view_excludes_this_month(self):
        self.expense(400)
        self.expense(900, when=last_month_day())
        ctx = self.home('?time_period=last_month')
        self.assertEqual(ctx['total_expenses'], D('900.00'))

    def test_custom_date_range(self):
        self.expense(400)
        self.expense(900, when=last_month_day())
        start = last_month_day() - datetime.timedelta(days=2)
        ctx = self.home(f'?time_period=custom&start_date={start}&end_date={last_month_day()}')
        self.assertEqual(ctx['total_expenses'], D('900.00'))

    def test_investment_transfers_are_counted_separately(self):
        demat = Account.objects.create(user=self.user, name='Demat', account_type='DEMAT', balance=D('0'),
                                       currency='₹')
        Transfer.objects.create(user=self.user, date=today(), amount=D('2500'), from_account=self.cash,
                                to_account=demat)
        self.income(10000)
        ctx = self.home()
        self.assertEqual(ctx['total_investments'], D('2500.00'))
        self.assertEqual(ctx['savings'], D('10000.00'))
        self.assertEqual(ctx['remaining_savings'], D('7500.00'))


class TestMonthOverMonth(DashBase):
    def test_comparison_with_the_previous_month(self):
        self.income(10000)
        self.expense(2000)
        self.income(8000, when=last_month_day())
        self.expense(1000, when=last_month_day())
        self.loan_repayment('900', '100', when=last_month_day())
        year, month = today().year, today().month
        ctx = self.home(f'?year={year}&month={month}')
        prev = ctx['prev_month_data']
        self.assertEqual((prev['income'], prev['expense'], prev['savings']), (D('8000.00'), D('1100.00'), D('6900.00')))
        self.assertAlmostEqual(float(prev['income_pct']), 25.0)
        self.assertAlmostEqual(float(prev['expense_pct']), (2000 - 1100) / 1100 * 100, places=4)
        self.assertAlmostEqual(float(prev['savings_pct']), (8000 - 6900) / 6900 * 100, places=4)

    def test_previous_month_follows_the_same_filters(self):
        self.expense(2000, 'Food')
        self.expense(5000, 'Transport')
        self.expense(1000, 'Food', when=last_month_day())
        self.expense(4000, 'Transport', when=last_month_day())
        year, month = today().year, today().month
        ctx = self.home(f'?year={year}&month={month}&category=Food')
        self.assertEqual(ctx['prev_month_data']['expense'], D('1000.00'))
        self.assertAlmostEqual(float(ctx['prev_month_data']['expense_pct']), 100.0)


class TestSalaryCycle(DashBase):
    def test_cycle_view_counts_only_the_current_cycle(self):
        profile = self.user.profile
        profile.salary_date = today().day
        profile.save()
        self.expense(400)                                                  # today = first day of the cycle
        self.expense(900, when=today() - datetime.timedelta(days=1))       # belongs to the previous cycle
        self.income(5000)
        self.income(7000, when=today() - datetime.timedelta(days=1))
        cache.clear()
        ctx = self.home()
        self.assertTrue(ctx['salary_cycle_active'])
        self.assertEqual((ctx['total_income'], ctx['total_expenses'], ctx['savings']),
                         (D('5000.00'), D('400.00'), D('4600.00')))


class TestYearToDateProjection(DashBase):
    def test_projection_uses_the_same_spending_as_the_savings_card(self):
        year, month = today().year, today().month
        for m in range(1, month + 1):
            self.income(10000, when=datetime.date(year, m, 1))
            self.expense(4000, when=datetime.date(year, m, 1))
        CapitalEvent.objects.create(user=self.user, date=datetime.date(year, month, 1), amount=D('1000'),
                                    currency='₹', subtype='other', account=self.cash, exclude_from_averages=False)
        ytd = month * 10000 - (month * 4000 + 1000)
        expected = ytd + (ytd / month) * (12 - month)
        cache.clear()
        self.assertAlmostEqual(self.home()['projected_savings'], expected, places=2)
