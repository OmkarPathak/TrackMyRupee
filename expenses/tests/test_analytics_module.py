"""Analytics page: yearly chart, YTD totals, health score and breakdown, charts, Year in Review."""

import calendar
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    CapitalEvent,
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


class TestAccessAndYears(AnalyticsBase):
    def test_every_plan_can_open_it(self):
        for tier in ('FREE', 'PLUS', 'PRO'):
            UserProfile.objects.filter(user=self.user).update(tier=tier)
            self.assertEqual(self.get().status_code, 200)
            self.assertFalse(self.get().context.get('is_locked'))

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 302)

    def test_an_empty_account_still_renders(self):
        context = self.ctx()
        self.assertEqual(context['available_years'], [today().year])
        self.assertEqual(context['avg_balance_rate'], 0)
        self.assertEqual(context['cat_labels'], [])

    def test_years_come_from_your_data_newest_first(self):
        self.expense(10, datetime.date(today().year - 2, 3, 5))
        self.income(10, datetime.date(today().year - 1, 3, 5))
        self.expense(10)
        self.assertEqual(self.ctx()['available_years'], [today().year, today().year - 1, today().year - 2])

    def test_a_bad_year_falls_back_to_this_year(self):
        self.assertEqual(self.ctx(year='abc')['selected_year'], today().year)
        self.assertTrue(self.ctx(year='abc')['is_current_year'])

    def test_a_past_year_is_not_the_current_year(self):
        context = self.ctx(year=today().year - 1)
        self.assertEqual(context['selected_year'], today().year - 1)
        self.assertFalse(context['is_current_year'])


class TestYearlyChart(AnalyticsBase):
    def test_this_year_runs_from_january_to_this_month(self):
        self.expense(10)
        context = self.ctx()
        self.assertEqual(len(context['chart_labels']), today().month)
        self.assertTrue(context['chart_labels'][0].startswith('Jan'))
        self.assertTrue(context['chart_labels'][-1].startswith(today().strftime('%b')))

    def test_a_past_year_shows_that_years_twelve_months(self):
        last = today().year - 1
        self.income(50000, datetime.date(last, 3, 10))
        self.expense(20000, datetime.date(last, 3, 12))
        context = self.ctx(year=last)
        self.assertEqual(len(context['chart_labels']), 12)
        self.assertTrue(all(str(last) in label for label in context['chart_labels']), context['chart_labels'])
        self.assertTrue(context['chart_labels'][0].startswith('Jan'))
        self.assertEqual(context['income_data'][2], 50000.0)
        self.assertEqual(context['expense_data'][2], 20000.0)

    def test_each_month_has_its_income_expense_and_rate(self):
        m0, m1 = month_start(0), month_start(1)
        self.income(100000, m0)
        self.expense(40000, m0)
        self.income(80000, m1)
        self.expense(60000, m1)
        context = self.ctx()
        self.assertEqual(context['income_data'][-1], 100000.0)
        self.assertEqual(context['expense_data'][-1], 40000.0)
        self.assertEqual(context['balance_rate_data'][-1], 60.0)
        if len(context['income_data']) > 1:
            self.assertEqual(context['income_data'][-2], 80000.0)
            self.assertEqual(context['balance_rate_data'][-2], 25.0)

    def test_a_month_without_income_has_a_zero_rate(self):
        self.expense(5000, month_start(0))
        self.assertEqual(self.ctx()['balance_rate_data'][-1], 0)

    def test_loan_interest_lowers_the_months_rate_but_principal_does_not(self):
        m0 = month_start(0)
        self.income(100000, m0)
        self.expense(30000, m0)
        self.make_loan_repayment(principal=20000, interest=10000, when=m0)
        self.assertEqual(self.ctx()['balance_rate_data'][-1], 60.0)

    def test_the_expense_bar_includes_the_loan_interest_the_rate_counts(self):
        m0 = month_start(0)
        self.income(100000, m0)
        self.expense(30000, m0)
        self.make_loan_repayment(principal=20000, interest=10000, when=m0)
        context = self.ctx()
        self.assertEqual(context['income_data'][-1] - context['expense_data'][-1],
                         context['income_data'][-1] * context['balance_rate_data'][-1] / 100)

    def test_foreign_expenses_use_their_value_in_your_currency(self):
        self.seed_fx('80')
        m0 = month_start(0)
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food', date=m0, currency='$')
        self.assertEqual(self.ctx()['expense_data'][-1], 800.0)


