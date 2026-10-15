from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.test import RequestFactory, TestCase
from django.urls import reverse

from expenses.services import SalaryAnalysisService
from expenses.templatetags.currency_filters import humanize_currency
from expenses.templatetags.digit_filters import compact_amount, ind_comma, money_exact
from expenses.templatetags.expense_extras import (
    category_icon,
    payment_bg,
    payment_color,
    sum_base_amounts,
    url_replace,
)

User = get_user_model()
PAYLOAD = '</script><script>alert(1)</script>'


class FilterToolbarXssTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('x1', 'x1@example.com', 'pw12345!x')
        self.client.force_login(self.user)

    def test_search_param_cannot_break_out_of_script(self):
        for name in ('expense-list', 'income-list', 'account-list'):
            html = self.client.get(reverse(name), {'search': PAYLOAD}).content.decode()
            self.assertNotIn('</script><script>alert(1)', html, name)

    def test_applied_state_still_round_trips(self):
        import json
        import re
        html = self.client.get(reverse('expense-list'), {'search': 'a<b&c'}).content.decode()
        raw = re.search(r'id="tmr-applied-state-[^"]*"[^>]*>\s*(.*?)\s*</script>', html, re.S).group(1)
        self.assertEqual(json.loads(raw)['search'], 'a<b&c')


class DashboardInsightEscapingTest(TestCase):
    def test_account_name_is_escaped_in_maturity_insight_source(self):
        import inspect

        from expenses.views import dashboard
        src = inspect.getsource(dashboard)
        self.assertIn("'name': escape(mat_acc.name)", src)


class NumberFilterTest(TestCase):
    def test_rollover_at_unit_boundaries(self):
        self.assertEqual(compact_amount(999999, 'USD'), '1M')
        self.assertEqual(compact_amount(9999999, '₹'), '1Cr')
        self.assertEqual(humanize_currency(999999, '$'), '1M')
        self.assertEqual(humanize_currency(9999999, '₹'), '1Cr')
        self.assertEqual(humanize_currency(199999, '₹'), '2L')

    def test_regular_values_unchanged(self):
        self.assertEqual(compact_amount(150000, '₹'), '1.5L')
        self.assertEqual(compact_amount(2500000, 'USD'), '2.5M')
        self.assertEqual(humanize_currency(12500000, '₹'), '1.2Cr')

    def test_no_negative_zero(self):
        self.assertEqual(compact_amount(-0.4), '0')
        self.assertEqual(compact_amount(-1500), '-1,500')

    def test_non_finite_values_do_not_crash(self):
        for f in (ind_comma, compact_amount, humanize_currency):
            self.assertEqual(str(f(float('nan'))), 'nan')
            self.assertEqual(str(f(float('inf'))), 'inf')
        self.assertEqual(str(ind_comma(float('inf'), 'USD')), 'inf')
        self.assertEqual(money_exact('abc'), 'abc')


class ExpenseExtrasTest(TestCase):
    def test_payment_helpers_accept_none(self):
        self.assertEqual(payment_color(None), '#4A4A46')
        self.assertEqual(payment_bg(None), '#F2F0EB')
        self.assertEqual(payment_color(' UPI '), '#9C5400')

    def test_sum_base_amounts_ignores_none(self):
        items = [SimpleNamespace(base_amount=Decimal('5')), SimpleNamespace(base_amount=None)]
        self.assertEqual(sum_base_amounts(items), Decimal('5'))

    def test_url_replace_none_clears_param(self):
        request = RequestFactory().get('/x/?page=3&q=a')
        out = url_replace({'request': request}, page=None)
        self.assertEqual(out, 'q=a')
        self.assertIn('page=2', url_replace({'request': request}, page=2))

    def test_url_replace_output_is_escaped_in_template(self):
        request = RequestFactory().get('/x/?q=a')
        html = Template('{% load expense_extras %}{% url_replace page=2 %}').render(Context({'request': request}))
        self.assertIn('q=a&amp;page=2', html)

    def test_category_icon_falls_back_when_icon_blank(self):
        from django.core.cache import cache

        from expenses.models import Category
        cache.clear()
        user = User.objects.create_user('ci', 'ci@example.com', 'pw12345!x')
        Category.objects.filter(user=user, name='Food').delete()
        Category.objects.create(user=user, name='Food', icon='')
        request = RequestFactory().get('/')
        request.user = user
        self.assertEqual(category_icon({'request': request}, 'Food'), 'bi-tag')


class SalaryCycleDatesTest(TestCase):
    def cycle(self, salary_date, target):
        user = SimpleNamespace(profile=SimpleNamespace(salary_date=salary_date))
        return SalaryAnalysisService.get_salary_cycle_dates(user, target)

    def test_no_salary_date_is_calendar_month(self):
        self.assertEqual(self.cycle(None, date(2026, 2, 10)), (date(2026, 2, 1), date(2026, 2, 28)))
        self.assertEqual(self.cycle(0, date(2024, 2, 10)), (date(2024, 2, 1), date(2024, 2, 29)))

    def test_mid_month_salary(self):
        self.assertEqual(self.cycle(15, date(2026, 5, 25)), (date(2026, 5, 15), date(2026, 6, 14)))
        self.assertEqual(self.cycle(15, date(2026, 5, 3)), (date(2026, 4, 15), date(2026, 5, 14)))

    def test_year_boundaries(self):
        self.assertEqual(self.cycle(15, date(2026, 12, 20)), (date(2026, 12, 15), date(2027, 1, 14)))
        self.assertEqual(self.cycle(15, date(2027, 1, 2)), (date(2026, 12, 15), date(2027, 1, 14)))

    def test_salary_day_31_clamps_in_short_months(self):
        self.assertEqual(self.cycle(31, date(2026, 2, 28)), (date(2026, 2, 28), date(2026, 3, 30)))
        self.assertEqual(self.cycle(31, date(2026, 3, 1)), (date(2026, 2, 28), date(2026, 3, 30)))
        self.assertEqual(self.cycle(31, date(2026, 3, 31)), (date(2026, 3, 31), date(2026, 4, 29)))

    def test_cycles_are_contiguous_and_cover_every_day(self):
        for salary_date in (1, 15, 28, 29, 30, 31):
            day = date(2023, 12, 1)
            prev_end = None
            seen_starts = set()
            while day < date(2025, 3, 1):
                start, end = self.cycle(salary_date, day)
                self.assertLessEqual(start, day, (salary_date, day))
                self.assertGreaterEqual(end, day, (salary_date, day))
                if start not in seen_starts:
                    seen_starts.add(start)
                    if prev_end:
                        self.assertEqual((start - prev_end).days, 1, (salary_date, day))
                    prev_end = end
                day = date.fromordinal(day.toordinal() + 1)

    def test_out_of_range_salary_date_falls_back(self):
        self.assertEqual(self.cycle(40, date(2026, 5, 10)), (date(2026, 5, 1), date(2026, 5, 31)))

    def test_missing_user_or_profile(self):
        self.assertEqual(SalaryAnalysisService.get_salary_cycle_dates(None, date(2026, 5, 10)),
                         (date(2026, 5, 1), date(2026, 5, 31)))
