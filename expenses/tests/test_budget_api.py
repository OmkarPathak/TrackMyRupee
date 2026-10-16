"""Budgets API (GET/PUT/POST api/budgets/...), the page payload and the month maths."""

import json
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses import budget_resolver as resolver
from expenses import budget_writes as writes
from expenses.budget_page import build_budget_page
from expenses.budgets import (
    days_left,
    expected_pace,
    month_phase,
    safe_to_spend_per_day,
)
from expenses.models import (
    BudgetLimitVersion,
    BudgetMonthOverride,
    Category,
    Expense,
    UserProfile,
)
from expenses.tests.budget_helpers import backdate_limits

D = Decimal
TODAY = timezone.localdate()
CUR = TODAY.replace(day=1)


def add_months(d, n):
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


PREV, NEXT, NEXT2 = add_months(CUR, -1), add_months(CUR, 1), add_months(CUR, 2)
ym = lambda d: d.strftime('%Y-%m')  # noqa: E731


class Base(TestCase):
    tier = 'PRO'

    def make_user(self, name, tier='PRO'):
        user = User.objects.create_user(name, f'{name}@example.com', 'pw')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier, profile.has_seen_tutorial, profile.consent_granted = tier, True, True
        profile.save()
        Category.objects.filter(user=user).delete()
        user.refresh_from_db()
        return user

    def setUp(self):
        cache.clear()
        self.user = self.make_user('api-user', self.tier)
        self.client.force_login(self.user)
        self.food = Category.objects.create(user=self.user, name='Food', limit=D('5000'))
        self.fuel = Category.objects.create(user=self.user, name='Fuel', limit=D('2000'))
        self.fun = Category.objects.create(user=self.user, name='Fun')
        backdate_limits(self.user, add_months(CUR, -3))
        self.spend('Food', 4500)
        self.spend('Fuel', 2500)

    def spend(self, cat, amount, when=None):
        return Expense.objects.create(user=self.user, date=when or TODAY, amount=D(amount), description='x',
                                      category=cat, currency='₹')

    def get(self, **params):
        return self.client.get(reverse('budgets-api'), params)

    def put(self, body, url='budgets-api-limit'):
        return self.client.put(reverse(url), json.dumps(body), content_type='application/json')

    def post(self, body, url='budgets-api-rebalance'):
        return self.client.post(reverse(url), json.dumps(body), content_type='application/json')

    def rows(self, resp):
        return {r['name']: r for r in resp.json()['categories']}


class MonthMathTests(TestCase):
    def test_phase(self):
        today = date(2026, 10, 16)
        self.assertEqual(month_phase(date(2026, 9, 1), today), 'past')
        self.assertEqual(month_phase(date(2026, 10, 1), today), 'current')
        self.assertEqual(month_phase(date(2026, 11, 1), today), 'future')

    def test_days_left_includes_today(self):
        today = date(2026, 10, 16)
        self.assertEqual(days_left(date(2026, 10, 1), today), 16)        # 16th..31st
        self.assertEqual(days_left(date(2026, 10, 1), date(2026, 10, 31)), 1)
        self.assertEqual(days_left(date(2026, 9, 1), today), 0)
        self.assertEqual(days_left(date(2026, 11, 1), today), 30)

    def test_expected_pace_is_total_times_day_over_days(self):
        self.assertEqual(expected_pace(73000, 15, 31), D('35322.58'))
        self.assertEqual(expected_pace(73000, 31, 31), D('73000.00'))
        self.assertEqual(expected_pace(0, 5, 31), D('0'))
        self.assertEqual(expected_pace(None, 5, 31), D('0'))

    def test_safe_to_spend(self):
        self.assertEqual(safe_to_spend_per_day(45702, 16), D('2856.38'))
        self.assertEqual(safe_to_spend_per_day(0, 10), D('0'))
        self.assertEqual(safe_to_spend_per_day(-50, 10), D('0'))
        self.assertEqual(safe_to_spend_per_day(100, 0), D('0'))


