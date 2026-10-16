"""The Budget page uses the app's unified filter toolbar: the time-period menu picks the month,
search and Status narrow the categories, Sort orders them."""

import json
from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from expenses.filters.definitions import BUDGET_FILTERS
from expenses.models import Category, Expense
from expenses.tests.test_budget_api import Base, add_months

D = Decimal
TODAY = timezone.localdate()
CUR = TODAY.replace(day=1)
PREV = add_months(CUR, -1)


class BudgetFilterTests(Base):
    def setUp(self):
        super().setUp()
        Category.objects.create(user=self.user, name='Rent', limit=D('9000'))
        self.spend('Rent', 100)

    def page(self, **params):
        response = self.client.get(reverse('budget'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def names(self, response):
        return [r['name'] for r in response.context['rows']]

    def test_toolbar_is_the_shared_component_with_budget_options(self):
        html = self.page().content.decode()
        self.assertIn('id="tmr-toolbar-budget"', html)
        config = json.loads(html.split('id="tmr-filter-config-budget" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(config['time_periods'], ['this_month', 'last_month', 'custom'])
        self.assertEqual([f['key'] for f in config['filters']], ['status'])
        self.assertEqual([o['key'] for o in config['sort_options']], ['urgent', 'spent', 'limit', 'name'])
        self.assertTrue(BUDGET_FILTERS.calendar_months_only)

    def test_time_period_picks_the_calendar_month(self):
        self.assertEqual(self.page().context['month_start'], CUR)
        self.assertEqual(self.page(time_period='this_month').context['month_start'], CUR)
        self.assertEqual(self.page(time_period='last_month').context['month_start'], PREV)

    def test_custom_range_uses_the_month_of_its_end_date(self):
        some = date(2026, 4, 3)
        ctx = self.page(time_period='custom', start_date='2026-04-03', end_date='2026-04-20').context
        self.assertEqual(ctx['month_start'], some.replace(day=1))
        ctx = self.page(time_period='custom', start_date='2026-04-03').context
        self.assertEqual(ctx['month_start'], date(2026, 4, 1))

    def test_salary_cycle_never_moves_the_budget_month(self):
        profile = self.user.profile
        profile.salary_date = 25
        profile.save()
        ctx = self.page(time_period='this_month').context
        self.assertEqual(ctx['month_start'], CUR)
        self.assertFalse(ctx['applied_state']['cycle_active'])

    def test_unsupported_periods_fall_back_to_this_month(self):
        self.assertEqual(self.page(time_period='this_year').context['month_start'], CUR)
        self.assertEqual(self.page(time_period='bogus').context['month_start'], CUR)

    def test_older_month_and_year_links_still_work(self):
        ctx = self.page(month=PREV.month, year=PREV.year).context
        self.assertEqual(ctx['month_start'], PREV)

    def test_search_narrows_categories_but_not_the_month_totals(self):
        unfiltered = self.page().context['page']['total']
        response = self.page(search='ren')
        self.assertEqual(self.names(response), ['Rent'])
        self.assertEqual(response.context['page']['total'], unfiltered)

    def test_status_filter_accepts_several_values(self):
        self.assertEqual(sorted(self.names(self.page(status=['over', 'limit']))), ['Food', 'Fuel'])
        self.assertEqual(self.names(self.page(status='nolimit')), ['Fun'])
        self.assertEqual(self.names(self.page(status='ontrack')), ['Rent'])

    def test_status_and_search_combine(self):
        self.assertEqual(self.names(self.page(status='over', search='fu')), ['Fuel'])
        self.assertEqual(self.names(self.page(status='over', search='food')), [])

    def test_nothing_matching_shows_a_message_not_the_no_budgets_state(self):
        response = self.page(search='zzz')
        self.assertContains(response, 'No categories match')
        self.assertNotContains(response, 'No budgets yet')
        self.assertTrue(response.context['filters_active'])

    def test_sort_comes_from_the_toolbar(self):
        self.assertEqual(self.names(self.page(sort='name')), ['Food', 'Fuel', 'Fun', 'Rent'])
        self.assertEqual(self.names(self.page(sort='limit'))[0], 'Rent')
        self.assertEqual(self.names(self.page())[0], 'Fuel')   # urgent: the one that is over

    def test_unknown_sort_falls_back_to_urgent(self):
        self.assertEqual(self.names(self.page(sort='date_desc'))[0], 'Fuel')

    def test_htmx_partial_keeps_the_toolbar_inside_the_shell(self):
        response = self.client.get(reverse('budget'), {'status': 'over'}, HTTP_HX_REQUEST='true')
        html = response.content.decode()
        self.assertIn('id="budget-shell"', html)
        self.assertIn('tmr-toolbar-budget', html)
        self.assertIn('"status": ["over"]', html)

    def test_other_pages_default_date_filter_is_untouched(self):
        from expenses.filters.definitions import CATEGORY_LIST_FILTERS
        self.assertEqual(CATEGORY_LIST_FILTERS.date_field, 'date')
        self.assertIsNone(CATEGORY_LIST_FILTERS.time_periods)
        Expense.objects.create(user=self.user, date=TODAY - timedelta(days=1), amount=D('1'), description='x', category='Food', currency='₹')


class StatusTileTests(BudgetFilterTests):
    def tiles(self, **params):
        return {t['key']: t for t in self.page(**params).context['status_tiles']}

    def test_tiles_show_the_month_counts_and_link_to_the_status_filter(self):
        tiles = self.tiles()
        self.assertEqual([tiles[k]['count'] for k in ('over', 'limit', 'ontrack', 'nolimit')], [1, 1, 1, 1])
        self.assertIn('status=over', tiles['over']['url'])
        self.assertFalse(any(t['active'] for t in tiles.values()))

    def test_active_tile_links_back_to_the_unfiltered_page(self):
        tiles = self.tiles(status='over')
        self.assertTrue(tiles['over']['active'])
        self.assertNotIn('status=', tiles['over']['url'])
        self.assertIn('status=limit', tiles['limit']['url'])      # clicking another one switches

    def test_tiles_keep_other_filters(self):
        tiles = self.tiles(status='over', search='fu', sort='name')
        self.assertIn('search=fu', tiles['limit']['url'])
        self.assertIn('sort=name', tiles['limit']['url'])

    def test_tiles_render_as_pressed_buttons_that_swap_the_shell(self):
        html = self.page(status='over').content.decode()
        self.assertIn('aria-pressed="true"', html)
        self.assertIn('hx-target="#budget-shell"', html)

    def test_counts_stay_about_the_whole_month_when_filtered(self):
        self.assertEqual(self.tiles(status='over')['ontrack']['count'], 1)