class TestCapitalEventsToggle(AnalyticsBase):
    def setUp(self):
        super().setUp()
        self.m0 = month_start(0)
        self.income(100000, self.m0)
        self.expense(20000, self.m0)

    def capital(self, amount, exclude):
        return CapitalEvent.objects.create(user=self.user, account=self.bank, amount=D(str(amount)),
                                           date=self.m0, subtype='large_purchase', currency='₹',
                                           exclude_from_averages=exclude)

    def test_an_excluded_event_stays_out_by_default(self):
        self.capital(30000, exclude=True)
        context = self.ctx()
        self.assertEqual(context['expense_data'][-1], 20000.0)
        self.assertEqual(context['balance_rate_data'][-1], 80.0)

    def test_the_switch_adds_it_to_the_bar_and_the_rate(self):
        self.capital(30000, exclude=True)
        context = self.ctx(include_capital_events='1')
        self.assertEqual(context['expense_data'][-1], 50000.0)
        self.assertEqual(context['balance_rate_data'][-1], 50.0)

    def test_an_event_that_counts_is_in_the_rate_without_the_switch(self):
        self.capital(30000, exclude=False)
        self.assertEqual(self.ctx()['balance_rate_data'][-1], 50.0)

    def test_the_switch_does_not_double_count_an_event_that_already_counts(self):
        self.capital(30000, exclude=False)
        self.assertEqual(self.ctx(include_capital_events='1')['balance_rate_data'][-1], 50.0)


class TestCategoryBreakdown(AnalyticsBase):
    def test_categories_are_summed_and_sorted_largest_first(self):
        self.expense(100, category='Food')
        self.expense(300, category='Rent')
        self.expense(50, category='Food')
        context = self.ctx()
        self.assertEqual(context['cat_labels'], ['Rent', 'Food'])
        self.assertEqual(context['cat_data'], [300.0, 150.0])

    def test_only_the_selected_year_counts(self):
        self.expense(100)
        self.expense(900, datetime.date(today().year - 1, 6, 1))
        self.assertEqual(self.ctx()['cat_data'], [100.0])
        self.assertEqual(self.ctx(year=today().year - 1)['cat_data'], [900.0])

    def test_future_dated_rows_are_left_out_of_this_years_figures(self):
        self.expense(100)
        future = today() + datetime.timedelta(days=20)
        if future.year == today().year:
            self.expense(5000, future)
            context = self.ctx()
            self.assertEqual(context['cat_data'], [100.0])
            self.assertEqual(context['total_expense_ytd'], 100)