class PayloadTests(Base):
    def test_current_month_payload(self):
        resp = self.get(month=ym(CUR))
        self.assertEqual(resp.status_code, 200)
        p = resp.json()
        self.assertTrue(p['success'])
        self.assertEqual((p['month'], p['phase']), (ym(CUR), 'current'))
        self.assertEqual(p['total'], 7000.0)               # derived: 5000 + 2000
        self.assertEqual(p['spent'], 7000.0)
        self.assertEqual(p['remaining'], 0.0)
        self.assertEqual(p['over_by'], 0.0)
        self.assertEqual(p['counts'], {'over': 1, 'at_limit': 1, 'on_track': 0, 'no_limit': 1})
        self.assertEqual(p['safe_to_spend_per_day'], 0.0)
        self.assertEqual(p['state'], 'over_budget')        # remaining is 0
        self.assertEqual(p['days_left'], resolver_days_left())
        rows = self.rows(resp)
        self.assertEqual(rows['Fuel']['status'], 'over')
        self.assertEqual(rows['Fuel']['over_by'], 500.0)
        self.assertEqual(rows['Food']['status'], 'limit')
        self.assertEqual(rows['Food']['percent'], 90.0)
        self.assertEqual(rows['Fun']['status'], 'nolimit')
        self.assertIsNone(rows['Fun']['limit'])
        self.assertEqual([r['name'] for r in p['categories']], ['Fuel', 'Food', 'Fun'])   # urgent first
        for key in ('id', 'uuid', 'limit', 'spent', 'remaining', 'percent', 'status', 'limit_source',
                    'has_future_changes'):
            self.assertIn(key, rows['Food'])

    def test_default_month_is_this_month_in_the_users_timezone(self):
        self.assertEqual(self.get().json()['month'], ym(CUR))
        with mock.patch('expenses.views.budgets_api.timezone.localdate', return_value=date(2026, 12, 31)):
            self.assertEqual(self.get().json()['month'], '2026-12')

    def test_safe_to_spend_and_pace_when_under_budget(self):
        Expense.objects.filter(user=self.user).delete()
        self.spend('Food', 1000)
        p = self.get().json()
        left = resolver_days_left()
        self.assertEqual(p['remaining'], 6000.0)
        self.assertEqual(p['safe_to_spend_per_day'], float((D(6000) / left).quantize(D('0.01'))))
        self.assertEqual(p['state'], 'ok')
        expected = float((D(7000) * TODAY.day / p['days_in_month']).quantize(D('0.01')))
        self.assertEqual(p['expected_pace'], expected)
        self.assertEqual(p['pace_delta'], round(expected - 1000.0, 2))

    def test_past_month_has_a_summary_but_no_pace_or_safe_to_spend(self):
        self.spend('Food', 300, when=PREV.replace(day=5))
        p = self.get(month=ym(PREV)).json()
        self.assertEqual(p['phase'], 'past')
        self.assertEqual((p['total'], p['spent']), (7000.0, 300.0))
        for key in ('expected_pace', 'pace_delta', 'safe_to_spend_per_day', 'days_left'):
            self.assertIsNone(p[key])
        self.assertEqual(p['state'], 'ok')

    def test_future_month_shows_limits_only(self):
        p = self.get(month=ym(NEXT)).json()
        self.assertEqual(p['phase'], 'future')
        self.assertEqual(p['total'], 7000.0)
        for key in ('spent', 'remaining', 'expected_pace', 'safe_to_spend_per_day', 'unbudgeted_spent'):
            self.assertIsNone(p[key])
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['spent'], 0.0)

    def test_limit_source_and_future_changes(self):
        writes.set_limit(self.user, self.food, CUR, 9000, 'this_month')
        writes.set_limit(self.user, self.fuel, NEXT2, 2500, 'every_month')
        rows = self.rows(self.get(month=ym(CUR)))
        self.assertEqual((rows['Food']['limit'], rows['Food']['limit_source']), (9000.0, 'override'))
        self.assertFalse(rows['Food']['has_future_changes'])
        self.assertEqual(rows['Fuel']['limit_source'], 'default')
        self.assertTrue(rows['Fuel']['has_future_changes'])
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 5000.0)

    def test_deleted_category_is_hidden_but_resolves_for_past_months(self):
        self.fuel.delete()
        self.assertNotIn('Fuel', self.rows(self.get(month=ym(CUR))))
        self.assertEqual(resolver.effective_limits_by_name(self.user, PREV)['fuel'], D('2000'))

    def test_empty_states(self):
        Category.objects.filter(user=self.user).delete()
        p = self.get().json()
        self.assertEqual((p['categories'], p['total'], p['state']), ([], 0.0, None))
        other = self.make_user('no-limits')
        Category.objects.create(user=other, name='Food')
        self.client.force_login(other)
        p = self.get().json()
        self.assertEqual((p['total'], p['counts']['no_limit']), (0.0, 1))
        self.assertIsNone(p['safe_to_spend_per_day'])


