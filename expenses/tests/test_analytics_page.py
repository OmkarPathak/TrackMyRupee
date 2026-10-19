"""The Analytics page: Spending, Cashflow and Net worth tabs under the unified filter toolbar.

The money rules (savings, savings rate, what counts as spending, currency conversion) are the app's single
definitions in expenses.savings; these tests check the page agrees with them, that every chart and tile links to
the transactions behind it, that the filters narrow the right things, and that the page stays cheap to build.
"""
import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import translation

from expenses.analytics import cashflow, spending, wealth
from expenses.analytics.common import Page, cache_key
from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    Income,
    Transfer,
    UserProfile,
)
from expenses.savings import savings_for_period

from .test_analytics_module import AnalyticsBase, month_start, today

D = Decimal
LAST_YEAR = today().year - 1
LAST_YEAR_RANGE = {'time_period': 'custom', 'start_date': f'{LAST_YEAR}-01-01', 'end_date': f'{LAST_YEAR}-12-31'}


class PageCase(AnalyticsBase):
    def page(self, tab='spend', **params):
        request = RequestFactory().get('/analytics/', params)
        request.user = User.objects.get(pk=self.user.pk)      # fresh, so a plan changed with .update() is seen
        return Page(request, tab)

    def tab(self, name='analytics', **params):
        return self.client.get(reverse(name), params)

    def capital(self, amount, when=None, exclude=True, **kw):
        return CapitalEvent.objects.create(user=self.user, account=self.bank, amount=D(str(amount)), date=when or today(),
                                           subtype='large_purchase', currency='₹', exclude_from_averages=exclude, **kw)

    def funds(self, name='Funds'):
        return Account.objects.create(user=self.user, name=name, account_type='MUTUAL_FUND', balance=0, currency='₹')


# ---------------------------------------------------------------------------------------------------------------
# access, address and shape of the page
# ---------------------------------------------------------------------------------------------------------------

class AccessTests(PageCase):
    def test_every_plan_can_open_every_tab(self):
        for tier in ('FREE', 'PLUS', 'PRO'):
            UserProfile.objects.filter(user=self.user).update(tier=tier)
            for name in ('analytics', 'analytics-cashflow', 'analytics-networth'):
                response = self.tab(name)
                self.assertEqual(response.status_code, 200, (tier, name))
                self.assertFalse(response.context['is_locked'])

    def test_login_is_required(self):
        self.client.logout()
        for name in ('analytics', 'analytics-cashflow', 'analytics-networth'):
            self.assertEqual(self.tab(name).status_code, 302)

    def test_the_old_trends_address_still_works_and_keeps_the_period(self):
        response = self.client.get(reverse('analytics-mom') + '?time_period=last_6_months')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], reverse('analytics-networth') + '?time_period=last_6_months')

    def test_a_plan_without_insights_sees_the_locked_page(self):
        with patch.object(type(self.user.profile), 'has_ai_access', new=False):
            response = self.tab()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_locked'])
        self.assertContains(response, reverse('pricing'))

    def test_an_htmx_request_gets_just_the_shell(self):
        response = self.client.get(reverse('analytics'), HTTP_HX_REQUEST='true')
        html = response.content.decode()
        self.assertIn('id="analytics-shell"', html)
        self.assertNotIn('<html', html)
        self.assertNotIn('id="analytics-shell"', html.split('id="analytics-shell"', 1)[1])

    def test_the_page_uses_the_unified_toolbar_and_has_one_title(self):
        html = self.tab().content.decode()
        self.assertEqual(html.count('class="tmr-toolbar-container"'), 1)
        self.assertEqual(html.count('data-analytics-title'), 1)
        self.assertIn('tmr-time-dropdown-btn', html)

    def test_each_tab_marks_itself_as_the_current_page(self):
        for name, key in (('analytics', 'spend'), ('analytics-cashflow', 'cashflow'), ('analytics-networth', 'networth')):
            context = self.tab(name).context
            active = [t['key'] for t in context['tabs'] if t['active']]
            self.assertEqual(active, [key])

    def test_the_tabs_carry_the_period_and_filters_along(self):
        context = self.tab(time_period='last_3_months', category='Food').context
        for t in context['tabs']:
            self.assertIn('time_period=last_3_months', t['url'])
            self.assertIn('category=Food', t['url'])

    def test_an_empty_account_renders_every_tab(self):
        for name in ('analytics', 'analytics-cashflow', 'analytics-networth'):
            self.assertEqual(self.tab(name).status_code, 200, name)

    def test_the_year_in_review_link_is_for_the_year_the_period_ends_in(self):
        self.expense(5)
        context = self.tab(**LAST_YEAR_RANGE).context
        self.assertEqual(context['wrapped_year'], LAST_YEAR)