class TestYearToDateTotals(AnalyticsBase):
    def test_this_year_stops_at_today(self):
        self.income(1000)
        self.expense(400)
        future = today() + datetime.timedelta(days=15)
        if future.year == today().year:
            self.income(9000, future)
            self.expense(9000, future)
        context = self.ctx()
        self.assertEqual(context['total_income_ytd'], 1000)
        self.assertEqual(context['total_expense_ytd'], 400)

    def test_a_past_year_is_the_whole_year(self):
        last = today().year - 1
        self.income(50000, datetime.date(last, 1, 15))
        self.income(50000, datetime.date(last, 12, 15))
        self.expense(30000, datetime.date(last, 12, 31))
        context = self.ctx(year=last)
        self.assertEqual((context['total_income_ytd'], context['total_expense_ytd']), (100000, 30000))
        self.assertEqual(context['avg_balance_rate'], 70.0)

    def test_the_rate_uses_the_one_savings_definition(self):
        self.income(100000)
        self.income(2000, source_type='Cashback & Rewards')
        self.expense(30000)
        self.make_loan_repayment(principal=20000, interest=10000)
        # savings = 102000 - 30000 - 10000 = 62000 ; base = 100000
        self.assertEqual(self.ctx()['avg_balance_rate'], 62.0)

    def test_invested_adds_up_transfers_into_investment_accounts_only(self):
        funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('5000'), date=today())
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=other, amount=D('7000'), date=today())
        self.assertEqual(self.ctx()['total_invested_ytd'], D('5000'))

    def test_invested_from_a_foreign_account_is_converted(self):
        self.seed_fx('80')
        usd = Account.objects.create(user=self.user, name='Dollars', account_type='SAVINGS_ACCOUNT', balance=1000, currency='$')
        funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='$')
        Transfer.objects.create(user=self.user, from_account=usd, to_account=funds, amount=D('10'), date=today())
        self.assertEqual(self.ctx()['total_invested_ytd'], D('800'))


class TestHealthScore(AnalyticsBase):
    def score(self, income, spend):
        Income.objects.all().delete()
        Expense.objects.all().delete()
        cache.clear()
        if income:
            self.income(income)
        if spend:
            self.expense(spend)
        context = self.ctx()
        return context['health_score'], context['health_label']

    def test_negative_savings_is_at_risk(self):
        self.assertEqual(self.score(1000, 1500), (10, 'At Risk'))

    def test_a_small_positive_rate_needs_attention(self):
        self.assertEqual(self.score(1000, 950), (40, 'Caution'))
        self.assertEqual(self.score(1000, 910), (40, 'Caution'))

    def test_ten_to_thirty_percent_is_stable(self):
        self.assertEqual(self.score(1000, 900), (70, 'Stable'))
        self.assertEqual(self.score(1000, 710), (70, 'Stable'))

    def test_thirty_percent_or_more_is_a_wealth_builder(self):
        self.assertEqual(self.score(1000, 700), (95, 'Wealth Builder'))
        self.assertEqual(self.score(1000, 100), (95, 'Wealth Builder'))

    def test_no_data_counts_as_zero_percent(self):
        self.assertEqual(self.score(0, 0), (40, 'Caution'))


class TestInsightsMatchTheRate(AnalyticsBase):
    def test_the_saved_amount_in_the_insight_is_the_savings_the_rate_is_based_on(self):
        self.income(100000)
        self.expense(40000)
        self.make_loan_repayment(principal=5000, interest=10000)
        context = self.ctx()
        self.assertEqual(context['avg_balance_rate'], 50.0)
        text = ' '.join(context['insights'])
        self.assertIn('₹50,000', text)
        self.assertNotIn('₹60,000', text)
        self.assertIn('(50%)', text)

    def test_the_gap_to_thirty_percent_is_measured_against_the_same_base(self):
        self.income(100000)
        self.income(10000, source_type='Refund / Reimbursement')
        self.expense(85000)
        # savings = 110000 - 85000 = 25000, base = 100000 -> 25%: needs 5000 more for 30% of the base
        text = ' '.join(self.ctx()['insights'])
        self.assertIn('₹5,000 more', text)

    def test_overspending_says_by_how_much(self):
        self.income(1000)
        self.expense(1500)
        text = ' '.join(self.ctx()['insights'])
        self.assertIn('50% overspend', text)

    def test_a_dominant_category_is_called_out(self):
        self.income(1000)
        self.expense(500, category='Rent')
        text = ' '.join(self.ctx()['insights'])
        self.assertIn("'Rent' is consuming 50%", text)