class GetValidationTests(Base):
    def test_bad_month_formats(self):
        for bad in ('2026-13', '2026-1', 'abc', '2026-10-01', '1999-01', '2101-01'):
            resp = self.get(month=bad)
            self.assertEqual(resp.status_code, 400, bad)
            self.assertEqual(resp.json()['code'], 'invalid_request')

    def test_requires_login_and_get_only(self):
        self.client.logout()
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get().json()['code'], 'auth_required')
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('budgets-api')).status_code, 405)

    def test_other_users_categories_never_appear(self):
        other = self.make_user('other')
        Category.objects.create(user=other, name='Secret', limit=D('1'))
        self.assertNotIn('Secret', self.rows(self.get()))


class SetLimitTests(Base):
    def test_this_month_only_changes_this_month(self):
        resp = self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': 6000, 'scope': 'this_month'})
        self.assertEqual(resp.status_code, 200)
        rows = self.rows(resp)
        self.assertEqual((rows['Food']['limit'], rows['Food']['limit_source']), (6000.0, 'override'))
        self.assertEqual(resp.json()['total'], 8000.0)
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 5000.0)
        self.assertEqual(self.rows(self.get(month=ym(PREV)))['Food']['limit'], 5000.0)

    def test_every_month_changes_future_not_past(self):
        self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': 6000, 'scope': 'every_month'})
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 6000.0)
        self.assertEqual(self.rows(self.get(month=ym(PREV)))['Food']['limit'], 5000.0)

    def test_acceptance_flow_raise_this_month_then_every_month_then_reset(self):
        body = lambda amount, scope: {'category_id': self.food.id, 'month': ym(CUR), 'amount': amount, 'scope': scope}  # noqa: E731
        self.put(body(8000, 'this_month'))
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 5000.0)    # next month unchanged
        self.put(body(7000, 'every_month'))                                              # replaces the override
        self.assertEqual(self.rows(self.get(month=ym(CUR)))['Food']['limit_source'], 'default')
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 7000.0)
        self.assertEqual(self.rows(self.get(month=ym(PREV)))['Food']['limit'], 5000.0)    # past unchanged
        self.put(body(9000, 'this_month'))
        resp = self.post({'category_id': self.food.id, 'month': ym(CUR)}, 'budgets-api-reset')
        self.assertEqual(self.rows(resp)['Food']['limit'], 7000.0)
        self.assertEqual(self.rows(resp)['Food']['limit_source'], 'default')

    def test_null_amount_removes_the_limit_with_scope(self):
        resp = self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': None, 'scope': 'this_month'})
        self.assertIsNone(self.rows(resp)['Food']['limit'])
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 5000.0)
        self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': None, 'scope': 'every_month'})
        self.assertIsNone(self.rows(self.get(month=ym(NEXT)))['Food']['limit'])

    def test_every_month_for_a_past_month_is_rejected_but_this_month_is_fine(self):
        resp = self.put({'category_id': self.food.id, 'month': ym(PREV), 'amount': 1, 'scope': 'every_month'})
        self.assertEqual((resp.status_code, resp.json()['code']), (400, 'past_every_month'))
        resp = self.put({'category_id': self.food.id, 'month': ym(PREV), 'amount': 1, 'scope': 'this_month'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.rows(resp)['Food']['limit'], 1.0)

    def test_lowering_below_spend_is_allowed_and_shows_over(self):
        resp = self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': 100, 'scope': 'this_month'})
        self.assertEqual(self.rows(resp)['Food']['status'], 'over')

    def test_validation_errors(self):
        ok = {'category_id': self.food.id, 'month': ym(CUR), 'amount': 10, 'scope': 'this_month'}
        cases = [
            ({**ok, 'amount': -5}, 400, 'invalid_amount'),
            ({**ok, 'amount': 'abc'}, 400, 'invalid_amount'),
            ({**ok, 'amount': True}, 400, 'invalid_amount'),
            ({**ok, 'amount': [1]}, 400, 'invalid_amount'),
            ({k: v for k, v in ok.items() if k != 'amount'}, 400, 'invalid_amount'),
            ({**ok, 'scope': 'forever'}, 400, 'invalid_scope'),
            ({**ok, 'scope': None}, 400, 'invalid_scope'),
            ({**ok, 'month': '2026-13'}, 400, 'invalid_request'),
            ({**ok, 'month': None}, 400, 'invalid_request'),
            ({**ok, 'category_id': 999999}, 404, 'not_found'),
            ({**ok, 'category_id': None}, 404, 'not_found'),
        ]
        for body, status, code in cases:
            resp = self.put(body)
            self.assertEqual((resp.status_code, resp.json()['code']), (status, code), body)
            self.assertFalse(resp.json()['success'])
        self.assertFalse(BudgetMonthOverride.objects.exists())
        resp = self.client.put(reverse('budgets-api-limit'), 'not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_accepts_category_uuid_and_string_amounts(self):
        resp = self.put({'category_id': str(self.food.uuid), 'month': ym(CUR), 'amount': '6500.50',
                         'scope': 'this_month'})
        self.assertEqual(self.rows(resp)['Food']['limit'], 6500.5)

    def test_ownership_and_auth(self):
        other = self.make_user('intruder')
        theirs = Category.objects.create(user=other, name='Theirs', limit=D('10'))
        resp = self.put({'category_id': theirs.id, 'month': ym(CUR), 'amount': 1, 'scope': 'this_month'})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resolver.effective_limit(other, theirs, CUR), D('10'))
        self.client.logout()
        resp = self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': 1, 'scope': 'this_month'})
        self.assertEqual(resp.status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('budgets-api-limit')).status_code, 405)

    def test_concurrent_style_duplicate_writes_leave_one_row(self):
        for amount in (1, 2, 3):
            self.put({'category_id': self.food.id, 'month': ym(NEXT), 'amount': amount, 'scope': 'every_month'})
        self.assertEqual(BudgetLimitVersion.objects.filter(category_name='food', effective_from=NEXT).count(), 1)
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 3.0)

    def test_budget_page_cache_is_cleared_by_writes(self):
        cache.set(f'budget_dashboard_{self.user.id}', 'stale')
        self.put({'category_id': self.food.id, 'month': ym(CUR), 'amount': 1, 'scope': 'this_month'})
        self.assertIsNone(cache.get(f'budget_dashboard_{self.user.id}'))