class ToolbarStateTests(PageCase):
    def test_the_toolbar_filters_become_the_pages_filters(self):
        page = self.page(category=['Food', 'Rent'], account=str(self.bank.pk), capital_events='include')
        self.assertEqual(page.categories, ['Food', 'Rent'])
        self.assertEqual(page.account_ids, [self.bank.pk])
        self.assertTrue(page.include_capital_events)
        self.assertTrue(page.filters_active)

    def test_someone_elses_account_is_ignored(self):
        stranger = User.objects.create_user('stranger', password='pw-str-12345')
        theirs = Account.objects.create(user=stranger, name='Theirs', account_type='SAVINGS_ACCOUNT', balance=1)
        page = self.page(account=[str(theirs.pk), 'abc', str(self.bank.pk)])
        self.assertEqual(page.account_ids, [self.bank.pk])

    def test_the_default_period_is_this_year(self):
        page = self.page()
        self.assertEqual(page.period.key, 'this_year')
        self.assertEqual(page.state['time_period'], 'this_year')

    def test_the_filters_are_offered_in_the_toolbar(self):
        keys = [f.key for f in self.tab().context['filter_config'].filters]
        self.assertEqual(keys, ['category', 'account', 'capital_events'])


class TimezoneTests(PageCase):
    def test_today_is_the_users_local_date_not_the_utc_one(self):
        # 00:30 on 1 January in India is still 31 December in UTC
        with patch('expenses.analytics.common.timezone.localdate', return_value=datetime.date(2027, 1, 1)):
            page = self.page()
        self.assertEqual(page.today, datetime.date(2027, 1, 1))
        self.assertEqual(page.period.start, datetime.date(2027, 1, 1))


# ---------------------------------------------------------------------------------------------------------------
# the money: one definition, everywhere
# ---------------------------------------------------------------------------------------------------------------

class SavingsDefinitionTests(PageCase):
    """What the page shows for a period must equal expenses.savings for the same days."""

    def seed(self):
        for month, income in ((1, 100000), (2, 90000), (3, 110000)):
            when = datetime.date(LAST_YEAR, month, 5)
            self.income(income, when)
            self.income(2000, when, source_type='Cashback & Rewards')
            self.expense(30000, when, category='Food')
            self.expense(10000, when, category='Rent')
            self.make_loan_repayment(principal=20000, interest=4000, when=when)
        self.capital(50000, datetime.date(LAST_YEAR, 2, 20), exclude=True)
        self.capital(8000, datetime.date(LAST_YEAR, 3, 20), exclude=False)

    def test_a_period_agrees_with_the_savings_module_without_the_switch(self):
        self.seed()
        start, end = datetime.date(LAST_YEAR, 1, 1), datetime.date(LAST_YEAR, 12, 31)
        mine = self.page(**LAST_YEAR_RANGE).summary
        theirs = savings_for_period(self.user, start, end)
        self.assertEqual((mine.income, mine.spending, mine.saved, mine.rate),
                         (theirs.income, theirs.spending, theirs.savings, theirs.rate))

    def test_the_switch_adds_the_excluded_events_on_top(self):
        self.seed()
        base = self.page(**LAST_YEAR_RANGE).summary
        with_switch = self.page(capital_events='include', **LAST_YEAR_RANGE).summary
        self.assertEqual(with_switch.spending - base.spending, D('50000'))
        self.assertEqual(with_switch.capital_extra, D('50000'))

    def test_an_event_that_already_counts_is_not_counted_twice(self):
        self.income(100000)
        self.expense(20000)
        self.capital(30000, exclude=False)
        for params in ({}, {'capital_events': 'include'}):
            self.assertEqual(self.page(**params).summary.spending, D('50000'), params)

    def test_loan_interest_counts_as_spending_but_principal_does_not(self):
        self.income(100000)
        self.expense(30000)
        self.make_loan_repayment(principal=20000, interest=10000)
        summary = self.page().summary
        self.assertEqual(summary.spending, D('40000'))
        self.assertEqual(summary.rate, D('60'))

    def test_cashback_is_income_but_not_part_of_the_rate_base(self):
        self.income(100000)
        self.income(2000, source_type='Cashback & Rewards')
        self.expense(30000)
        summary = self.page().summary
        self.assertEqual(summary.income, D('102000'))
        self.assertEqual(summary.rate, D('72'))            # (102000 - 30000) / 100000

    def test_foreign_amounts_count_at_their_value_in_your_currency(self):
        self.seed_fx('80')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food', date=today(), currency='$')
        self.assertEqual(self.page().summary.spending, D('800'))

    def test_invested_adds_up_transfers_into_investment_accounts_only(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        funds = self.funds()
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('5000'), date=today())
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=other, amount=D('7000'), date=today())
        self.assertEqual(self.page().summary.invested, D('5000'))

    def test_invested_from_a_foreign_account_is_converted(self):
        self.seed_fx('80')
        usd = Account.objects.create(user=self.user, name='Dollars', account_type='SAVINGS_ACCOUNT', balance=1000, currency='$')
        funds = Account.objects.create(user=self.user, name='USFunds', account_type='MUTUAL_FUND', balance=0, currency='$')
        Transfer.objects.create(user=self.user, from_account=usd, to_account=funds, amount=D('10'), date=today())
        self.assertEqual(self.page().summary.invested, D('800'))

    def test_this_year_stops_at_today_so_future_entries_stay_out(self):
        self.income(1000)
        self.expense(400)
        future = today() + datetime.timedelta(days=15)
        if future.year == today().year:
            self.income(9000, future)
            self.expense(9000, future)
        summary = self.page().summary
        self.assertEqual((summary.income, summary.spending), (D('1000'), D('400')))

    def test_a_past_period_is_the_whole_of_it(self):
        self.income(50000, datetime.date(LAST_YEAR, 1, 15))
        self.income(50000, datetime.date(LAST_YEAR, 12, 15))
        self.expense(30000, datetime.date(LAST_YEAR, 12, 31))
        summary = self.page(**LAST_YEAR_RANGE).summary
        self.assertEqual((summary.income, summary.spending, summary.rate), (D('100000'), D('30000'), D('70')))

    def test_the_comparison_period_is_summed_the_same_way(self):
        self.income(1000, datetime.date(LAST_YEAR, 2, 1))
        self.expense(250, datetime.date(LAST_YEAR, 2, 2))
        page = self.page()
        self.assertEqual((page.compare.income, page.compare.spending), (D('1000'), D('250')))


