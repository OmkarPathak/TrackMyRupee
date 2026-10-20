"""Budgets follow the salary cycle: which days a budget month covers, and that the Budget page, the alerts,
the Dashboard and the writes all agree on it."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.budget_cycle import (
    budget_month_of,
    budget_range,
    current_budget_month,
    current_groups,
    key_for_range,
    next_month,
    previous_month,
    salary_day_of,
)
from expenses.budget_page import build_budget_page
from expenses.budget_writes import BudgetError, set_limit
from expenses.budgets import (
    bulk_current_spend,
    bulk_monthly_spend,
    monthly_spend_by_category,
    monthly_spend_by_category_months,
)
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    Notification,
    UserProfile,
)
from expenses.services import SalaryAnalysisService

D = Decimal
d = datetime.date


class CycleMathTests(TestCase):
    def test_calendar_months_when_there_is_no_salary_day(self):
        for day in (0, 1):
            self.assertEqual(budget_range(day, d(2026, 2, 17)), (d(2026, 2, 1), d(2026, 2, 28)))
        self.assertEqual(budget_month_of(1, d(2026, 2, 17)), d(2026, 2, 1))

    def test_an_early_salary_day_names_the_cycle_by_the_month_it_starts_in(self):
        self.assertEqual(budget_range(5, d(2026, 10, 1)), (d(2026, 10, 5), d(2026, 11, 4)))
        self.assertEqual(budget_month_of(5, d(2026, 11, 3)), d(2026, 10, 1))
        self.assertEqual(budget_month_of(5, d(2026, 11, 5)), d(2026, 11, 1))

    def test_a_late_salary_day_names_the_cycle_by_the_month_it_ends_in(self):
        self.assertEqual(budget_range(25, d(2026, 10, 1)), (d(2026, 9, 25), d(2026, 10, 24)))
        self.assertEqual(budget_month_of(25, d(2026, 10, 19)), d(2026, 10, 1))
        self.assertEqual(budget_month_of(25, d(2026, 10, 25)), d(2026, 11, 1))

    def test_a_short_month_uses_its_last_day_as_the_salary_day(self):
        self.assertEqual(budget_range(31, d(2026, 3, 1)), (d(2026, 2, 28), d(2026, 3, 30)))
        self.assertEqual(budget_range(31, d(2026, 2, 1)), (d(2026, 1, 31), d(2026, 2, 27)))

    def test_it_agrees_with_the_salary_cycle_the_dashboard_uses_for_every_day_of_two_years(self):
        user = User.objects.create_user('cycle-math', password='x')
        for salary_day in range(1, 32):
            UserProfile.objects.update_or_create(user=user, defaults={'salary_date': salary_day})
            user.refresh_from_db()
            day = d(2025, 1, 1)
            while day <= d(2026, 12, 31):
                expected = SalaryAnalysisService.get_salary_cycle_dates(user, day)
                key = budget_month_of(salary_day, day)
                self.assertEqual(budget_range(salary_day, key), expected, (salary_day, day))
                self.assertEqual(salary_day_of(user), salary_day)
                day += datetime.timedelta(days=3)

    def test_every_month_has_exactly_one_cycle_and_the_cycles_do_not_overlap_or_leave_gaps(self):
        for salary_day in (1, 2, 15, 16, 25, 28, 29, 30, 31):
            key = d(2025, 1, 1)
            previous_end = None
            for _ in range(30):
                start, end = budget_range(salary_day, key)
                if previous_end is not None:
                    self.assertEqual(start, previous_end + datetime.timedelta(days=1), (salary_day, key))
                previous_end = end
                key = next_month(key)

    def test_key_for_range_only_matches_a_whole_budget_month(self):
        self.assertEqual(key_for_range(25, d(2026, 9, 25), d(2026, 10, 24)), d(2026, 10, 1))
        self.assertIsNone(key_for_range(25, d(2026, 9, 25), d(2026, 10, 23)))
        self.assertEqual(key_for_range(1, d(2026, 10, 1), d(2026, 10, 31)), d(2026, 10, 1))

    def test_previous_and_next_cross_the_year(self):
        self.assertEqual(previous_month(d(2026, 1, 1)), d(2025, 12, 1))
        self.assertEqual(next_month(d(2026, 12, 1)), d(2027, 1, 1))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cyc-user', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.salary_date = 25
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        Category.objects.filter(user=self.user).delete()
        Account.objects.filter(user=self.user).delete()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('100000'), currency='₹')

    def cat(self, name, limit=None):
        return Category.objects.create(user=self.user, name=name, limit=None if limit is None else D(str(limit)))

    def spend(self, category, amount, when, user=None):
        return Expense.objects.create(user=user or self.user, account=self.bank, amount=D(str(amount)), base_amount=D(str(amount)),
                                      date=when, category=category, description='x', currency='₹', exchange_rate=1)


class SpendFollowsTheCycleTests(Base):
    def setUp(self):
        super().setUp()
        self.cat('Food', 1000)
        self.spend('Food', 100, d(2026, 9, 24))      # the cycle before
        self.spend('Food', 200, d(2026, 9, 25))      # first day of October's budget
        self.spend('Food', 300, d(2026, 10, 24))     # its last day
        self.spend('Food', 400, d(2026, 10, 25))     # November's

    def test_spend_counts_only_the_cycle_days(self):
        spend, total = monthly_spend_by_category(self.user, 2026, 10)
        self.assertEqual(spend, {'food': D('500')})
        self.assertEqual(total, D('500'))

    def test_several_months_are_cut_the_same_way(self):
        out = monthly_spend_by_category_months(self.user, [d(2026, 9, 1), d(2026, 10, 1), d(2026, 11, 1)])
        self.assertEqual(out[d(2026, 9, 1)].get('food', D('0')), D('100'))
        self.assertEqual(out[d(2026, 10, 1)]['food'], D('500'))
        self.assertEqual(out[d(2026, 11, 1)]['food'], D('400'))

    def test_a_capital_event_counts_in_the_cycle_it_falls_in(self):
        CapitalEvent.objects.create(user=self.user, account=self.bank, amount=D('900'), base_amount=D('900'), date=d(2026, 9, 30),
                                    subtype='large_purchase', category='Food', currency='₹', exclude_from_budget=False,
                                    exclude_from_averages=True)
        self.assertEqual(monthly_spend_by_category(self.user, 2026, 10)[0]['food'], D('1400'))
        self.assertEqual(monthly_spend_by_category(self.user, 2026, 9)[0]['food'], D('100'))

    def test_bulk_uses_each_users_own_cycle(self):
        calendar_user = User.objects.create_user('cal-user', password='pw')
        self.spend('Food', 50, d(2026, 10, 30), user=calendar_user)
        self.spend('Food', 60, d(2026, 10, 10), user=calendar_user)
        bulk = bulk_monthly_spend(2026, 10)
        self.assertEqual(bulk[(self.user.id, 'food')], D('500'))
        self.assertEqual(bulk[(calendar_user.id, 'food')], D('110'))

    def test_bulk_matches_the_per_user_function_for_every_user(self):
        bulk = bulk_monthly_spend(2026, 10)
        mine, _ = monthly_spend_by_category(self.user, 2026, 10)
        self.assertEqual({k[1]: v for k, v in bulk.items() if k[0] == self.user.id}, mine)

    def test_current_spend_is_for_the_cycle_each_user_is_in_today(self):
        calendar_user = User.objects.create_user('cal-user-2', password='pw')
        self.spend('Food', 70, d(2026, 10, 27), user=calendar_user)
        self.spend('Food', 80, d(2026, 11, 2), user=calendar_user)
        groups = {tuple(g['user_ids'] or ()): g for g in current_groups(d(2026, 10, 27))}
        self.assertEqual(groups[(self.user.id,)]['key'], d(2026, 11, 1))
        current = bulk_current_spend(d(2026, 10, 27))
        self.assertEqual(current[(self.user.id, 'food')], D('400'))          # 25 Oct onward: November's budget so far
        self.assertEqual(current[(calendar_user.id, 'food')], D('70'))        # October, the calendar month


class PageTests(Base):
    def test_the_payload_describes_the_cycle(self):
        self.cat('Food', 3100)
        self.spend('Food', 1000, d(2026, 10, 5))
        page = build_budget_page(self.user, d(2026, 10, 1), d(2026, 10, 19))
        self.assertEqual((page['start'], page['end']), ('2026-09-25', '2026-10-24'))
        self.assertTrue(page['is_cycle'])
        self.assertEqual(page['days_in_month'], 30)
        self.assertEqual(page['phase'], 'current')
        self.assertEqual(page['days_left'], 6)                  # 19..24 Oct
        self.assertEqual(page['spent'], 1000.0)
        # day 25 of a 30-day cycle: 3100 * 25 / 30
        self.assertEqual(page['expected_pace'], 2583.33)

    def test_a_cycle_that_ended_is_past_and_one_that_has_not_begun_is_future(self):
        self.cat('Food', 100)
        self.assertEqual(build_budget_page(self.user, d(2026, 9, 1), d(2026, 10, 19))['phase'], 'past')
        self.assertEqual(build_budget_page(self.user, d(2026, 11, 1), d(2026, 10, 19))['phase'], 'future')
        self.assertEqual(build_budget_page(self.user, d(2026, 11, 1), d(2026, 10, 25))['phase'], 'current')

    def test_without_a_salary_day_the_page_is_the_calendar_month(self):
        UserProfile.objects.filter(user=self.user).update(salary_date=1)
        self.user.refresh_from_db()
        page = build_budget_page(self.user, d(2026, 10, 1), d(2026, 10, 19))
        self.assertEqual((page['start'], page['end'], page['is_cycle']), ('2026-10-01', '2026-10-31', False))
        self.assertEqual(page['days_left'], 13)

    def test_the_page_names_the_cycle_and_defaults_to_the_one_you_are_in(self):
        self.cat('Food', 100)
        response = self.client.get(reverse('budget'))
        today = timezone.localdate()
        key = current_budget_month(self.user, today)
        self.assertEqual(response.context['month_start'], key)
        self.assertEqual(response.context['cycle_start'], budget_range(25, key)[0])
        self.assertContains(response, 'Your salary cycle')

    def test_the_api_defaults_to_the_current_budget_month(self):
        self.cat('Food', 100)
        key = current_budget_month(self.user, timezone.localdate())
        data = self.client.get(reverse('budgets-api')).json()
        self.assertEqual(data['month'], key.strftime('%Y-%m'))
        self.assertEqual(data['start'], budget_range(25, key)[0].isoformat())


class WriteTests(Base):
    def test_every_month_cannot_be_set_for_a_cycle_that_already_ended(self):
        food = self.cat('Food')
        today = timezone.localdate()
        key = current_budget_month(self.user, today)
        set_limit(self.user, food, key, 500, 'every_month')
        with self.assertRaises(BudgetError):
            set_limit(self.user, food, previous_month(key), 500, 'every_month')

    def test_this_month_can_still_be_set_for_a_past_cycle(self):
        food = self.cat('Food')
        key = current_budget_month(self.user, timezone.localdate())
        self.assertEqual(set_limit(self.user, food, previous_month(key), 700, 'this_month'), D('700'))


class AlertTests(Base):
    def run_alerts(self):
        call_command('send_notifications')
        return list(Notification.objects.filter(user=self.user, slug__startswith='budget-'))

    def test_alerts_count_the_cycle_you_are_in_and_name_it_by_its_budget_month(self):
        today = timezone.localdate()
        key = current_budget_month(self.user, today)
        start, _ = budget_range(25, key)
        self.cat('Food', 1000)
        self.spend('Food', 900, start)                       # inside the cycle, possibly in the month before
        self.spend('Food', 5000, start - datetime.timedelta(days=1))   # the cycle before: must not count
        alerts = self.run_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].title, 'Budget Alert: Food')
        self.assertTrue(alerts[0].slug.endswith(f'-{key.year}-{key.month}'))

    def test_the_alert_is_not_sent_twice_in_one_cycle(self):
        today = timezone.localdate()
        start, _ = budget_range(25, current_budget_month(self.user, today))
        self.cat('Food', 1000)
        self.spend('Food', 900, start)
        self.run_alerts()
        self.run_alerts()
        self.assertEqual(len(Notification.objects.filter(user=self.user, slug__startswith='budget-')), 1)


class DashboardTests(Base):
    def test_this_month_on_the_dashboard_uses_the_limit_of_the_budget_month_you_are_in(self):
        today = timezone.localdate()
        # a salary day that puts the cycle in a different month's budget than today's calendar month
        if today.day >= 16:
            salary_day = 16          # the cycle that started this month ends next month: next month's budget
        elif today.day < 15:
            salary_day = 15          # the cycle that started last month: last month's budget
        else:
            self.skipTest('on the 15th every salary day names the calendar month')
        UserProfile.objects.filter(user=self.user).update(salary_date=salary_day)
        self.user.refresh_from_db()
        key = current_budget_month(self.user, today)
        self.assertNotEqual(key, today.replace(day=1))
        food = self.cat('Food')
        set_limit(self.user, food, key, 3000, 'this_month')
        set_limit(self.user, food, today.replace(day=1), 999, 'this_month')
        self.spend('Food', 100, today)
        context = self.client.get(reverse('home'), {'time_period': 'this_month'}).context
        row = [r for r in context['category_limits'] if r['name'] == 'Food'][0]
        self.assertEqual(row['limit'], 3000)