class PlanLockApiTests(Base):
    tier = 'FREE'

    def test_locked_categories_are_hidden_refused_and_not_counted(self):
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        extra = [Category.objects.create(user=self.user, name=f'X{i}', limit=D('100')) for i in range(cap)]
        backdate_limits(self.user, add_months(CUR, -3))
        locked = extra[-1]
        p = self.get().json()
        self.assertEqual(len(p['categories']), cap)
        self.assertNotIn(locked.name, [r['name'] for r in p['categories']])
        self.assertEqual(p['locked_count'], 3)   # Food, Fuel, Fun are the oldest; the extras fill the rest
        resp = self.put({'category_id': locked.id, 'month': ym(CUR), 'amount': 1, 'scope': 'this_month'})
        self.assertEqual((resp.status_code, resp.json()['code']), (403, 'plan_locked'))
        allowed_total = sum(r['limit'] or 0 for r in p['categories'])
        self.assertEqual(p['total'], allowed_total)


class RebalanceApiTests(Base):
    def move(self, amount, src=None, dst=None, scope='this_month', month=None):
        return self.post({'month': ym(month or CUR), 'scope': scope, 'moves': [
            {'from_category': (src or self.food).id, 'to_category': (dst or self.fun).id, 'amount': amount}]})

    def test_moves_limit_and_returns_the_page(self):
        Expense.objects.filter(user=self.user).delete()
        resp = self.move(1500, dst=self.fuel)
        self.assertEqual(resp.status_code, 200)
        rows = self.rows(resp)
        self.assertEqual((rows['Food']['limit'], rows['Fuel']['limit']), (3500.0, 3500.0))
        self.assertEqual(resp.json()['total'], 7000.0)               # moving never changes the total
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Food']['limit'], 5000.0)

    def test_rejects_source_without_limit_and_below_spent_atomically(self):
        resp = self.move(10, src=self.fun, dst=self.food)
        self.assertEqual((resp.status_code, resp.json()['code']), (400, 'source_no_limit'))
        resp = self.post({'month': ym(CUR), 'scope': 'this_month', 'moves': [
            {'from_category': self.fuel.id, 'to_category': self.food.id, 'amount': 1},
            {'from_category': self.food.id, 'to_category': self.fun.id, 'amount': 1000},   # Food spent 4500 of 5001
        ]})
        self.assertEqual(resp.json()['code'], 'below_spent')
        self.assertFalse(BudgetMonthOverride.objects.exists())

    def test_validation_and_ownership(self):
        other = self.make_user('other2')
        theirs = Category.objects.create(user=other, name='Theirs', limit=D('10'))
        for body, status in [
            ({'month': ym(CUR), 'scope': 'this_month', 'moves': []}, 400),
            ({'month': ym(CUR), 'scope': 'this_month'}, 400),
            ({'month': ym(CUR), 'scope': 'this_month', 'moves': ['x']}, 400),
            ({'month': 'bad', 'scope': 'this_month', 'moves': [{}]}, 400),
            ({'month': ym(CUR), 'scope': 'nope', 'moves': [
                {'from_category': self.food.id, 'to_category': self.fun.id, 'amount': 1}]}, 400),
            ({'month': ym(CUR), 'scope': 'this_month', 'moves': [
                {'from_category': self.food.id, 'to_category': theirs.id, 'amount': 1}]}, 404),
            ({'month': ym(CUR), 'scope': 'this_month', 'moves': [
                {'from_category': self.food.id, 'to_category': self.food.id, 'amount': 1}]}, 400),
            ({'month': ym(CUR), 'scope': 'this_month', 'moves': [
                {'from_category': self.food.id, 'to_category': self.fun.id, 'amount': -1}]}, 400),
        ]:
            self.assertEqual(self.post(body).status_code, status, body)
        self.client.logout()
        self.assertEqual(self.move(1).status_code, 401)

    def test_every_month_scope_rejected_for_past_months(self):
        resp = self.move(1, scope='every_month', month=PREV)
        self.assertEqual(resp.json()['code'], 'past_every_month')