class FilterTests(PageCase):
    def test_a_category_filter_narrows_spending_and_leaves_income_alone(self):
        self.income(10000)
        self.expense(100, category='Food')
        self.expense(900, category='Rent')
        self.make_loan_repayment(principal=500, interest=50)
        summary = self.page(category='Food').summary
        self.assertEqual(summary.income, D('10000'))
        self.assertEqual(summary.spending, D('100'))         # loan interest has no category, so it drops out

    def test_a_category_filter_narrows_capital_events_too(self):
        self.capital(5000, exclude=False, category='Food')
        self.capital(7000, exclude=False, category='Rent')
        self.assertEqual(self.page(category='Food').summary.spending, D('5000'))

    def test_an_account_filter_narrows_everything_that_has_an_account(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        self.income(1000)
        Income.objects.create(user=self.user, account=other, amount=D('300'), source_type='Salary', date=today(), currency='₹')
        self.expense(100)
        Expense.objects.create(user=self.user, account=other, amount=D('40'), category='Food', date=today(), currency='₹')
        summary = self.page(account=str(other.pk)).summary
        self.assertEqual((summary.income, summary.spending), (D('300'), D('40')))

    def test_category_names_that_differ_only_in_case_are_one_category(self):
        self.expense(100, category='Food')
        self.expense(50, category='food ')
        categories = self.page().summary.categories
        self.assertEqual(list(categories.values()), [D('150')])


# ---------------------------------------------------------------------------------------------------------------
# the Spending tab
# ---------------------------------------------------------------------------------------------------------------

class SpendingTabTests(PageCase):
    def data(self, **params):
        return spending.build(self.page('spend', **params))

    def test_each_bar_has_its_income_and_spending_and_a_link_to_its_days(self):
        self.income(100000, datetime.date(LAST_YEAR, 3, 10))
        self.expense(40000, datetime.date(LAST_YEAR, 3, 12))
        chart = self.data(**LAST_YEAR_RANGE)['in_out']
        self.assertEqual(chart['granularity'], 'month')
        self.assertEqual(len(chart['labels']), 12)
        self.assertEqual(chart['income'][2], 100000.0)
        self.assertEqual(chart['spending'][2], 40000.0)
        self.assertIn(f'start_date={LAST_YEAR}-03-01', chart['tips'][2]['href'])
        self.assertIn(f'end_date={LAST_YEAR}-03-31', chart['tips'][2]['href'])
        self.assertIn('time_period=custom', chart['tips'][2]['href'])

    def test_a_short_period_shows_spending_only_because_income_comes_in_lumps(self):
        self.income(100000)
        self.expense(10)
        chart = self.data(time_period='this_month')['in_out']
        self.assertIn(chart['granularity'], ('day', 'week'))
        self.assertIsNone(chart['income'])

    def test_a_bar_includes_loan_interest_but_not_principal(self):
        self.expense(30000, datetime.date(LAST_YEAR, 3, 3))
        self.make_loan_repayment(principal=20000, interest=10000, when=datetime.date(LAST_YEAR, 3, 4))
        self.assertEqual(self.data(**LAST_YEAR_RANGE)['in_out']['spending'][2], 40000.0)

    def test_the_switch_adds_a_capital_event_to_its_bar(self):
        self.capital(30000, datetime.date(LAST_YEAR, 3, 3), exclude=True)
        self.assertEqual(self.data(**LAST_YEAR_RANGE)['in_out']['spending'][2], 0.0)
        self.assertEqual(self.data(capital_events='include', **LAST_YEAR_RANGE)['in_out']['spending'][2], 30000.0)

    def test_categories_are_summed_and_ranked_largest_first_with_a_share(self):
        self.expense(100, category='Food')
        self.expense(300, category='Rent')
        self.expense(50, category='Food')
        rows = self.data()['categories']
        self.assertEqual([r['name'] for r in rows], ['Rent', 'Food'])
        self.assertEqual(rows[0]['amount'], '₹300')
        self.assertEqual(rows[0]['share_text'], '67%')
        self.assertEqual(rows[0]['width'], 100.0)

    def test_only_the_period_counts(self):
        self.expense(100)
        self.expense(900, datetime.date(LAST_YEAR, 6, 1))
        self.assertEqual([r['amount'] for r in self.data()['categories']], ['₹100'])
        self.assertEqual([r['amount'] for r in self.data(**LAST_YEAR_RANGE)['categories']], ['₹900'])

    def test_the_biggest_six_are_listed_and_the_rest_are_grouped(self):
        for i, name in enumerate('ABCDEFGH'):
            Category.objects.get_or_create(user=self.user, name=name)
            self.expense(100 + i, category=name)
        rows = self.data()['categories']
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[-1]['name'], 'Everything else')
        self.assertEqual(rows[-1]['amount'], '₹201')        # the two smallest, 100 + 101

    def test_capital_events_show_up_under_their_category_when_they_count(self):
        self.capital(40000, exclude=True, category='Electronics')
        self.assertEqual(self.data()['categories'], [])
        names = [r['name'] for r in self.data(capital_events='include')['categories']]
        self.assertEqual(names, ['Electronics'])

    def test_a_capital_event_without_a_category_is_named_after_its_type(self):
        self.capital(40000, exclude=False)
        self.assertEqual(self.data()['categories'][0]['name'], 'Large Purchase')

    def test_each_category_row_links_to_that_category_in_all_transactions(self):
        self.expense(100, category='Food')
        href = self.data()['categories'][0]['href']
        self.assertIn(reverse('all-transactions'), href)
        self.assertIn('category=Food', href)
        self.assertIn('type=EXPENSE', href)

    def test_a_category_changes_against_the_comparison_period(self):
        self.expense(200, datetime.date(LAST_YEAR, 3, 3), category='Food')
        self.expense(300, category='Food')
        row = self.data()['categories'][0]
        self.assertEqual(row['delta']['text'], '▲ 50%')
        self.assertEqual(row['delta']['tone'], 'bad')

    def test_cumulative_spending_adds_up_day_by_day(self):
        first = today().replace(day=1)
        self.expense(100, first)
        self.expense(200, today())
        chart = self.data(time_period='this_month')['cumulative']
        self.assertEqual(chart['current'][-1], 300.0)
        self.assertEqual(chart['current'][0], 100.0)
        self.assertEqual(chart['current'], sorted(chart['current']))

    def test_cumulative_spending_lines_up_with_the_comparison_period_by_day(self):
        prev = month_start(1)
        self.expense(50, prev)
        self.expense(75, prev + datetime.timedelta(days=1))
        self.expense(10, today().replace(day=1))
        chart = self.data(time_period='this_month')['cumulative']
        self.assertEqual(chart['compare'][0], 50.0)
        self.assertEqual(chart['compare'][1], 125.0)

    def test_a_comparison_with_no_spending_is_left_out(self):
        self.expense(10)
        self.assertIsNone(self.data()['cumulative']['compare'])

    def test_the_budget_pace_spreads_the_months_limit_over_the_days(self):
        for category in Category.objects.filter(user=self.user):
            category.limit = D('3000') if category.name == 'Food' else None
            category.save()
        self.expense(10)
        page = self.page('spend', time_period='this_month')
        data = spending.cumulative_chart(page)
        days = page.period.full_days
        self.assertAlmostEqual(data['budget'][0], 3000.0 / days, places=2)
        self.assertAlmostEqual(data['budget'][-1], 3000.0 * page.period.days / days, places=2)

    def test_top_categories_over_time_follow_the_bars(self):
        self.expense(100, datetime.date(LAST_YEAR, 3, 3), category='Food')
        self.expense(30, datetime.date(LAST_YEAR, 4, 3), category='Food')
        self.expense(500, datetime.date(LAST_YEAR, 4, 4), category='Rent')
        data = self.data(**LAST_YEAR_RANGE)['trend']
        self.assertEqual(data['names'], ['Rent', 'Food'])
        self.assertEqual(data['series'][1][2:4], [100.0, 30.0])
        self.assertEqual(len(data['tips']), len(data['labels']))

    def test_the_tiles_are_formatted_and_link_to_their_transactions(self):
        self.income(5000)
        self.expense(1200)
        tiles = {t['key']: t for t in self.data()['tiles']}
        self.assertEqual(tiles['income']['value'], '₹5,000')
        self.assertEqual(tiles['spending']['value'], '₹1,200')
        self.assertEqual(tiles['rate']['value'], '76.0%')
        self.assertIn('type=INCOME', tiles['income']['href'])
        for kind in ('EXPENSE', 'LOAN', 'CAPITAL_EVENT'):
            self.assertIn(f'type={kind}', tiles['spending']['href'])

    def test_tile_changes_say_whether_they_are_good_or_bad(self):
        self.income(1000, datetime.date(LAST_YEAR, 3, 3))
        self.expense(100, datetime.date(LAST_YEAR, 3, 3))
        self.income(2000)
        self.expense(300)
        tiles = {t['key']: t for t in self.data()['tiles']}
        self.assertEqual((tiles['income']['delta'], tiles['income']['delta_class']), ('▲ 100%', 'good'))
        self.assertEqual((tiles['spending']['delta'], tiles['spending']['delta_class']), ('▲ 200%', 'bad'))

    def test_the_sentence_names_the_saved_amount_and_the_rate_it_is_based_on(self):
        self.income(100000)
        self.expense(40000)
        self.make_loan_repayment(principal=5000, interest=10000)
        text = self.data()['insight']
        self.assertIn('₹50,000', text)
        self.assertIn('(50%)', text)

    def test_with_no_income_the_sentence_says_so_instead_of_inventing_a_rate(self):
        self.expense(10)
        self.assertIn('No income', self.data()['insight'])


