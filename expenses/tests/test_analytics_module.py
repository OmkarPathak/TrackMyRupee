"""Shared set-up for the Analytics tests, and the Year in Review page and its data (the Analytics page itself is
tested in test_analytics_page.py)."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    Category,
    Expense,
    FXRate,
    Income,
    Loan,
    LoanRepayment,
    Transfer,
    UserProfile,
)

D = Decimal


def today():
    return timezone.now().date()


def month_start(offset=0):
    """First day of the month `offset` months before this one."""
    t = today()
    index = t.year * 12 + (t.month - 1) - offset
    return datetime.date(index // 12, index % 12 + 1, 1)


class AnalyticsBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = User.objects.create_user('ana-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = self.tier
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000000'), currency='₹')
        for name in ('Food', 'Rent', 'Travel', 'Fun'):
            Category.objects.get_or_create(user=self.user, name=name)
        cache.clear()

    def income(self, amount, when=None, source_type='Salary', **kw):
        return Income.objects.create(user=self.user, account=self.bank, amount=D(str(amount)),
                                     source_type=source_type, date=when or today(), currency='₹', **kw)

    def expense(self, amount, when=None, category='Food', **kw):
        return Expense.objects.create(user=self.user, account=self.bank, amount=D(str(amount)),
                                      category=category, date=when or today(), currency='₹', **kw)

    def get(self, **params):
        return self.client.get(reverse('analytics'), params)

    def ctx(self, **params):
        return self.get(**params).context

    def seed_fx(self, usd_inr='80'):
        for (src, dst), rate in {('USD', 'INR'): usd_inr, ('$', '₹'): usd_inr,
                                 ('INR', 'USD'): str(D('1') / D(usd_inr)), ('₹', '$'): str(D('1') / D(usd_inr))}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()

    def make_loan_repayment(self, principal, interest, when=None):
        loan = Loan.objects.filter(user=self.user).first() or Loan.objects.create(
            user=self.user, name='Car', loan_type='AUTO', initial_principal=D('500000'), duration_months=60,
            start_date=month_start(8), currency='₹')
        return LoanRepayment.objects.create(
            loan=loan, from_account=self.bank, amount=D(str(principal + interest)),
            principal_portion=D(str(principal)), interest_portion=D(str(interest)), date=when or today())


class TestYearInReviewAccess(AnalyticsBase):
    def test_free_users_are_sent_to_pricing(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        response = self.client.get(reverse('year_in_review_default'))
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)

    def test_plus_users_see_it(self):
        UserProfile.objects.filter(user=self.user).update(tier='PLUS')
        self.expense(100)
        self.income(1000)
        self.assertEqual(self.client.get(reverse('year_in_review_default')).status_code, 200)


class TestYearInReviewData(AnalyticsBase):
    tier = 'PLUS'

    def review(self, **kw):
        from expenses.utils import generate_year_in_review_data
        return generate_year_in_review_data(self.user, kw.get('year', today().year))

    def test_no_expenses_means_no_story(self):
        self.income(1000)
        self.assertFalse(self.review()['has_data'])

    def test_totals_top_categories_and_months(self):
        year = today().year - 1
        self.income(50000, datetime.date(year, 2, 1))
        self.expense(300, datetime.date(year, 2, 3), category='Food', description='big one')
        self.expense(100, datetime.date(year, 5, 3), category='Rent')
        self.expense(50, datetime.date(year, 5, 4), category='Rent')
        data = self.review(year=year)
        self.assertEqual((data['total_earned'], data['total_spent'], data['transaction_count']), (50000, 450, 3))
        self.assertEqual([c['category'] for c in data['top_categories']], ['Food', 'Rent'])
        self.assertEqual(data['highest_month']['name'], 'February')
        self.assertEqual(data['highest_month']['total'], 300)
        self.assertEqual(data['lowest_month']['name'], 'May')
        self.assertEqual(data['biggest_expense']['description'], 'big one')

    def test_saved_and_the_rate_use_the_one_definition(self):
        year = today().year - 1
        self.income(100000, datetime.date(year, 2, 1))
        self.expense(40000, datetime.date(year, 2, 3))
        self.make_loan_repayment(principal=5000, interest=10000, when=datetime.date(year, 2, 5))
        data = self.review(year=year)
        self.assertEqual(data['net_saved'], 50000)
        self.assertEqual(data['savings_rate'], D('50.0'))

    def test_invested_counts_transfers_into_investment_accounts_in_your_currency(self):
        funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        self.expense(10)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('5000'), date=today())
        self.assertEqual(self.review()['total_invested'], 5000)

    def test_this_year_stops_at_today(self):
        self.expense(100)
        future = today() + datetime.timedelta(days=20)
        if future.year == today().year:
            self.expense(9999, future)
            self.assertEqual(self.review()['total_spent'], 100)


class TestYearInReviewPage(AnalyticsBase):
    tier = 'PLUS'

    def test_the_link_on_analytics_opens_the_selected_year(self):
        last = today().year - 1
        self.expense(777, datetime.date(last, 4, 4))
        self.expense(5)
        url = reverse('year_in_review', args=[last])
        page = self.get(time_period='custom', start_date=f'{last}-01-01', end_date=f'{last}-12-31')
        self.assertIn(url, page.content.decode())
        context = self.client.get(url).context
        self.assertEqual(context['year'], last)
        self.assertEqual(context['review_data']['total_spent'], 777)

    def test_the_default_url_is_this_year_and_says_so(self):
        self.expense(5)
        context = self.client.get(reverse('year_in_review_default')).context
        self.assertEqual(context['year'], today().year)
        self.assertEqual(context['review_data']['total_spent'], 5)

    def test_an_absurd_year_falls_back_to_this_year(self):
        self.expense(5)
        context = self.client.get(reverse('year_in_review', args=[1500])).context
        self.assertEqual(context['year'], today().year)
