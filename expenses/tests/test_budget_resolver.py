"""Versioned budget limits: the resolver, the write rules (scopes), rebalance, the backfill and
the plan lock. Months are relative to today so the suite never goes stale."""

import importlib
from datetime import date
from decimal import Decimal

from django.apps import apps
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from expenses import budget_resolver as resolver
from expenses import budget_writes as writes
from expenses.budget_writes import BudgetError, BudgetPlanLocked
from expenses.models import (
    BudgetLimitVersion,
    BudgetMonthOverride,
    Category,
    Expense,
    UserProfile,
)

D = Decimal


def add_months(d, n):
    idx = d.year * 12 + d.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


CUR = timezone.localdate().replace(day=1)
PREV, PREV2, NEXT, NEXT2 = (add_months(CUR, n) for n in (-1, -2, 1, 2))


class Base(TestCase):
    tier = 'PRO'

    def setUp(self):
        cache.clear()
        self.user = self.make_user('res-user', self.tier)

    def make_user(self, name, tier='PRO'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier = tier
        profile.save()
        user.refresh_from_db()
        Category.objects.filter(user=user).delete()
        return user

    def cat(self, name, user=None):
        return Category.objects.create(user=user or self.user, name=name)

    def version(self, cat, month, amount):
        return BudgetLimitVersion.objects.create(
            user=cat.user, category=cat, category_name=cat.name.lower(),
            effective_from=month, amount=None if amount is None else D(str(amount)))

    def override(self, cat, month, amount):
        return BudgetMonthOverride.objects.create(
            user=cat.user, category=cat, category_name=cat.name.lower(),
            month=month, amount=None if amount is None else D(str(amount)))

    def limit(self, cat, month):
        return resolver.effective_limit(self.user, cat, month)


class ResolverTests(Base):
    def test_no_rows_means_no_limit(self):
        food = self.cat('Food')
        self.assertIsNone(self.limit(food, CUR))
        self.assertEqual(resolver.effective_limits(self.user, CUR, [food]), {food.id: None})

    def test_version_applies_from_its_month_onward_only(self):
        food = self.cat('Food')
        self.version(food, CUR, 5000)
        self.assertIsNone(self.limit(food, PREV))
        self.assertEqual(self.limit(food, CUR), D('5000'))
        self.assertEqual(self.limit(food, add_months(CUR, 7)), D('5000'))

    def test_latest_version_wins_and_past_is_unaffected_by_new_version(self):
        food = self.cat('Food')
        self.version(food, PREV2, 4000)
        before = self.limit(food, PREV)
        self.version(food, CUR, 6000)
        self.assertEqual(before, D('4000'))
        self.assertEqual(self.limit(food, PREV), D('4000'))
        self.assertEqual(self.limit(food, PREV2), D('4000'))
        self.assertEqual(self.limit(food, CUR), D('6000'))
        self.assertEqual(self.limit(food, NEXT), D('6000'))

    def test_override_beats_version_for_that_month_only(self):
        food = self.cat('Food')
        self.version(food, PREV2, 4000)
        self.override(food, CUR, 9000)
        self.assertEqual(self.limit(food, PREV), D('4000'))
        self.assertEqual(self.limit(food, CUR), D('9000'))
        self.assertEqual(self.limit(food, NEXT), D('4000'))

    def test_null_version_removes_the_limit_from_then_on(self):
        food = self.cat('Food')
        self.version(food, PREV2, 4000)
        self.version(food, CUR, None)
        self.assertEqual(self.limit(food, PREV), D('4000'))
        self.assertIsNone(self.limit(food, CUR))
        self.assertIsNone(self.limit(food, NEXT))

    def test_null_override_means_no_limit_this_month(self):
        food = self.cat('Food')
        self.version(food, PREV2, 4000)
        self.override(food, CUR, None)
        self.assertIsNone(self.limit(food, CUR))
        self.assertEqual(self.limit(food, NEXT), D('4000'))

    def test_dates_inside_a_month_resolve_to_that_month(self):
        food = self.cat('Food')
        self.version(food, CUR, 100)
        self.assertEqual(self.limit(food, CUR.replace(day=17)), D('100'))

    def test_names_match_case_insensitively_and_users_are_isolated(self):
        food = self.cat('Food')
        self.version(food, CUR, 100)
        other = self.make_user('someone-else')
        theirs = self.cat('Food', user=other)
        self.assertIsNone(resolver.effective_limit(other, theirs, CUR))
        renamed_case = Category(user=self.user, name=' FOOD ')
        self.assertEqual(resolver.effective_limit(self.user, renamed_case, CUR), D('100'))

    def test_deleted_category_still_resolves_for_past_months(self):
        food = self.cat('Food')
        self.version(food, PREV2, 4000)
        food.delete()
        by_name = resolver.effective_limits_by_name(self.user, PREV)
        self.assertEqual(by_name['food'], D('4000'))
        self.assertTrue(BudgetLimitVersion.objects.filter(user=self.user, category__isnull=True).exists())

    def test_range_returns_each_month_in_one_query(self):
        food, fuel = self.cat('Food'), self.cat('Fuel')
        self.version(food, PREV2, 100)
        self.version(food, CUR, 200)
        self.override(fuel, PREV, 50)
        cats = [food, fuel]
        with CaptureQueriesContext(connection) as ctx:
            out = resolver.effective_limits_range(self.user, PREV2, NEXT, cats)
        self.assertEqual(len(ctx), 1)
        self.assertEqual(list(out), [PREV2, PREV, CUR, NEXT])
        self.assertEqual([out[m][food.id] for m in out], [D('100'), D('100'), D('200'), D('200')])
        self.assertEqual([out[m][fuel.id] for m in out], [None, D('50'), None, None])

    def test_single_query_per_call(self):
        food = self.cat('Food')
        self.version(food, CUR, 100)
        self.override(food, CUR, 90)
        with CaptureQueriesContext(connection) as ctx:
            resolver.effective_limits(self.user, CUR, [food])
            resolver.total_budget(self.user, CUR, [food])
        self.assertEqual(len(ctx), 2)  # one per call

    def test_total_is_the_sum_of_effective_limits(self):
        food, fuel, fun = self.cat('Food'), self.cat('Fuel'), self.cat('Fun')
        self.version(food, CUR, 100)
        self.version(fuel, CUR, 50)
        self.version(fun, CUR, None)
        self.override(food, CUR, 120)
        self.assertEqual(resolver.total_budget(self.user, CUR), D('170'))
        self.assertEqual(resolver.total_budget(self.user, PREV), D('0'))
        self.assertEqual(resolver.total_budget(self.user, CUR, [fuel]), D('50'))

    def test_zero_limit_counts_as_no_limit_in_total(self):
        food = self.cat('Food')
        self.version(food, CUR, 0)
        self.assertEqual(resolver.total_budget(self.user, CUR), D('0'))


class WriteRuleTests(Base):
    def setUp(self):
        super().setUp()
        self.food = self.cat('Food')
        self.version(self.food, PREV2, 5000)

    def set(self, month, amount, scope):
        return writes.set_limit(self.user, self.food, month, amount, scope)

    def test_this_month_creates_override_and_leaves_versions_alone(self):
        self.set(CUR, 6000, 'this_month')
        self.assertEqual(self.limit(self.food, CUR), D('6000'))
        self.assertEqual(self.limit(self.food, NEXT), D('5000'))
        self.assertEqual(BudgetLimitVersion.objects.count(), 1)

    def test_every_month_changes_future_not_past(self):
        self.set(CUR, 6000, 'every_month')
        self.assertEqual(self.limit(self.food, PREV), D('5000'))
        self.assertEqual(self.limit(self.food, CUR), D('6000'))
        self.assertEqual(self.limit(self.food, NEXT2), D('6000'))

    def test_every_month_deletes_same_month_override_but_keeps_later_ones(self):
        self.override(self.food, CUR, 1)
        self.override(self.food, NEXT, 7777)
        self.set(CUR, 6000, 'every_month')
        self.assertFalse(BudgetMonthOverride.objects.filter(month=CUR).exists())
        self.assertEqual(self.limit(self.food, NEXT), D('7777'))
        self.assertEqual(self.limit(self.food, NEXT2), D('6000'))

    def test_every_month_not_allowed_for_past_months_but_this_month_is(self):
        with self.assertRaises(BudgetError) as cm:
            self.set(PREV, 1, 'every_month')
        self.assertEqual(cm.exception.code, 'past_every_month')
        self.set(PREV, 1234, 'this_month')
        self.assertEqual(self.limit(self.food, PREV), D('1234'))
        self.assertEqual(self.limit(self.food, CUR), D('5000'))

    def test_second_edit_same_month_replaces_the_first(self):
        self.set(CUR, 6000, 'every_month')
        self.set(CUR, 6500, 'every_month')
        self.assertEqual(BudgetLimitVersion.objects.filter(effective_from=CUR).count(), 1)
        self.assertEqual(self.limit(self.food, CUR), D('6500'))
        self.set(CUR, 1, 'this_month')
        self.set(CUR, 2, 'this_month')
        self.assertEqual(BudgetMonthOverride.objects.filter(month=CUR).count(), 1)
        self.assertEqual(self.limit(self.food, CUR), D('2'))

    def test_future_every_month_then_edit_earlier_month_keeps_later_version(self):
        self.set(NEXT2, 9000, 'every_month')
        self.set(NEXT, 6000, 'every_month')
        self.assertEqual(self.limit(self.food, NEXT), D('6000'))
        self.assertEqual(self.limit(self.food, NEXT2), D('9000'))

    def test_remove_limit_is_scoped_like_edits(self):
        self.set(CUR, None, 'this_month')
        self.assertIsNone(self.limit(self.food, CUR))
        self.assertEqual(self.limit(self.food, NEXT), D('5000'))
        self.set(NEXT, None, 'every_month')
        self.assertIsNone(self.limit(self.food, NEXT2))
        self.assertEqual(self.limit(self.food, PREV), D('5000'))

    def test_reset_override_returns_to_default(self):
        self.set(CUR, 9000, 'this_month')
        self.assertEqual(writes.reset_override(self.user, self.food, CUR), D('5000'))
        self.assertFalse(BudgetMonthOverride.objects.exists())

    def test_lowering_below_spend_is_allowed(self):
        Expense.objects.create(user=self.user, date=CUR, amount=D('900'), description='x',
                               category='Food', currency='₹')
        self.assertEqual(self.set(CUR, 100, 'this_month'), D('100'))

    def test_validation(self):
        for bad in (-1, 'abc', float('nan'), 10 ** 14):
            with self.assertRaises(BudgetError) as cm:
                self.set(CUR, bad, 'this_month')
            self.assertEqual(cm.exception.code, 'invalid_amount')
        with self.assertRaises(BudgetError) as cm:
            self.set(CUR, 1, 'forever')
        self.assertEqual(cm.exception.code, 'invalid_scope')
        self.assertEqual(self.limit(self.food, CUR), D('5000'))

    def test_zero_is_stored_and_other_users_category_is_rejected(self):
        self.assertEqual(self.set(CUR, 0, 'this_month'), D('0.00'))
        other = self.make_user('intruder')
        with self.assertRaises(BudgetError) as cm:
            writes.set_limit(other, self.food, CUR, 1, 'this_month')
        self.assertEqual(cm.exception.code, 'not_found')

    def test_write_clears_the_budget_page_cache(self):
        cache.set(f'budget_dashboard_{self.user.id}', 'stale')
        self.set(CUR, 1, 'this_month')
        self.assertIsNone(cache.get(f'budget_dashboard_{self.user.id}'))

    def test_rename_moves_history(self):
        self.set(CUR, 6000, 'this_month')
        self.food.name = 'Groceries'
        self.food.save()
        writes.rename_category_history(self.user, 'Food', 'Groceries', self.food)
        self.assertEqual(self.limit(self.food, CUR), D('6000'))
        self.assertEqual(self.limit(self.food, PREV), D('5000'))


class PlanLockTests(Base):
    tier = 'FREE'

    def test_allowed_ids_follow_plans_py(self):
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        cats = [self.cat(f'C{i}') for i in range(cap + 2)]
        allowed = resolver.plan_allowed_category_ids(self.user)
        self.assertEqual(allowed, [c.id for c in cats[:cap]])

    def test_locked_category_cannot_be_written(self):
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        cats = [self.cat(f'C{i}') for i in range(cap + 1)]
        writes.set_limit(self.user, cats[0], CUR, 10, 'this_month')
        with self.assertRaises(BudgetPlanLocked):
            writes.set_limit(self.user, cats[-1], CUR, 10, 'this_month')

    def test_pro_is_unlimited(self):
        pro = self.make_user('pro-user', 'PRO')
        cats = [self.cat(f'C{i}', user=pro) for i in range(30)]
        self.assertEqual(len(resolver.plan_allowed_category_ids(pro)), len(cats))


class RebalanceTests(Base):
    def setUp(self):
        super().setUp()
        self.food, self.fuel = self.cat('Food'), self.cat('Fuel')
        self.version(self.food, PREV2, 5000)
        self.version(self.fuel, PREV2, 1000)

    def move(self, amount, src=None, dst=None, scope='this_month'):
        return writes.rebalance(self.user, CUR, scope, [
            {'from_category': src or self.food, 'to_category': dst or self.fuel, 'amount': amount}])

    def test_moves_limit_between_categories(self):
        self.move(1500)
        self.assertEqual(self.limit(self.food, CUR), D('3500'))
        self.assertEqual(self.limit(self.fuel, CUR), D('2500'))
        self.assertEqual(self.limit(self.food, NEXT), D('5000'))

    def test_every_month_scope_moves_going_forward(self):
        self.move(1000, scope='every_month')
        self.assertEqual(self.limit(self.food, NEXT2), D('4000'))
        self.assertEqual(self.limit(self.food, PREV), D('5000'))

    def test_rejects_source_without_limit(self):
        none = self.cat('Gadgets')
        with self.assertRaises(BudgetError) as cm:
            self.move(10, src=none)
        self.assertEqual(cm.exception.code, 'source_no_limit')

    def test_rejects_move_below_spent_and_rolls_back(self):
        Expense.objects.create(user=self.user, date=CUR, amount=D('4800'), description='x',
                               category='Food', currency='₹')
        with self.assertRaises(BudgetError) as cm:
            writes.rebalance(self.user, CUR, 'this_month', [
                {'from_category': self.fuel, 'to_category': self.food, 'amount': 100},
                {'from_category': self.food, 'to_category': self.fuel, 'amount': 500},
            ])
        self.assertEqual(cm.exception.code, 'below_spent')
        self.assertFalse(BudgetMonthOverride.objects.exists())

    def test_rejects_empty_and_self_moves(self):
        for moves in ([], [{'from_category': self.food, 'to_category': self.food, 'amount': 5}]):
            with self.assertRaises(BudgetError):
                writes.rebalance(self.user, CUR, 'this_month', moves)


class BackfillTests(Base):
    def run_backfill(self):
        module = importlib.import_module('expenses.migrations.0107_backfill_budget_limit_versions')
        module.backfill(apps, None)

    def test_version_starts_at_earliest_expense_month_so_history_is_unchanged(self):
        food = self.cat('Food')
        food.limit = D('5000')
        food.save()
        Expense.objects.create(user=self.user, date=PREV2.replace(day=20), amount=D('1'),
                               description='x', category='Food', currency='₹')
        self.run_backfill()
        self.assertEqual(self.limit(food, PREV2), D('5000'))
        self.assertEqual(self.limit(food, CUR), D('5000'))
        self.assertIsNone(self.limit(food, add_months(PREV2, -1)))

    def test_no_expenses_falls_back_to_current_month_and_skips_blank_limits(self):
        food, fuel = self.cat('Food'), self.cat('Fuel')
        food.limit = D('100')
        food.save()
        self.run_backfill()
        self.assertEqual(BudgetLimitVersion.objects.get().effective_from, CUR)
        self.assertIsNone(self.limit(fuel, CUR))

    def test_backfill_is_rerunnable(self):
        food = self.cat('Food')
        food.limit = D('100')
        food.save()
        self.run_backfill()
        self.run_backfill()
        self.assertEqual(BudgetLimitVersion.objects.count(), 1)