# ---------------------------------------------------------------------------------------------------------------
# the Cashflow tab
# ---------------------------------------------------------------------------------------------------------------

class SankeyTests(PageCase):
    def flow(self, **params):
        return cashflow.sankey(self.page('cashflow', **params))

    def links(self, **params):
        return {(f['source'], f['target']): f['value'] for f in self.flow(**params)['links']}

    def test_income_splits_into_costs_and_what_was_kept(self):
        self.income(1000)
        self.expense(300, category='Food')
        self.expense(100, category='Rent')
        links = self.links()
        self.assertEqual(links[('income', 'living')], 400.0)
        self.assertEqual(links[('income', 'kept')], 600.0)
        self.assertEqual(links[('living', 'cat:food')], 300.0)
        self.assertEqual(links[('living', 'cat:rent')], 100.0)

    def test_investing_comes_out_of_what_is_left_and_names_the_account(self):
        funds = self.funds('Zerodha')
        self.income(1000)
        self.expense(300)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('200'), date=today())
        links = self.links()
        self.assertEqual(links[('income', 'invested')], 200.0)
        self.assertEqual(links[('income', 'kept')], 500.0)
        self.assertEqual(links[('invested', f'acc:{funds.pk}')], 200.0)

    def test_what_flows_out_of_income_equals_income(self):
        self.income(1000)
        self.expense(300)
        self.expense(50, category='Rent')
        out = sum(v for (src, _), v in self.links().items() if src == 'income')
        self.assertEqual(out, 1000.0)

    def test_every_block_is_fed_exactly_as_much_as_it_passes_on(self):
        funds = self.funds()
        self.income(1000)
        self.expense(300, category='Food')
        self.make_loan_repayment(principal=100, interest=40)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('200'), date=today())
        flow = self.flow()
        inflow, outflow = {}, {}
        for f in flow['links']:
            inflow[f['target']] = inflow.get(f['target'], 0) + f['value']
            outflow[f['source']] = outflow.get(f['source'], 0) + f['value']
        for node in flow['nodes']:
            if node['id'] in inflow and node['id'] in outflow:
                self.assertAlmostEqual(inflow[node['id']], outflow[node['id']], places=2, msg=node['id'])

    def test_spending_more_than_earned_shows_a_shortfall_feeding_the_costs(self):
        self.income(1000)
        self.expense(1500)
        flow = self.flow()
        ids = {n['id'] for n in flow['nodes']}
        self.assertIn('shortfall', ids)
        self.assertNotIn('kept', ids)
        links = {(f['source'], f['target']): f['value'] for f in flow['links']}
        self.assertEqual(links[('shortfall', 'living')], 500.0)
        self.assertEqual(links[('income', 'living')], 1000.0)

    def test_only_the_five_biggest_categories_are_listed_and_the_rest_grouped(self):
        self.income(10000)
        for i, name in enumerate('ABCDEFG'):
            Category.objects.get_or_create(user=self.user, name=name)
            self.expense(100 + i, category=name)
        flow = self.flow()
        children = [n for n in flow['nodes'] if n['level'] == 2]
        self.assertEqual(len(children), 6)
        rest = next(n for n in children if n['id'] == 'cat:rest')
        self.assertEqual(rest['value'], 201.0)

    def test_nothing_is_drawn_without_data(self):
        self.assertEqual(self.flow()['nodes'], [])
        self.assertFalse(cashflow.build(self.page('cashflow'))['has_flow'])

    def test_every_block_links_to_the_transactions_behind_it(self):
        self.income(1000)
        self.expense(300, category='Food')
        self.make_loan_repayment(principal=100, interest=40)
        by_id = {n['id']: n for n in self.flow()['nodes']}
        self.assertIn('type=INCOME', by_id['income']['href'])
        self.assertIn('type=LOAN', by_id['interest']['href'])
        self.assertIn('category=Food', by_id['cat:food']['href'])

    def test_shares_are_of_income_and_changes_are_against_the_comparison(self):
        self.income(1000)
        self.expense(200, datetime.date(LAST_YEAR, 3, 3), category='Food')
        self.expense(300, category='Food')
        node = next(n for n in self.flow()['nodes'] if n['id'] == 'cat:food')
        self.assertEqual(node['share_text'], '30%')
        self.assertEqual(node['delta']['text'], '▲ 50%')

    def test_the_takeaways_say_the_biggest_cost_and_what_happened_to_the_money(self):
        self.income(1000)
        self.expense(500, category='Rent')
        text = ' '.join(self.flow()['takeaways'])
        self.assertIn('Rent is your biggest cost', text)
        self.assertIn('₹500', text)

    def test_overspending_is_called_out_in_words(self):
        self.income(1000)
        self.expense(1500)
        self.assertIn('more than you earned', ' '.join(self.flow()['takeaways']))

    def test_investing_more_than_you_kept_is_not_called_overspending(self):
        funds = self.funds()
        self.income(1000)
        self.expense(300)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('900'), date=today())
        text = ' '.join(self.flow()['takeaways'])
        self.assertNotIn('more than you earned', text)
        self.assertIn('paid from savings', text)
        shortfall = next(n for n in self.flow()['nodes'] if n['id'] == 'shortfall')
        self.assertEqual(shortfall['label'], 'Paid from savings')
        self.assertEqual(shortfall['value'], 200.0)

    def test_the_flow_list_orders_costs_with_their_children_beneath(self):
        self.income(1000)
        self.expense(300, category='Food')
        rows = self.flow()['rows']
        ids = [r['id'] for r in rows]
        self.assertLess(ids.index('living'), ids.index('cat:food'))
        self.assertLess(ids.index('cat:food'), ids.index('kept'))


