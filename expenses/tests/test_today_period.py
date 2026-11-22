"""The "Today" time period of the unified filter: it resolves to just today on every page that uses the filter, and the
menu, the toolbar label and the guide list it."""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.analytics.periods import COMPARE_BEFORE, build_period
from expenses.models import Account, Expense
from expenses.periods import resolve_period

ROOT = Path(__file__).resolve().parents[2]


class ResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('todayer', password='pw')

    def test_today_is_just_today(self):
        period = resolve_period(user=self.user, time_period='today', today=date(2026, 11, 22))
        self.assertEqual((period.start, period.end, period.key, period.is_cycle),
                         (date(2026, 11, 22), date(2026, 11, 22), 'today', False))

    def test_a_salary_cycle_does_not_change_it(self):
        self.user.profile.salary_date = 15
        self.user.profile.save()
        period = resolve_period(user=self.user, time_period='today', today=date(2026, 11, 22))
        self.assertEqual((period.start, period.end, period.is_cycle), (date(2026, 11, 22), date(2026, 11, 22), False))

    def test_it_is_not_mistaken_for_an_unknown_period(self):
        """(an unknown period falls back to this month)"""
        self.assertEqual(resolve_period(user=self.user, time_period='today', today=date(2026, 11, 22)).key, 'today')

    def test_analytics_compare_it_with_the_day_before(self):
        today = date(2026, 11, 22)
        period = build_period(self.user, {'time_period': 'today'}, today)
        self.assertEqual((period.start, period.end), (today, today))
        self.assertEqual(period.compare, (today - timedelta(days=1), today - timedelta(days=1)))
        self.assertEqual(period.compare_kind, COMPARE_BEFORE)


class PageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('todayer', password='pw')
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('1000'), currency='₹')
        for label, days in (('Chai today', 0), ('Dinner yesterday', 1), ('Lunch last week', 7)):
            Expense.objects.create(user=self.user, date=self.today - timedelta(days=days), amount=Decimal('50'),
                                   description=label, category='Food', account=account, base_amount=Decimal('50'))

    def test_all_transactions_shows_only_todays(self):
        html = self.client.get(reverse('all-transactions'), {'time_period': 'today'}).content.decode()
        self.assertIn('Chai today', html)
        self.assertNotIn('Dinner yesterday', html)
        self.assertNotIn('Lunch last week', html)

    def test_expenses_shows_only_todays(self):
        html = self.client.get(reverse('expense-list'), {'time_period': 'today'}).content.decode()
        self.assertIn('Chai today', html)
        self.assertNotIn('Dinner yesterday', html)

    def test_the_toolbar_says_today(self):
        html = self.client.get(reverse('all-transactions'), {'time_period': 'today'}).content.decode()
        label = html.split('class="tmr-time-label">', 1)[1].split('</span>', 1)[0]
        self.assertIn('Today', label)

    def test_the_dashboard_shows_a_daily_trend_for_today(self):
        response = self.client.get(reverse('home'), {'time_period': 'today'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['trend_title'], "Today's Expenses")
        self.assertTrue(response.context['trend_is_daily'])

    def test_analytics_open_on_today(self):
        self.assertEqual(self.client.get(reverse('analytics'), {'time_period': 'today'}).status_code, 200)

    def test_income_opens_on_today(self):
        self.assertEqual(self.client.get(reverse('income-list'), {'time_period': 'today'}).status_code, 200)


class MenuTests(TestCase):
    def test_both_menus_offer_today_first(self):
        """The salary-cycle menu (under its calendar heading) and the plain one."""
        script = (ROOT / 'static' / 'js' / 'tmr_filter.js').read_text()
        self.assertEqual(script.count('data-time-key="today"'), 2)
        plain = script.split("headerHtml = `\n        <div class=\"tmr-popover-header-row\">\n          <div class=\"tmr-section-label\">\n            <i class=\"bi bi-calendar\"", 1)[1]
        self.assertLess(plain.index('data-time-key="today"'), plain.index('data-time-key="this_month"'))

    def test_the_budget_page_still_offers_only_its_own_periods(self):
        from expenses.filters import definitions
        configs = [c for c in vars(definitions).values() if getattr(c, 'time_periods', None)]
        self.assertTrue(configs)
        for config in configs:
            self.assertNotIn('today', config.time_periods)

    def test_the_guide_lists_it_in_every_language(self):
        for suffix in ('', '.hi', '.mr'):
            text = (ROOT / 'guide-src' / '14-filters-and-search' / f'index{suffix}.md').read_text()
            self.assertIn('- **Today**\n- **This month**', text, suffix or 'en')