class TestHealthBreakdown(AnalyticsBase):
    def metrics(self, **params):
        return {m['label']: m for m in self.ctx(**params)['health_breakdown']}

    def test_the_four_metrics_are_there(self):
        self.income(1000)
        self.assertEqual(list(self.metrics()), ['Savings Rate', 'Expense Growth', 'Consistency', 'Risk Buffer'])

    def test_savings_rate_status_follows_the_rate(self):
        self.income(1000)
        self.expense(700)
        self.assertEqual(self.metrics()['Savings Rate']['value'], '30.0%')
        self.assertEqual(self.metrics()['Savings Rate']['status'], 'success')

    def test_consistency_counts_the_last_completed_months_with_positive_savings(self):
        for offset, spend in ((1, 500), (2, 500), (3, 1500), (4, 500)):
            self.income(1000, month_start(offset))
            self.expense(spend, month_start(offset))
        metric = self.metrics()['Consistency']
        self.assertEqual(metric['value'], '3/10')
        self.assertEqual(metric['status'], 'danger')

    def test_expense_growth_compares_like_with_like(self):
        # three completed months at 1,000 expenses + 500 loan interest each: average 1,500
        for offset in (1, 2, 3):
            self.expense(1000, month_start(offset))
            self.make_loan_repayment(principal=700, interest=500, when=month_start(offset))
        # this year's spending to date, per month elapsed, is far below that
        self.expense(100)
        growth = self.metrics()['Expense Growth']
        self.assertLessEqual(float(growth['value'].rstrip('%')), 0)
        self.assertEqual(growth['status'], 'success')

    def test_risk_buffer_is_savings_over_the_monthly_average_spend(self):
        for offset in (1, 2, 3):
            self.expense(1000, month_start(offset))
        self.income(10000)
        # year-to-date savings = this month's income less whichever of those three months fall this year;
        # the average monthly spend over the last three completed months is 1,000
        spent_this_year = sum(1000 for offset in (1, 2, 3) if month_start(offset).year == today().year)
        expected = (10000 - spent_this_year) / 1000
        metric = self.metrics()['Risk Buffer']
        self.assertEqual(metric['value'], f'{expected:.0f} months')
        self.assertEqual(metric['status'], 'success')

    def test_the_summary_names_the_best_and_the_weakest_metric(self):
        self.income(1000)
        self.expense(100)
        summary = self.ctx()['health_summary']
        self.assertIn('excellent', summary)


class TestSankey(AnalyticsBase):
    def flows(self):
        return {(a, b): v for a, b, v in self.ctx()['sankey_data']}

    def test_income_splits_into_savings_and_living_expenses(self):
        self.income(1000)
        self.expense(300, category='Food')
        self.expense(100, category='Rent')
        flows = self.flows()
        self.assertEqual(flows[('Income', 'Savings')], 600.0)
        self.assertEqual(flows[('Income', 'Living Expenses')], 400.0)
        self.assertEqual(flows[('Living Expenses', 'Food')], 300.0)
        self.assertEqual(flows[('Living Expenses', 'Rent')], 100.0)

    def test_investments_come_out_of_the_surplus(self):
        funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        self.income(1000)
        self.expense(300)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('200'), date=today())
        flows = self.flows()
        self.assertEqual(flows[('Income', 'Investments')], 200.0)
        self.assertEqual(flows[('Income', 'Savings')], 500.0)

    def test_what_flows_out_equals_what_flows_in(self):
        self.income(1000)
        self.expense(300)
        self.expense(50, category='Rent')
        out = sum(v for a, b, v in self.ctx()['sankey_data'] if a == 'Income')
        self.assertEqual(out, 1000.0)

    def test_a_deficit_is_shown_as_extra_income(self):
        self.income(1000)
        self.expense(1500)
        flows = self.flows()
        self.assertEqual(flows[('Deficit (Overspending)', 'Income')], 500.0)
        self.assertEqual(flows[('Income', 'Living Expenses')], 1500.0)

    def test_only_the_five_biggest_categories_are_listed_and_the_rest_are_grouped(self):
        self.income(10000)
        for i, name in enumerate(['A', 'B', 'C', 'D', 'E', 'F', 'G']):
            Category.objects.get_or_create(user=self.user, name=name)
            self.expense(100 + i, category=name)
        flows = self.flows()
        listed = [b for (a, b) in flows if a == 'Living Expenses']
        self.assertEqual(len(listed), 6)
        self.assertEqual(flows[('Living Expenses', 'Other Expenses')], 100.0 + 101.0)

    def test_nothing_is_drawn_without_data(self):
        self.assertEqual(self.ctx()['sankey_data'], [])