class HealthTests(PageCase):
    def rows(self, **params):
        return {r['label']: r for r in cashflow.health(self.page('cashflow', **params))}

    def test_there_are_four_plain_numbers_and_no_score(self):
        self.income(1000)
        self.assertEqual(list(self.rows()), ['Savings rate', 'Spending trend', 'Emergency fund', 'Months in the black'])

    def test_the_savings_rate_follows_the_rate(self):
        self.income(1000)
        self.expense(700)
        row = self.rows()['Savings rate']
        self.assertEqual((row['value'], row['tone']), ('30%', 'good'))
        self.expense(400)
        self.assertEqual(self.rows()['Savings rate']['tone'], 'bad')

    def test_the_spending_trend_is_against_the_comparison(self):
        self.expense(100, datetime.date(LAST_YEAR, 3, 3))
        self.expense(110)
        row = self.rows()['Spending trend']
        self.assertEqual((row['value'], row['tone']), ('+10%', 'warn'))

    def test_the_spending_trend_needs_something_to_compare_with(self):
        self.expense(100)
        self.assertEqual(self.rows()['Spending trend']['value'], '—')

    def test_the_emergency_fund_is_cash_in_months_of_spending(self):
        self.expense(10000, month_start(0))
        Account.objects.filter(pk=self.bank.pk).update(balance=D('60000'))
        row = self.rows(**{'time_period': 'custom', 'start_date': str(today() - datetime.timedelta(days=29)),
                           'end_date': str(today())})['Emergency fund']
        self.assertIn('months', row['value'])

    def test_months_in_the_black_counts_months_with_savings(self):
        for offset, spend in ((1, 500), (2, 500), (3, 1500)):
            self.income(1000, month_start(offset))
            self.expense(spend, month_start(offset))
        start = month_start(3)
        row = self.rows(time_period='custom', start_date=str(start), end_date=str(today()))['Months in the black']
        self.assertEqual(row['value'], '2 of 3')

    def test_months_in_the_black_needs_two_active_months(self):
        self.income(1000)
        self.assertEqual(self.rows()['Months in the black']['value'], '—')