class QueryCountTests(Base):
    def test_payload_is_three_queries(self):
        for i in range(10):
            Category.objects.create(user=self.user, name=f'Cat{i}', limit=D('100'))
            self.spend(f'Cat{i}', 10)
        user = User.objects.select_related('profile').get(pk=self.user.pk)
        writes.set_limit(user, self.food, CUR, 1, 'this_month')
        writes.set_limit(user, self.fuel, NEXT, 1, 'every_month')
        for month in (PREV, CUR, NEXT):
            with CaptureQueriesContext(connection) as ctx:
                build_budget_page(user, month, TODAY)
            self.assertLessEqual(len(ctx), 3, [q['sql'] for q in ctx.captured_queries])

    def test_query_count_does_not_grow_with_categories(self):
        user = User.objects.select_related('profile').get(pk=self.user.pk)
        with CaptureQueriesContext(connection) as few:
            build_budget_page(user, CUR, TODAY)
        for i in range(25):
            Category.objects.create(user=self.user, name=f'More{i}', limit=D('50'))
            self.spend(f'More{i}', 5)
        with CaptureQueriesContext(connection) as many:
            build_budget_page(user, CUR, TODAY)
        self.assertEqual(len(few), len(many))


def resolver_days_left():
    from expenses.budgets import days_left as dl
    return dl(CUR, TODAY)


class PaceBandTests(Base):
    """pace_state: within 5% of the total limit either way is on pace (a spend exactly on the edge too)."""

    def state(self, spent, today=date(2026, 6, 15)):
        Expense.objects.filter(user=self.user).delete()
        Category.objects.filter(user=self.user).exclude(pk=self.food.pk).delete()
        Category.objects.filter(pk=self.food.pk).update(name='Food')
        writes.set_limit(self.user, self.food, date(2026, 6, 1), 3000, 'this_month')
        self.spend('Food', spent, when=date(2026, 6, 10))
        return build_budget_page(self.user, date(2026, 6, 1), today)

    def test_band_edges_on_day_15_of_30(self):
        band = 150                                    # 5% of 3000; expected pace by day 15 is 1500
        self.assertEqual(self.state(1500)['pace_state'], 'on_track')
        self.assertEqual(self.state(1500 + band)['pace_state'], 'on_track')
        self.assertEqual(self.state(1500 + band + 1)['pace_state'], 'ahead')
        self.assertEqual(self.state(1500 - band)['pace_state'], 'on_track')
        self.assertEqual(self.state(1500 - band - 1)['pace_state'], 'under')
        p = self.state(1000)
        self.assertEqual((p['expected_pace'], p['pace_delta']), (1500.0, 500.0))
        self.assertEqual(p['days_left'], 16)


