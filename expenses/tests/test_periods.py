from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from expenses.models import Expense, UserProfile
from expenses.periods import resolve_period, ResolvedPeriod


class PeriodResolverTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='perioduser', password='password123')
        self.profile = self.user.profile

    def test_resolver_salary_date_1_default(self):
        """salary_date=1 must resolve this_month and last_month to calendar months."""
        self.profile.salary_date = 1
        self.profile.save()

        today = date(2026, 9, 10)
        p = resolve_period(user=self.user, time_period='this_month', today=today)
        self.assertEqual(p.start, date(2026, 9, 1))
        self.assertEqual(p.end, date(2026, 9, 30))
        self.assertEqual(p.key, 'this_month')
        self.assertFalse(p.is_cycle)

        p_last = resolve_period(user=self.user, time_period='last_month', today=today)
        self.assertEqual(p_last.start, date(2026, 8, 1))
        self.assertEqual(p_last.end, date(2026, 8, 31))
        self.assertEqual(p_last.key, 'last_month')
        self.assertFalse(p_last.is_cycle)

    def test_resolver_salary_date_15_before_and_after(self):
        """salary_date=15 resolves to cycle containing today."""
        self.profile.salary_date = 15
        self.profile.save()

        # Before salary day (10 Sep) -> cycle is 15 Aug to 14 Sep
        today_before = date(2026, 9, 10)
        p_before = resolve_period(user=self.user, time_period='this_month', today=today_before)
        self.assertEqual(p_before.start, date(2026, 8, 15))
        self.assertEqual(p_before.end, date(2026, 9, 14))
        self.assertTrue(p_before.is_cycle)

        # Preceding cycle for last_month
        p_last_before = resolve_period(user=self.user, time_period='last_month', today=today_before)
        self.assertEqual(p_last_before.start, date(2026, 7, 15))
        self.assertEqual(p_last_before.end, date(2026, 8, 14))
        self.assertTrue(p_last_before.is_cycle)

        # After salary day (20 Sep) -> cycle is 15 Sep to 14 Oct
        today_after = date(2026, 9, 20)
        p_after = resolve_period(user=self.user, time_period='this_month', today=today_after)
        self.assertEqual(p_after.start, date(2026, 9, 15))
        self.assertEqual(p_after.end, date(2026, 10, 14))
        self.assertTrue(p_after.is_cycle)

        p_last_after = resolve_period(user=self.user, time_period='last_month', today=today_after)
        self.assertEqual(p_last_after.start, date(2026, 8, 15))
        self.assertEqual(p_last_after.end, date(2026, 9, 14))
        self.assertTrue(p_last_after.is_cycle)

    def test_resolver_salary_date_31_short_months(self):
        """salary_date=31 on short months (Sep 30, Feb 28)."""
        self.profile.salary_date = 31
        self.profile.save()

        # 30 Sep 2026 (September has 30 days, effective salary day is 30)
        today_sep30 = date(2026, 9, 30)
        p_sep30 = resolve_period(user=self.user, time_period='this_month', today=today_sep30)
        self.assertEqual(p_sep30.start, date(2026, 9, 30))
        self.assertEqual(p_sep30.end, date(2026, 10, 30))
        self.assertTrue(p_sep30.is_cycle)

        # 28 Feb 2026 (Non-leap, Feb has 28 days, effective salary day is 28)
        today_feb28 = date(2026, 2, 28)
        p_feb28 = resolve_period(user=self.user, time_period='this_month', today=today_feb28)
        self.assertEqual(p_feb28.start, date(2026, 2, 28))
        self.assertEqual(p_feb28.end, date(2026, 3, 30))
        self.assertTrue(p_feb28.is_cycle)

    def test_resolver_rolling_and_calendar_presets(self):
        """Test last_3_months, this_year, all, and custom."""
        today = date(2026, 9, 10)
        p_3m = resolve_period(user=self.user, time_period='last_3_months', today=today)
        self.assertEqual(p_3m.start, date(2026, 6, 12)) # today - 90d
        self.assertEqual(p_3m.end, today)
        self.assertFalse(p_3m.is_cycle)

        p_year = resolve_period(user=self.user, time_period='this_year', today=today)
        self.assertEqual(p_year.start, date(2026, 1, 1))
        self.assertEqual(p_year.end, date(2026, 12, 31))

        # 'all' ignores stale start_date/end_date
        p_all = resolve_period(user=self.user, time_period='all', start_date='2020-01-01', end_date='2021-01-01', today=today)
        self.assertIsNone(p_all.start)
        self.assertIsNone(p_all.end)
        self.assertEqual(p_all.key, 'all')

        # 'custom' parses safely; unparseable strings become None
        p_custom_valid = resolve_period(user=self.user, time_period='custom', start_date='2026-05-01', end_date='2026-05-15', today=today)
        self.assertEqual(p_custom_valid.start, date(2026, 5, 1))
        self.assertEqual(p_custom_valid.end, date(2026, 5, 15))

        p_custom_invalid = resolve_period(user=self.user, time_period='custom', start_date='garbage-date', end_date='2026-99-99', today=today)
        self.assertIsNone(p_custom_invalid.start)
        self.assertIsNone(p_custom_invalid.end)

    def test_resolver_unrecognized_fallback(self):
        """Unrecognized time_period falls back to this_month."""
        today = date(2026, 9, 10)
        p = resolve_period(user=self.user, time_period='unknown_preset', today=today)
        self.assertEqual(p.key, 'this_month')
        self.assertEqual(p.start, date(2026, 9, 1))
        self.assertEqual(p.end, date(2026, 9, 30))


class CrossPagePeriodAgreementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cycleuser', password='password123')
        self.profile = self.user.profile
        self.profile.salary_date = 15
        self.profile.has_seen_tutorial = True
        self.profile.consent_granted = True
        self.profile.save()
        self.client.force_login(self.user)

    def test_cross_page_agreement_salary_date_15(self):
        """
        User with salary_date=15, expenses on 5 Sep and 20 Sep.
        With today=10 Sep 2026, 'this_month' cycle is 15 Aug to 14 Sep.
        Dashboard and Expenses page must report the exact same total (5 Sep expense only = 100).
        """
        # 5 Sep 2026 is inside current cycle (15 Aug – 14 Sep)
        Expense.objects.create(
            user=self.user,
            amount=Decimal('100.00'),
            base_amount=Decimal('100.00'),
            date=date(2026, 9, 5),
            category='Food',
            description='Lunch inside cycle'
        )
        # 20 Sep 2026 is in next cycle (15 Sep – 14 Oct)
        Expense.objects.create(
            user=self.user,
            amount=Decimal('200.00'),
            base_amount=Decimal('200.00'),
            date=date(2026, 9, 20),
            category='Transport',
            description='Taxi next cycle'
        )

        mock_today = date(2026, 9, 10)
        with patch('django.utils.timezone.localdate', return_value=mock_today):
            with patch('django.utils.timezone.now', return_value=timezone.datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)):
                # Dashboard view
                dash_resp = self.client.get(reverse('home'), {'time_period': 'this_month'})
                self.assertEqual(dash_resp.status_code, 200)
                dash_total = dash_resp.context['total_expenses']

                # Expenses list view
                exp_resp = self.client.get(reverse('expense-list'), {'time_period': 'this_month'})
                self.assertEqual(exp_resp.status_code, 200)
                exp_items = exp_resp.context['expenses']
                exp_total = sum(e.base_amount for e in exp_items)

                self.assertEqual(dash_total, Decimal('100.00'))
                self.assertEqual(exp_total, Decimal('100.00'))
                self.assertEqual(dash_total, exp_total)

    def test_filter_robustness_garbage_and_all_time(self):
        """?time_period=foo&start_date=garbage and ?time_period=all&start_date=2020-01-01 both return 200."""
        Expense.objects.create(
            user=self.user,
            amount=Decimal('50.00'),
            base_amount=Decimal('50.00'),
            date=date(2025, 1, 1),
            category='Old',
            description='Old record'
        )
        Expense.objects.create(
            user=self.user,
            amount=Decimal('75.00'),
            base_amount=Decimal('75.00'),
            date=date(2026, 9, 10),
            category='New',
            description='New record'
        )

        # 1. Garbage param returns 200 without crashing
        resp_garbage = self.client.get(reverse('expense-list'), {'time_period': 'foo', 'start_date': 'garbage'})
        self.assertEqual(resp_garbage.status_code, 200)

        # 2. time_period=all with stale start_date returns all rows
        resp_all = self.client.get(reverse('expense-list'), {'time_period': 'all', 'start_date': '2020-01-01'})
        self.assertEqual(resp_all.status_code, 200)
        self.assertEqual(len(resp_all.context['expenses']), 2)