class ComparisonChartTests(PageCase):
    def test_it_lines_the_period_up_against_the_one_before_bar_for_bar(self):
        self.expense(100, datetime.date(LAST_YEAR, 3, 3))
        self.expense(300, datetime.date(today().year, 1, 3))
        chart = cashflow.comparison_chart(self.page('cashflow'))
        self.assertEqual(chart['title'], 'This year against last year')
        self.assertEqual(chart['current'][0], 300.0)
        self.assertEqual(chart['compare'][2], 100.0)

    def test_a_comparison_with_nothing_in_it_is_left_out(self):
        self.expense(100)
        chart = cashflow.comparison_chart(self.page('cashflow'))
        self.assertIsNone(chart['compare'])
        self.assertIn('Nothing was recorded', chart['hint'])


# ---------------------------------------------------------------------------------------------------------------
# the Net worth tab
# ---------------------------------------------------------------------------------------------------------------

class NetWorthTabTests(PageCase):
    tier = 'PRO'

    def series(self, **params):
        response = self.tab('analytics-networth', **params)
        self.assertEqual(response.status_code, 200)
        return response, response.context['series']

    def test_the_series_ends_with_the_current_net_worth(self):
        self.expense(250)
        _r, series = self.series()
        current, _balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(series['total'][-1], float(current))

    def test_a_principal_repayment_does_not_move_net_worth(self):
        from expenses.models import Loan
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'),
                                   duration_months=12, start_date=today() - datetime.timedelta(days=200), is_active=True)
        Account.objects.filter(pk=self.bank.pk).update(balance=D('10000'))
        mid = (today().replace(day=1) - datetime.timedelta(days=1)).replace(day=15)
        from expenses.models import LoanRepayment
        LoanRepayment.objects.create(loan=loan, from_account=self.bank, date=mid, amount=D('1000'),
                                     principal_portion=D('1000'), interest_portion=D('0'))
        _r, series = self.series(time_period='last_3_months')
        self.assertEqual(series['total'][-1], series['total'][-2])

    def test_the_breakdown_adds_up_to_net_worth_and_liabilities_are_negative(self):
        self.funds().__class__.objects.filter(name='Funds').delete()
        Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('-30000'), currency='₹')
        Account.objects.create(user=self.user, name='MF', account_type='MUTUAL_FUND', balance=D('20000'), currency='₹')
        _r, series = self.series()
        groups = {g['key']: g['data'] for g in series['groups']}
        for i, total in enumerate(series['total']):
            self.assertAlmostEqual(sum(g[i] for g in groups.values()), total, places=1)
        self.assertLess(groups['g_debt'][-1], 0)

    def test_there_are_never_more_than_twelve_points(self):
        self.expense(10, datetime.date(LAST_YEAR - 2, 1, 5))
        _r, series = self.series(time_period='all')
        self.assertLessEqual(len(series['total']), 12)

    def test_a_free_plan_looks_back_only_two_months_and_is_told_why(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        self.expense(10, datetime.date(LAST_YEAR, 1, 5))
        response, series = self.series(time_period='all')
        self.assertTrue(response.context['limited'])
        self.assertContains(response, reverse('pricing'))
        first_month = today().replace(day=1)
        index = first_month.year * 12 + first_month.month - 1 - 1
        limit_start = datetime.date(index // 12, index % 12 + 1, 1)
        self.assertGreaterEqual(wealth.history_start(self.page('networth')), limit_start)
        self.assertLessEqual(len(series['total']), 12)

    def test_a_free_plan_inside_its_limit_is_not_nagged(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        self.expense(10)
        response, _series = self.series(time_period='this_month')
        self.assertFalse(response.context['limited'])
        self.assertNotContains(response, 'Viewing limited financial history')

    def test_a_paid_plan_is_not_limited(self):
        self.expense(10, datetime.date(LAST_YEAR, 1, 5))
        response, _ = self.series(time_period='all')
        self.assertFalse(response.context['limited'])

    def test_idle_against_growing_counts_only_what_you_hold(self):
        Account.objects.filter(user=self.user).delete()
        Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('80000'), currency='₹')
        Account.objects.create(user=self.user, name='MF', account_type='MUTUAL_FUND', balance=D('20000'), currency='₹')
        Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('-30000'), currency='₹')
        Account.objects.create(user=self.user, name='Overdrawn', account_type='SAVINGS_ACCOUNT', balance=D('-9000'), currency='₹')
        rows = self.tab('analytics-networth').context['allocation']
        self.assertEqual([r['share'] for r in rows], [80.0, 20.0])

    def test_a_dollar_account_counts_at_its_value_in_your_currency(self):
        self.seed_fx('80')
        Account.objects.filter(user=self.user).delete()
        Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('80000'), currency='₹')
        Account.objects.create(user=self.user, name='US', account_type='MUTUAL_FUND', balance=D('250'), currency='$')
        rows = self.tab('analytics-networth').context['allocation']
        self.assertEqual([r['share'] for r in rows], [80.0, 20.0])

    def test_a_new_user_sees_an_empty_state_not_invented_numbers(self):
        Account.objects.filter(user=self.user).delete()
        response = self.tab('analytics-networth')
        self.assertFalse(response.context['has_data'])
        self.assertEqual(response.context['allocation'], [])
        self.assertContains(response, 'Nothing to show for this period')

    def test_categories_rising_three_full_months_in_a_row_are_listed(self):
        for months_back, amount in ((3, 100), (2, 150), (1, 200)):
            self.expense(amount, month_start(months_back), category='Food')
        for months_back, amount in ((3, 100), (2, 90), (1, 200)):
            self.expense(amount, month_start(months_back), category='Rent')
        rows = self.tab('analytics-networth').context['rising']
        self.assertEqual([r['name'] for r in rows], ['Food'])
        self.assertIn('category=Food', rows[0]['href'])

    def test_the_current_part_month_is_not_mistaken_for_a_rise(self):
        for months_back, amount in ((3, 100), (2, 150)):
            self.expense(amount, month_start(months_back), category='Food')
        self.expense(900, today(), category='Food')
        self.assertEqual(self.tab('analytics-networth').context['rising'], [])

    def test_each_point_links_to_what_changed_up_to_it(self):
        self.expense(10)
        _r, series = self.series(time_period='last_3_months')
        for tip in series['tips']:
            self.assertIn(reverse('all-transactions'), tip['href'])