class TranslationTests(Base):
    """The page is translated (Hindi/Marathi) including the strings the script reads."""

    def page(self, lang):
        import os

        from django.conf import settings
        if not os.path.exists(os.path.join(settings.BASE_DIR, 'locale', lang, 'LC_MESSAGES', 'django.mo')):
            self.skipTest('compiled catalog (compilemessages) not present')
        profile = self.user.profile
        profile.language = lang
        profile.save()
        return self.client.get(reverse('budget')).content.decode()

    def test_hindi(self):
        html = self.page('hi')
        self.assertIn('आज सुरक्षित खर्च', html)
        self.assertIn('सीमा बदलें', html)
        # percent signs in script templates come out single, so %(name)s placeholders still work
        self.assertIn('data-p-this="%(name)s: %(from)s → %(to)s, %(month)s के लिए"', html)

    def test_marathi(self):
        html = self.page('mr')
        self.assertIn('आज सुरक्षित खर्च', html)
        self.assertIn('मर्यादा बदला', html)
        self.assertIn('data-p-every="%(name)s: %(from)s → %(to)s, %(month)s पासून पुढे"', html)


class NewBudgetDialogTests(Base):
    """"New budget" is a dialog on the page: pick a category without a limit or create one, set the limit."""

    def test_button_opens_the_dialog_instead_of_leaving_the_page(self):
        html = self.client.get(reverse('budget')).content.decode()
        self.assertIn('data-bdx-new', html)
        self.assertIn('id="bdx-new"', html)
        self.assertIn('id="bdx-new-form"', html)
        self.assertIn(reverse('category-create-ajax'), html)
        self.assertNotIn(reverse('category-create') + '?next', html)

    def test_dialog_data_lists_categories_without_a_limit(self):
        rows = {r['name']: r for r in self.get().json()['categories']}
        self.assertIsNone(rows['Fun']['limit'])          # offered in the dialog's picker
        self.assertTrue(rows['Food']['limit'])           # already budgeted, not offered

    def test_create_a_category_then_budget_it_in_one_flow(self):
        created = self.client.post(reverse('category-create-ajax'), json.dumps({'name': 'Pets'}),
                                   content_type='application/json').json()
        self.assertTrue(created['success'])
        resp = self.put({'category_id': created['id'], 'month': ym(CUR), 'amount': 1800, 'scope': 'every_month'})
        self.assertEqual(resp.status_code, 200)
        rows = self.rows(resp)
        self.assertEqual(rows['Pets']['limit'], 1800.0)
        self.assertEqual(self.rows(self.get(month=ym(NEXT)))['Pets']['limit'], 1800.0)

    def test_pick_an_existing_category_without_a_limit(self):
        resp = self.put({'category_id': self.fun.id, 'month': ym(CUR), 'amount': 500, 'scope': 'this_month'})
        self.assertEqual(self.rows(resp)['Fun']['limit'], 500.0)

    def test_duplicate_name_is_refused_by_the_create_step(self):
        created = self.client.post(reverse('category-create-ajax'), json.dumps({'name': 'food'}),
                                   content_type='application/json').json()
        self.assertFalse(created['success'])

    def test_button_is_still_offered_when_only_the_plan_blocks_new_categories(self):
        free = self.make_user('free-new', 'FREE')
        from finance_tracker.plans import get_limit
        for i in range(get_limit('FREE', 'budget_categories')):
            Category.objects.create(user=free, name=f'F{i}')          # all five slots used, none has a limit
        self.client.force_login(free)
        resp = self.client.get(reverse('budget'))
        self.assertTrue(resp.context['new_budget_possible'])
        self.assertContains(resp, 'data-bdx-new')
        self.assertContains(resp, 'data-can-add="0"')

    def test_upgrade_link_when_nothing_can_be_budgeted_or_created(self):
        free = self.make_user('free-full', 'FREE')
        from finance_tracker.plans import get_limit
        for i in range(get_limit('FREE', 'budget_categories')):
            Category.objects.create(user=free, name=f'F{i}', limit=D('10'))
        self.client.force_login(free)
        resp = self.client.get(reverse('budget'))
        self.assertFalse(resp.context['new_budget_possible'])
        self.assertContains(resp, reverse('pricing'))