class TestSmallCharts(AnalyticsBase):
    def test_weekday_totals_add_up_to_the_years_spending(self):
        self.expense(100, datetime.date(today().year, 1, 1))
        self.expense(50, datetime.date(today().year, 1, 2))
        self.expense(25, datetime.date(today().year, 1, 3))
        context = self.ctx()
        self.assertEqual(len(context['dow_data']), 7)
        self.assertEqual(sum(context['dow_data']), 175.0)
        jan1 = datetime.date(today().year, 1, 1)
        index = (jan1.weekday() + 1) % 7  # Sunday first
        self.assertEqual(context['dow_data'][index], 100.0)

    def test_the_burn_down_is_cumulative_to_today_then_projected(self):
        first = today().replace(day=1)
        self.expense(100, first)
        self.expense(200, today())
        context = self.ctx()
        days = calendar.monthrange(today().year, today().month)[1]
        self.assertEqual(len(context['burn_down_labels']), days)
        self.assertEqual(context['burn_down_spent'][today().day - 1], 300.0)
        if today().day < days:
            self.assertIsNone(context['burn_down_spent'][-1])
            self.assertEqual(context['burn_down_projection'][today().day - 1], 300.0)
            burn_rate = 300.0 / today().day
            self.assertAlmostEqual(context['burn_down_projection'][-1], 300.0 + burn_rate * (days - today().day), places=1)

    def test_the_budget_line_is_the_monthly_limit_spread_over_the_days(self):
        Category.objects.filter(user=self.user).update(limit=None)
        Category.objects.filter(user=self.user, name='Food').update(limit=D('3000'))
        self.expense(10)
        days = calendar.monthrange(today().year, today().month)[1]
        context = self.ctx()
        self.assertEqual(context['burn_down_budget'][days - 1], 3000.0)
        self.assertAlmostEqual(context['burn_down_budget'][0], 3000.0 / days, places=2)

    def test_no_burn_down_for_a_past_year(self):
        self.expense(10, datetime.date(today().year - 1, 5, 5))
        self.assertEqual(self.ctx(year=today().year - 1)['burn_down_labels'], [])

    def test_recurring_and_one_time_split_this_month(self):
        self.expense(300, description='Netflix (Recurring)')
        self.expense(200, description='Lunch')
        self.assertEqual(self.ctx()['recurring_split_data'], [300.0, 200.0])

    def test_an_empty_month_splits_into_zero_and_zero(self):
        self.assertEqual(self.ctx()['recurring_split_data'], [0.0, 0.0])


class TestPerformanceAndCache(AnalyticsBase):
    def test_the_page_stays_within_a_query_budget(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for offset in range(6):
            self.income(1000, month_start(offset))
            for category in ('Food', 'Rent', 'Travel'):
                self.expense(100, month_start(offset), category=category)
        with CaptureQueriesContext(connection) as queries:
            self.get()
        self.assertLessEqual(len(queries), 60, len(queries))

    def test_the_default_view_is_cached_and_a_new_expense_clears_it(self):
        from expenses.views.dashboard import AnalyticsView
        AnalyticsView._force_cache_in_testing = True
        try:
            self.income(1000)
            first = self.ctx()['total_income_ytd']
            self.income(500)
            self.assertEqual(self.ctx()['total_income_ytd'], first + 500)
        finally:
            AnalyticsView._force_cache_in_testing = False


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
        self.assertIn(url, self.get(year=last).content.decode())
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