# ---------------------------------------------------------------------------------------------------------------
# links, caching and cost
# ---------------------------------------------------------------------------------------------------------------

class ClickThroughTests(PageCase):
    def listed(self, href):
        response = self.client.get(href)
        self.assertEqual(response.status_code, 200)
        return response, list(response.context['transactions'])

    def test_a_category_row_opens_exactly_the_rows_it_adds_up(self):
        self.expense(100, category='Food')
        self.expense(50, category='Food')
        self.expense(900, category='Rent')
        row = next(r for r in spending.build(self.page())['categories'] if r['name'] == 'Food')
        _r, rows = self.listed(row['href'])
        self.assertEqual(sum(t['unified_amount'] for t in rows), D('150'))
        self.assertEqual({t['cat'] for t in rows}, {'Food'})

    def test_a_bar_opens_the_transactions_of_its_days_only(self):
        self.expense(100, datetime.date(LAST_YEAR, 3, 3))
        self.expense(900, datetime.date(LAST_YEAR, 4, 3))
        tip = spending.build(self.page(**LAST_YEAR_RANGE))['in_out']['tips'][2]
        _r, rows = self.listed(tip['href'])
        self.assertEqual([t['unified_amount'] for t in rows], [D('100')])

    def test_filters_travel_with_the_link(self):
        self.expense(100, category='Food')
        self.expense(400, category='Rent')
        tip = spending.build(self.page(category='Food'))['in_out']['tips'][-1]
        self.assertIn('category=Food', tip['href'])
        _r, rows = self.listed(tip['href'])
        self.assertEqual({t['cat'] for t in rows}, {'Food'})

    def test_an_income_link_does_not_carry_a_category_it_could_never_match(self):
        self.income(500)
        self.expense(100, category='Food')
        tiles = {t['key']: t for t in spending.build(self.page(category='Food'))['tiles']}
        self.assertNotIn('category=', tiles['income']['href'])
        _r, rows = self.listed(tiles['income']['href'])
        self.assertEqual(len(rows), 1)

    def test_every_link_on_every_tab_opens(self):
        self.income(1000)
        self.expense(300, category='Food')
        self.make_loan_repayment(principal=100, interest=40)
        self.capital(500, exclude=False)
        funds = self.funds()
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('200'), date=today())
        hrefs = set()
        for data in (spending.build(self.page('spend')), cashflow.build(self.page('cashflow'))):
            hrefs.update(t['href'] for t in data['tiles'])
            for key in ('categories',):
                hrefs.update(r['href'] for r in data.get(key, []))
        flow = cashflow.build(self.page('cashflow'))
        hrefs.update(n['href'] for n in flow['sankey']['nodes'] if n['href'])
        hrefs.update(r['href'] for r in flow['health'])
        for href in hrefs:
            self.assertEqual(self.client.get(href).status_code, 200, href)


