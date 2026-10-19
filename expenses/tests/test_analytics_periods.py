"""Which days an Analytics period covers, what it is compared with, and how it is cut into bars."""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.analytics.periods import (
    COMPARE_BEFORE,
    COMPARE_LAST_MONTH,
    COMPARE_LAST_YEAR,
    buckets,
    build_period,
    granularity_for,
    sample,
)

D = datetime.date
TODAY = D(2026, 10, 19)


def state(key='this_year', start='', end=''):
    return {'time_period': key, 'start_date': start, 'end_date': end}


class GranularityTests(TestCase):
    def test_a_short_period_gets_a_bar_per_day_then_week_then_month_then_quarter(self):
        self.assertEqual(granularity_for(1), 'day')
        self.assertEqual(granularity_for(35), 'day')
        self.assertEqual(granularity_for(36), 'week')
        self.assertEqual(granularity_for(200), 'week')
        self.assertEqual(granularity_for(201), 'month')
        self.assertEqual(granularity_for(800), 'month')
        self.assertEqual(granularity_for(801), 'quarter')


class BucketTests(TestCase):
    def test_days(self):
        out = buckets(D(2026, 3, 1), D(2026, 3, 4), 'day')
        self.assertEqual([(b.start, b.end) for b in out], [(D(2026, 3, i), D(2026, 3, i)) for i in range(1, 5)])

    def test_weeks_start_at_the_period_start_and_the_last_one_is_clipped(self):
        out = buckets(D(2026, 3, 1), D(2026, 3, 17), 'week')
        self.assertEqual([(b.start, b.end) for b in out],
                         [(D(2026, 3, 1), D(2026, 3, 7)), (D(2026, 3, 8), D(2026, 3, 14)), (D(2026, 3, 15), D(2026, 3, 17))])

    def test_months_follow_the_calendar_and_the_ends_are_clipped_to_the_period(self):
        out = buckets(D(2026, 1, 20), D(2026, 3, 10), 'month')
        self.assertEqual([(b.start, b.end) for b in out],
                         [(D(2026, 1, 20), D(2026, 1, 31)), (D(2026, 2, 1), D(2026, 2, 28)), (D(2026, 3, 1), D(2026, 3, 10))])

    def test_quarters(self):
        out = buckets(D(2025, 11, 5), D(2026, 5, 2), 'quarter')
        self.assertEqual([(b.start, b.end) for b in out],
                         [(D(2025, 11, 5), D(2025, 12, 31)), (D(2026, 1, 1), D(2026, 3, 31)), (D(2026, 4, 1), D(2026, 5, 2))])

    def test_the_bars_cover_every_day_once(self):
        for granularity in ('day', 'week', 'month', 'quarter'):
            out = buckets(D(2025, 2, 11), D(2026, 4, 3), granularity)
            self.assertEqual(sum(b.days for b in out), (D(2026, 4, 3) - D(2025, 2, 11)).days + 1, granularity)
            for earlier, later in zip(out, out[1:], strict=False):
                self.assertEqual(earlier.end + datetime.timedelta(days=1), later.start)


class SampleTests(TestCase):
    def test_short_lists_are_untouched(self):
        self.assertEqual(sample([1, 2, 3], 5), [1, 2, 3])

    def test_a_long_list_keeps_its_first_and_last_and_stays_within_the_limit(self):
        picked = sample(list(range(100)), 12)
        self.assertLessEqual(len(picked), 12)
        self.assertEqual((picked[0], picked[-1]), (0, 99))
        self.assertEqual(picked, sorted(picked))


class PeriodTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('per', password='pw-per-12345')

    def period(self, key='this_year', start='', end='', today=TODAY, earliest=None):
        return build_period(self.user, state(key, start, end), today, earliest=earliest)

    def test_this_year_runs_to_today_and_is_compared_with_the_same_dates_last_year(self):
        p = self.period()
        self.assertEqual((p.start, p.end), (D(2026, 1, 1), TODAY))
        self.assertEqual(p.compare, (D(2025, 1, 1), D(2025, 10, 19)))
        self.assertEqual(p.compare_kind, COMPARE_LAST_YEAR)
        self.assertEqual(p.granularity, 'month')

    def test_a_leap_day_compares_with_the_28th(self):
        p = self.period(today=D(2028, 2, 29))
        self.assertEqual(p.compare[1], D(2027, 2, 28))

    def test_this_month_is_compared_with_the_same_number_of_days_of_last_month(self):
        p = self.period('this_month')
        self.assertEqual((p.start, p.end), (D(2026, 10, 1), TODAY))
        self.assertEqual(p.compare, (D(2026, 9, 1), D(2026, 9, 19)))
        self.assertEqual(p.compare_kind, COMPARE_LAST_MONTH)
        self.assertEqual(p.full_days, 31)          # the budget is spread over the whole month, not just up to today

    def test_this_month_on_a_salary_cycle_is_the_cycle(self):
        profile = self.user.profile
        profile.salary_date = 25
        profile.save()
        p = self.period('this_month')
        self.assertEqual(p.start.day, 25)
        self.assertEqual(p.compare[0].day, 25)
        self.assertEqual(p.compare_kind, COMPARE_LAST_MONTH)

    def test_last_month_is_compared_with_the_period_just_before_it(self):
        p = self.period('last_month')
        self.assertEqual((p.start, p.end), (D(2026, 9, 1), D(2026, 9, 30)))
        self.assertEqual(p.compare, (D(2026, 8, 2), D(2026, 8, 31)))
        self.assertEqual(p.compare_kind, COMPARE_BEFORE)

    def test_a_rolling_period_is_compared_with_the_stretch_of_the_same_length_before_it(self):
        p = self.period('last_3_months')
        self.assertEqual((p.start, p.end), (D(2026, 7, 21), TODAY))
        length = (p.end - p.start).days + 1
        self.assertEqual(p.compare[1], p.start - datetime.timedelta(days=1))
        self.assertEqual((p.compare[1] - p.compare[0]).days + 1, length)

    def test_all_time_has_no_comparison_and_starts_at_the_first_transaction(self):
        p = self.period('all', earliest=D(2025, 3, 3))
        self.assertEqual((p.start, p.end), (D(2025, 3, 3), TODAY))
        self.assertIsNone(p.compare)
        self.assertIsNone(p.compare_kind)

    def test_a_custom_range_is_clipped_to_today(self):
        p = self.period('custom', '2026-10-01', '2026-12-31')
        self.assertEqual(p.end, TODAY)

    def test_a_backwards_custom_range_is_put_the_right_way_round(self):
        p = self.period('custom', '2026-05-10', '2026-05-01')
        self.assertEqual((p.start, p.end), (D(2026, 5, 1), D(2026, 5, 10)))

    def test_a_custom_range_with_only_an_end_starts_at_the_first_transaction(self):
        p = self.period('custom', '', '2026-05-10', earliest=D(2026, 1, 2))
        self.assertEqual((p.start, p.end), (D(2026, 1, 2), D(2026, 5, 10)))

    def test_the_comparison_is_dropped_when_it_lies_before_any_data(self):
        p = self.period(earliest=D(2026, 3, 1))
        self.assertIsNone(p.compare)

    def test_one_day_is_a_bar_per_day(self):
        p = self.period('custom', '2026-10-05', '2026-10-05')
        self.assertEqual((p.days, p.granularity), (1, 'day'))