class BulkLimitsApiTests(Base):
    """PUT api/budgets/limits/ sets several limits atomically."""

    def test_sets_many_in_one_request(self):
        resp = self.put({'month': ym(CUR), 'scope': 'every_month',
                         'limits': [{'category_id': self.fun.id, 'amount': 700},
                                    {'category_id': self.fuel.id, 'amount': 2500}]}, 'budgets-api-limits')
        self.assertEqual(resp.status_code, 200)
        rows = self.rows(resp)
        self.assertEqual(rows['Fun']['limit'], 700.0)
        self.assertEqual(rows['Fuel']['limit'], 2500.0)

    def test_one_bad_row_rolls_everything_back(self):
        resp = self.put({'month': ym(CUR), 'scope': 'this_month',
                         'limits': [{'category_id': self.fun.id, 'amount': 700},
                                    {'category_id': self.fuel.id, 'amount': -5}]}, 'budgets-api-limits')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'invalid_amount')
        self.assertEqual(self.rows(self.get(month=ym(CUR)))['Fun']['limit'], None)

    def test_rejects_empty_duplicates_and_other_users_categories(self):
        url = 'budgets-api-limits'
        self.assertEqual(self.put({'month': ym(CUR), 'scope': 'this_month', 'limits': []}, url).status_code, 400)
        dup = [{'category_id': self.fun.id, 'amount': 1}, {'category_id': self.fun.id, 'amount': 2}]
        self.assertEqual(self.put({'month': ym(CUR), 'scope': 'this_month', 'limits': dup}, url).json()['code'],
                         'duplicate_category')
        other = self.make_user('someone-else')
        theirs = Category.objects.create(user=other, name='Theirs')
        resp = self.put({'month': ym(CUR), 'scope': 'this_month',
                         'limits': [{'category_id': theirs.id, 'amount': 5}]}, url)
        self.assertEqual(resp.status_code, 404)

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.put({'month': ym(CUR), 'limits': []}, 'budgets-api-limits').status_code, 401)


class SuggestionHousekeepingTests(Base):
    def test_dismissing_prunes_old_and_caps_rows(self):
        from expenses.models import BudgetSuggestionDismissal as D_
        old = add_months(CUR, -6)
        D_.objects.create(user=self.user, key=f'move:1:2:{ym(old)}')
        keep = D_.objects.create(user=self.user, key=f'move:1:3:{ym(PREV)}')
        key = f'move:{self.food.id}:{self.fuel.id}:{ym(CUR)}'
        resp = self.post({'key': key}, 'budgets-api-dismiss')
        self.assertEqual(resp.status_code, 200)
        keys = set(D_.objects.filter(user=self.user).values_list('key', flat=True))
        self.assertEqual(keys, {keep.key, key})

    def test_fragment_reuses_cached_rows(self):
        cache.set(f'budget_suggestion_rows_{self.user.id}', [], 60)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('budgets-api-suggestion'), {'month': ym(CUR)})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'')
        self.assertFalse([q for q in ctx.captured_queries if 'expenses_expense' in q['sql']])

    def test_limit_write_drops_the_cached_rows(self):
        cache.set(f'budget_suggestion_rows_{self.user.id}', [{'x': 1}], 60)
        self.put({'category_id': self.fun.id, 'month': ym(CUR), 'amount': 50, 'scope': 'this_month'})
        self.assertIsNone(cache.get(f'budget_suggestion_rows_{self.user.id}'))


class CategoryFormPlanLockTests(TestCase):
    def test_locked_category_limit_cannot_change_but_rename_works(self):
        from expenses.forms import CategoryForm
        user = User.objects.create_user('lockform', 'lockform@example.com', 'pw')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier = 'FREE'
        profile.save()
        Category.objects.filter(user=user).delete()
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        cats = [Category.objects.create(user=user, name=f'C{i}') for i in range(cap + 1)]
        locked = cats[-1]
        bad = CategoryForm({'name': 'C-locked', 'icon': '', 'limit': '900'}, instance=Category.objects.get(pk=locked.pk))
        self.assertFalse(bad.is_valid())
        self.assertIn('limit', bad.errors)
        ok = CategoryForm({'name': 'Renamed', 'icon': '', 'limit': ''}, instance=Category.objects.get(pk=locked.pk))
        self.assertTrue(ok.is_valid(), ok.errors)
