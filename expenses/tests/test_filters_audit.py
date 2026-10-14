"""Audit of the shared filter engine: every chip, the search box, the period and the sort give the rows
they promise, malformed input never hides or breaks the page, and the numbers agree with the rows."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.filters.definitions import AMOUNT_RANGE_OPTIONS, EXPENSE_FILTERS
from expenses.filters.engine import apply_filter_config
from expenses.models import Account, Expense, FXRate, Income

D = Decimal
TODAY = timezone.localdate()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='flt', password='pw')
        self.user.profile.tier = 'PRO'
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', balance=D('100000'), currency='₹')
        self.card = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('0'), currency='₹')
        self.rf = RequestFactory()

    def expense(self, amount, description='x', when=None, **kw):
        values = dict(user=self.user, account=self.cash, amount=D(str(amount)), date=when or TODAY, category='Food',
                      description=description, currency='₹', payment_method='Cash')
        values.update(kw)
        return Expense.objects.create(**values)

    def run_filters(self, config=EXPENSE_FILTERS, **params):
        request = self.rf.get('/x/', params)
        request.user = self.user
        qs = Expense.objects.filter(user=self.user) if config is EXPENSE_FILTERS else Income.objects.filter(user=self.user)
        result, state = apply_filter_config(qs, request, config)
        return list(result), state

    def descriptions(self, **params):
        return [e.description for e in self.run_filters(**params)[0]]


class AmountBucketTests(Base):
    def test_every_amount_falls_in_exactly_one_bucket(self):
        for amount in ('0.01', '499.99', '500', '1999.99', '2000', '2000.01', '9999.99', '10000', '10000.01'):
            self.expense(amount, description=f'a{amount}')
        seen = {}
        for bucket in AMOUNT_RANGE_OPTIONS:
            for d in self.descriptions(amount_range=bucket, time_period='all'):
                seen.setdefault(d, []).append(bucket)
        self.assertEqual(len(seen), 9, 'some amount is in no bucket')
        self.assertTrue(all(len(v) == 1 for v in seen.values()), f'in two buckets: {seen}')

    def test_the_boundaries_land_where_the_labels_say(self):
        for amount in ('499.99', '500', '2000', '10000', '10000.01'):
            self.expense(amount, description=f'a{amount}')
        under = self.descriptions(amount_range='Under 500', time_period='all')
        self.assertEqual(under, ['a499.99'])
        self.assertEqual(sorted(self.descriptions(amount_range='500 to 2,000', time_period='all')), ['a500'])
        self.assertEqual(sorted(self.descriptions(amount_range='2,000 to 10,000', time_period='all')), ['a10000', 'a2000'])
        self.assertEqual(self.descriptions(amount_range='Over 10,000', time_period='all'), ['a10000.01'])

    def test_two_buckets_together_are_either_or(self):
        self.expense('100', 'small')
        self.expense('50000', 'big')
        self.expense('3000', 'mid')
        got = sorted(self.descriptions(amount_range=['Under 500', 'Over 10,000'], time_period='all'))
        self.assertEqual(got, ['big', 'small'])

    def test_the_old_bookmarked_labels_with_a_rupee_sign_still_work(self):
        self.expense('1000', 'mid')
        self.assertEqual(self.descriptions(amount_range='₹500 to ₹2,000', time_period='all'), ['mid'])

    def test_buckets_use_the_value_in_the_users_currency(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=TODAY,
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        usd = Account.objects.create(user=self.user, name='USD', balance=D('1000'), currency='$')
        self.expense('10', 'ten dollars', account=usd, currency='$')   # ₹800
        self.assertEqual(self.descriptions(amount_range='500 to 2,000', time_period='all'), ['ten dollars'])
        self.assertEqual(self.descriptions(amount_range='Under 500', time_period='all'), [])


class SearchTests(Base):
    def test_search_is_case_insensitive_and_matches_part_of_a_word(self):
        self.expense(10, 'Swiggy Dinner')
        self.assertEqual(self.descriptions(search='swIGgy'), ['Swiggy Dinner'])
        self.assertEqual(self.descriptions(search='inner'), ['Swiggy Dinner'])

    def test_percent_underscore_and_quotes_are_just_text(self):
        self.expense(10, '100% juice')
        self.expense(10, 'a_b')
        self.expense(10, "Joe's cafe")
        self.expense(10, 'plain')
        self.assertEqual(self.descriptions(search='100%'), ['100% juice'])
        self.assertEqual(self.descriptions(search='a_b'), ['a_b'])
        self.assertEqual(self.descriptions(search="Joe's"), ["Joe's cafe"])
        self.assertEqual(self.descriptions(search='%'), ['100% juice'])
        self.assertEqual(self.descriptions(search='_'), ['a_b'])

    def test_the_q_alias_and_surrounding_spaces(self):
        self.expense(10, 'Coffee')
        self.assertEqual(self.descriptions(q='  coffee  '), ['Coffee'])

    def test_a_blank_search_filters_nothing(self):
        self.expense(10, 'A')
        self.assertEqual(self.descriptions(search='   '), ['A'])

    def test_search_never_returns_another_users_rows(self):
        stranger = User.objects.create_user(username='s', password='pw')
        theirs = Account.objects.create(user=stranger, name='T', balance=D('1'), currency='₹')
        Expense.objects.create(user=stranger, account=theirs, amount=D('5'), date=TODAY, category='Food',
                               description='secret coffee', currency='₹')
        self.expense(10, 'my coffee')
        self.assertEqual(self.descriptions(search='coffee'), ['my coffee'])

    def test_hindi_text_is_searchable(self):
        self.expense(10, 'चाय और समोसा')
        self.assertEqual(self.descriptions(search='चाय'), ['चाय और समोसा'])


class PeriodTests(Base):
    def test_a_custom_range_includes_both_end_days(self):
        for offset in (-11, -10, -5, 0, 1):
            self.expense(10, f'd{offset}', when=TODAY + timedelta(days=offset))
        start, end = TODAY - timedelta(days=10), TODAY
        got = sorted(self.descriptions(time_period='custom', start_date=start.isoformat(), end_date=end.isoformat()))
        self.assertEqual(got, ['d-10', 'd-5', 'd0'])

    def test_dates_alone_mean_a_custom_range(self):
        self.expense(10, 'in', when=TODAY - timedelta(days=2))
        self.expense(10, 'out', when=TODAY - timedelta(days=40))
        got = self.descriptions(start_date=(TODAY - timedelta(days=5)).isoformat(), end_date=TODAY.isoformat())
        self.assertEqual(got, ['in'])

    def test_a_reversed_range_shows_nothing_rather_than_everything(self):
        self.expense(10, 'a')
        got = self.descriptions(time_period='custom', start_date=TODAY.isoformat(),
                                end_date=(TODAY - timedelta(days=9)).isoformat())
        self.assertEqual(got, [])

    def test_a_garbage_date_is_ignored_not_a_crash(self):
        self.expense(10, 'a')
        for bad in ('abc', '2026-13-45', '31/12/2026', "'; drop table--"):
            self.descriptions(time_period='custom', start_date=bad, end_date=bad)

    def test_all_means_everything_and_ignores_stale_dates(self):
        self.expense(10, 'old', when=TODAY - timedelta(days=900))
        self.assertEqual(self.descriptions(time_period='all', start_date=TODAY.isoformat()), ['old'])

    def test_this_month_is_the_calendar_month_by_default(self):
        self.expense(10, 'now')
        self.expense(10, 'old', when=TODAY.replace(day=1) - timedelta(days=1))
        self.assertEqual(self.descriptions(), ['now'])

    def test_last_month_is_the_whole_previous_month(self):
        first = TODAY.replace(day=1)
        last_end = first - timedelta(days=1)
        self.expense(10, 'first', when=last_end.replace(day=1))
        self.expense(10, 'last', when=last_end)
        self.expense(10, 'this', when=first)
        self.assertEqual(sorted(self.descriptions(time_period='last_month')), ['first', 'last'])

    def test_an_unknown_period_falls_back_to_this_month(self):
        self.expense(10, 'now')
        self.expense(10, 'old', when=TODAY - timedelta(days=100))
        self.assertEqual(self.descriptions(time_period='nonsense'), ['now'])


class ChipTests(Base):
    def test_values_within_one_chip_are_or_and_different_chips_are_and(self):
        self.expense(10, 'food-cash', category='Food', payment_method='Cash')
        self.expense(10, 'food-upi', category='Food', payment_method='UPI')
        self.expense(10, 'rent-cash', category='Rent', payment_method='Cash')
        self.expense(10, 'fun-card', category='Fun', payment_method='Credit Card')
        self.assertEqual(sorted(self.descriptions(category=['Food', 'Rent'])), ['food-cash', 'food-upi', 'rent-cash'])
        self.assertEqual(self.descriptions(category=['Food', 'Rent'], payment_method='Cash').__len__(), 2)
        self.assertEqual(sorted(self.descriptions(category='Food', payment_method='Cash')), ['food-cash'])

    def test_a_chip_for_another_users_account_shows_nothing_of_theirs(self):
        stranger = User.objects.create_user(username='s', password='pw')
        theirs = Account.objects.create(user=stranger, name='T', balance=D('1'), currency='₹')
        Expense.objects.create(user=stranger, account=theirs, amount=D('5'), date=TODAY, category='Food',
                               description='theirs', currency='₹')
        self.expense(10, 'mine')
        self.assertEqual(self.descriptions(account=str(theirs.id)), [])

    def test_the_no_account_chip_finds_old_rows_without_an_account(self):
        legacy = self.expense(10, 'legacy')
        Expense.objects.filter(pk=legacy.pk).update(account=None)
        self.expense(10, 'normal')
        self.assertEqual(self.descriptions(account='none'), ['legacy'])
        self.assertEqual(sorted(self.descriptions(account=['none', str(self.cash.id)])), ['legacy', 'normal'])

    def test_empty_chip_values_are_ignored(self):
        self.expense(10, 'a')
        self.assertEqual(self.descriptions(category=['', ' ']), ['a'])

    def test_a_chip_the_page_does_not_have_is_ignored(self):
        self.expense(10, 'a')
        self.assertEqual(self.descriptions(secret_field='x', user='1'), ['a'])

    def test_the_applied_state_reports_the_chips_that_were_used(self):
        self.expense(10, 'a')
        _rows, state = self.run_filters(category='Food', search='a')
        self.assertEqual(state['filters'], {'category': ['Food']})
        self.assertEqual(state['search'], 'a')


class SortTests(Base):
    def test_amount_sorts_use_the_value_in_the_users_currency_with_a_stable_tiebreak(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=TODAY,
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        usd = Account.objects.create(user=self.user, name='USD', balance=D('1000'), currency='$')
        self.expense('5', 'five dollars', account=usd, currency='$')   # ₹400
        self.expense('300', 'three hundred rupees')
        self.expense('900', 'nine hundred rupees')
        self.assertEqual(self.descriptions(sort='amount_desc'),
                         ['nine hundred rupees', 'five dollars', 'three hundred rupees'])
        self.assertEqual(self.descriptions(sort='amount_asc'),
                         ['three hundred rupees', 'five dollars', 'nine hundred rupees'])

    def test_date_sorts_both_ways(self):
        self.expense(1, 'a', when=TODAY - timedelta(days=2))
        self.expense(1, 'b', when=TODAY - timedelta(days=1))
        self.assertEqual(self.descriptions(sort='date_desc'), ['b', 'a'])
        self.assertEqual(self.descriptions(sort='date_asc'), ['a', 'b'])

    def test_an_unknown_sort_is_harmless_and_cannot_reach_other_fields(self):
        self.expense(1, 'a')
        for sort in ('user__password', '-id; drop', '', 'amount'):
            self.descriptions(sort=sort)


class ListPageTests(Base):
    """The page must show what the engine found, and not fall over on bad input."""

    def test_the_expense_list_survives_garbage_parameters(self):
        self.expense(10, 'a')
        r = self.client.get(reverse('expense-list'), {
            'category': '<script>', 'account': 'abc', 'amount_range': 'nonsense', 'sort': '??',
            'time_period': 'x', 'start_date': 'y', 'search': '%' * 300})
        self.assertEqual(r.status_code, 200)

    def test_the_filtered_export_matches_the_filtered_list(self):
        self.expense(10, 'keep', category='Food')
        self.expense(10, 'drop', category='Rent')
        import csv
        import io
        r = self.client.get(reverse('export-expenses'), {'category': 'Food'})
        rows = list(csv.reader(io.StringIO(r.content.decode('utf-8-sig'))))
        self.assertEqual([row[1] for row in rows[1:]], ['keep'])