class CacheTests(PageCase):
    def test_a_finished_tab_is_kept_and_a_new_expense_clears_it(self):
        with override_settings(ANALYTICS_CACHE_SECONDS=300):
            self.income(1000)
            first = self.tab().context['tiles'][0]['value']
            self.income(500)
            self.assertEqual(self.tab().context['tiles'][0]['value'], '₹1,500')
            self.assertNotEqual(first, '₹1,500')

    def test_a_second_visit_is_served_from_the_cache(self):
        with override_settings(ANALYTICS_CACHE_SECONDS=300):
            self.tab()
            with CaptureQueriesContext(connection) as warm:
                self.tab()
            cache.clear()
            with CaptureQueriesContext(connection) as cold:
                self.tab()
        self.assertLess(len(warm), len(cold))

    def test_each_filter_combination_and_language_has_its_own_entry(self):
        keys = {cache_key(self.page(**params)) for params in ({}, {'category': 'Food'}, {'time_period': 'last_month'})}
        self.assertEqual(len(keys), 3)
        with translation.override('hi'):
            hindi = cache_key(self.page())
        self.assertNotIn(hindi, keys)

    def test_the_net_worth_points_are_kept_until_something_changes(self):
        with override_settings(ANALYTICS_CACHE_SECONDS=300):
            self.tab('analytics-networth')
            with CaptureQueriesContext(connection) as warm:
                self.tab('analytics-networth')
            self.expense(5)
            with CaptureQueriesContext(connection) as after_write:
                self.tab('analytics-networth')
        self.assertLess(len(warm), len(after_write))


class CostTests(PageCase):
    def populate(self, months):
        for offset in range(months):
            self.income(1000, month_start(offset))
            for category in ('Food', 'Rent', 'Travel'):
                self.expense(100, month_start(offset), category=category)

    def queries(self, name):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.tab(name).status_code, 200)
        return len(ctx)

    def measure(self, name='analytics'):
        """Queries for a request, after one that lets the page's shared chrome (sidebar, notifications) cache itself."""
        self.queries(name)
        cache.delete(f'analytics_version_{self.user.id}')      # but make the page itself do its work again
        return self.queries(name)

    def test_the_cost_does_not_grow_with_the_number_of_transactions(self):
        self.populate(2)
        small = self.measure()
        for _ in range(4):
            self.populate(11)
        large = self.measure()
        self.assertEqual(small, large)
        self.assertLessEqual(large, 40)

    def test_the_cashflow_tab_stays_within_a_query_budget(self):
        self.populate(8)
        self.assertLessEqual(self.queries('analytics-cashflow'), 80)

    def test_the_net_worth_tab_does_a_bounded_amount_of_work(self):
        self.populate(8)
        self.assertLessEqual(self.queries('analytics-networth'), 400)
