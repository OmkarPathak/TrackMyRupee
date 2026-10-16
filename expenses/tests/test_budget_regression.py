"""Regression: every figure that uses budget limits, for the CURRENT month, is what it was before
limits became versioned (Category.limit -> expenses.budget_resolver), and all readers agree.

Expected values are derived by hand from the scenario below, so they hold on any day of the month
and were verified green against the pre-refactor code first."""

import calendar
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses import budget_resolver as resolver
from expenses import budget_writes as writes
from expenses.models import (
    BudgetLimitVersion,
    Category,
    Expense,
    Notification,
    UserProfile,
)
from expenses.tests.budget_helpers import backdate_limits, legacy_page_ctx

D = Decimal
TODAY = timezone.localdate()


class Scenario(TestCase):
    tier = 'PRO'

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('reg-user', 'reg@example.com', 'pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = self.tier
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        Category.objects.filter(user=self.user).delete()
        self.food = Category.objects.create(user=self.user, name='Food', limit=D('5000'))
        self.fuel = Category.objects.create(user=self.user, name='Fuel', limit=D('2000'))
        self.rent = Category.objects.create(user=self.user, name='Rent', limit=D('10000'))
        self.fun = Category.objects.create(user=self.user, name='Fun')           # no limit
        self.spend('Food', 4500)     # 90%  -> at limit
        self.spend('Fuel', 2500)     # 125% -> over
        self.spend('Rent', 100)      # 1%   -> on track
        self.spend('Fun', 300)       # no limit
        self.client.force_login(self.user)

    def spend(self, category, amount):
        return Expense.objects.create(user=self.user, date=TODAY, amount=D(amount), description='x',
                                      category=category, currency='₹')


class HomeDashboardTests(Scenario):
    def test_category_limits_for_the_current_month(self):
        resp = self.client.get(reverse('home'))
        rows = {c['name']: c for c in resp.context['category_limits']}
        self.assertEqual(rows['Food']['limit'], 5000.0)
        self.assertEqual(rows['Food']['monthly_limit'], 5000.0)
        self.assertEqual(rows['Food']['used_percent'], 90.0)
        self.assertEqual(rows['Food']['status'], 'limit')
        self.assertEqual(rows['Fuel']['limit'], 2000.0)
        self.assertEqual(rows['Fuel']['used_percent'], 125.0)
        self.assertEqual(rows['Fuel']['status'], 'over')
        self.assertEqual(rows['Rent']['status'], 'ontrack')
        self.assertIsNone(rows['Fun']['limit'])
        self.assertEqual(rows['Fun']['status'], 'nolimit')
        self.assertTrue(resp.context['has_any_budget'])

    def test_budget_breach_insight_names_the_over_category(self):
        resp = self.client.get(reverse('home'))
        texts = ' '.join(str(i.get('text', '')) for i in resp.context['smart_bullet_insights'])
        self.assertIn('Fuel', texts)
        self.assertIn('exceeded budget by', texts)

    def test_no_limits_means_no_budget(self):
        Category.objects.filter(user=self.user).delete()
        Category.objects.create(user=self.user, name='Food')
        cache.clear()
        resp = self.client.get(reverse('home'))
        self.assertFalse(resp.context['has_any_budget'])


class AnalyticsTests(Scenario):
    def test_burn_down_budget_line_ends_at_the_total_of_all_limits(self):
        resp = self.client.get(reverse('analytics'))
        line = resp.context['burn_down_budget']
        self.assertEqual(line[-1], 17000.0)          # 5000 + 2000 + 10000, a full month of budget
        self.assertEqual(len(line), len(resp.context['burn_down_labels']))


class BudgetPageTests(Scenario):
    def test_page_totals_and_rows(self):
        ctx = legacy_page_ctx(self.client.get(reverse('budget')).context)
        self.assertEqual(ctx['total_budget'], D('17000'))
        rows = {r['category'].name: r for r in ctx['budget_data']}
        self.assertEqual(rows['Food']['limit'], D('5000'))
        self.assertEqual(rows['Fuel']['status'], 'over')
        self.assertEqual(rows['Fuel']['over_budget'], D('500'))
        self.assertEqual(rows['Rent']['remaining'], D('9900'))
        self.assertEqual(rows['Fun']['status'], 'nolimit')


class NotificationTests(Scenario):
    def test_alerts_match_the_page_statuses(self):
        call_command('send_notifications')
        slugs = sorted(Notification.objects.filter(user=self.user, slug__startswith='budget-')
                       .values_list('title', flat=True))
        self.assertEqual(slugs, ['Budget Alert: Food', 'Budget Exceeded: Fuel'])

    def test_alert_message_shows_the_limit(self):
        call_command('send_notifications')
        n = Notification.objects.get(user=self.user, title='Budget Exceeded: Fuel')
        self.assertIn('Limit: ₹2000', n.message)


class PlanLockTests(Scenario):
    tier = 'FREE'

    def setUp(self):
        super().setUp()
        for i in range(3):   # push past the Free allowance so the newest categories are locked
            Category.objects.create(user=self.user, name=f'Extra {i}', limit=D('1000'))
        cache.clear()

    def test_locked_categories_do_not_count_on_the_budget_page(self):
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        allowed = list(Category.objects.filter(user=self.user).order_by('id')[:cap])
        resp = legacy_page_ctx(self.client.get(reverse('budget')).context)
        self.assertEqual(resp['total_budget'], sum((c.limit for c in allowed if c.limit), D('0')))


def months_ago(n):
    idx = TODAY.year * 12 + TODAY.month - 1 - n
    return date(idx // 12, idx % 12 + 1, 1)


class VersionedLimitReaderTests(Scenario):
    """Readers follow the versioned limits: changing the recurring limit moves the current month
    and the future, never the past, and the Budget page and dashboard always agree."""

    def setUp(self):
        super().setUp()
        backdate_limits(self.user, months_ago(3))   # limits have applied for a few months

    def test_changing_the_default_does_not_change_past_months_on_the_page(self):
        past = months_ago(1)
        Expense.objects.create(user=self.user, date=past.replace(day=10), amount=D('600'), description='x',
                               category='Fuel', currency='₹')
        before = legacy_page_ctx(self.client.get(reverse('budget'), {'month': past.month, 'year': past.year}).context)
        writes.set_limit(self.user, self.fuel, TODAY, 3000, 'every_month')
        after = legacy_page_ctx(self.client.get(reverse('budget'), {'month': past.month, 'year': past.year}).context)
        self.assertEqual(before['total_budget'], after['total_budget'])
        self.assertEqual(after['total_budget'], D('17000'))
        now = legacy_page_ctx(self.client.get(reverse('budget')).context)
        self.assertEqual(now['total_budget'], D('18000'))

    def test_override_changes_only_its_month_everywhere(self):
        writes.set_limit(self.user, self.fuel, TODAY, 4000, 'this_month')
        page = legacy_page_ctx(self.client.get(reverse('budget')).context)
        rows = {r['category'].name: r for r in page['budget_data']}
        self.assertEqual(rows['Fuel']['limit'], D('4000'))
        self.assertEqual(rows['Fuel']['status'], 'ontrack')      # 2500 of 4000
        home = {c['name']: c for c in self.client.get(reverse('home')).context['category_limits']}
        self.assertEqual(home['Fuel']['limit'], 4000.0)
        self.assertEqual(home['Fuel']['status'], 'ontrack')
        nxt = legacy_page_ctx(self.client.get(reverse('budget'), {'month': (TODAY.month % 12) + 1,
                                                                  'year': TODAY.year + (TODAY.month == 12)}).context)
        self.assertEqual({r['category'].name: r['limit'] for r in nxt['budget_data']}['Fuel'], D('2000'))

    def test_home_period_limit_adds_up_each_months_limit(self):
        """A 3-month period whose limit changed part way is the sum of what applied each month."""
        first = months_ago(2)
        BudgetLimitVersion.objects.filter(user=self.user, category_name='fuel').update(effective_from=first)
        writes.set_limit(self.user, self.fuel, TODAY, 3000, 'every_month')   # 3000 from now
        resp = self.client.get(reverse('home'), {'time_period': 'custom',
                                                 'start_date': first.isoformat(), 'end_date': TODAY.isoformat()})
        fuel = {c['name']: c for c in resp.context['category_limits']}['Fuel']
        self.assertAlmostEqual(fuel['limit'], 2000 * 2 + 3000 * (TODAY.day / calendar.monthrange(TODAY.year, TODAY.month)[1]), places=1)

    def test_notifications_use_the_months_effective_limit(self):
        writes.set_limit(self.user, self.fuel, TODAY, 5000, 'this_month')    # Fuel is no longer over
        call_command('send_notifications')
        titles = sorted(Notification.objects.filter(user=self.user, slug__startswith='budget-')
                        .values_list('title', flat=True))
        self.assertEqual(titles, ['Budget Alert: Food'])

    def test_home_near_limit_insight_uses_the_85_percent_rule(self):
        Expense.objects.filter(user=self.user, category__in=['Food', 'Fuel']).delete()
        Expense.objects.create(user=self.user, date=TODAY, amount=D('1700'), description='x',
                               category='Fuel', currency='₹')            # exactly 85% of Fuel's 2000
        ctx = self.client.get(reverse('home')).context
        self.assertEqual({c['name']: c for c in ctx['category_limits']}['Fuel']['status'], 'limit')
        titles = [str(i.get('title')) for i in ctx['actionable_alerts']]
        self.assertIn('Approaching Limit', titles)


class WriterTests(Scenario):
    def test_category_form_edit_is_an_every_month_change_from_now(self):
        from expenses.forms import CategoryForm
        backdate_limits(self.user, months_ago(3))
        form = CategoryForm({'name': 'Food', 'icon': 'bi-tag', 'limit': '6000'}, instance=self.food)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertEqual(resolver.effective_limit(self.user, self.food, TODAY), D('6000'))
        self.assertEqual(resolver.effective_limit(self.user, self.food, months_ago(1)), D('5000'))

    def test_category_form_shows_the_months_effective_limit(self):
        from expenses.forms import CategoryForm
        writes.set_limit(self.user, self.food, TODAY, 7000, 'this_month')
        food = Category.objects.get(pk=self.food.pk)
        self.assertEqual(CategoryForm(instance=food).initial['limit'], D('7000'))

    def test_rename_keeps_the_limit_history(self):
        backdate_limits(self.user, months_ago(3))
        food = Category.objects.get(pk=self.food.pk)
        food.name = 'Groceries'
        food.save()
        self.assertEqual(resolver.effective_limit(self.user, food, months_ago(2)), D('5000'))
        self.assertIsNone(resolver.effective_limits_by_name(self.user, TODAY).get('food'))

    def test_recreating_a_deleted_category_without_a_limit_starts_unlimited(self):
        self.food.delete()
        fresh = Category.objects.create(user=self.user, name='Food')
        self.assertIsNone(resolver.effective_limit(self.user, fresh, TODAY))

    def test_a_category_form_clearing_the_limit_removes_it_going_forward(self):
        from expenses.forms import CategoryForm
        backdate_limits(self.user, months_ago(3))
        form = CategoryForm({'name': 'Food', 'icon': 'bi-tag', 'limit': ''}, instance=self.food)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertIsNone(resolver.effective_limit(self.user, self.food, TODAY))
        self.assertEqual(resolver.effective_limit(self.user, self.food, months_ago(1)), D('5000'))


class ListAndExportTests(Scenario):
    def test_category_list_filter_and_sort_use_the_effective_limit(self):
        writes.set_limit(self.user, self.rent, TODAY, None, 'this_month')    # Rent unbudgeted this month
        url = reverse('category-list')
        names = lambda qs: [c.name for c in self.client.get(url, qs).context['categories']]  # noqa: E731
        self.assertEqual(sorted(names({'budget_status': 'unbudgeted'})), ['Fun', 'Rent'])
        self.assertEqual(sorted(names({'budget_status': 'budgeted'})), ['Food', 'Fuel'])
        self.assertEqual(names({'sort': 'limit_desc'})[:2], ['Food', 'Fuel'])

    def test_export_has_the_limit_per_month(self):
        import csv
        import io
        backdate_limits(self.user, months_ago(1))
        writes.set_limit(self.user, self.fuel, TODAY, 2500, 'this_month')
        resp = self.client.post(reverse('export-data'), {'entities': ['budget_limits']})
        rows = list(csv.reader(io.StringIO(resp.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0][:4], ['Month', 'Category', 'Limit', 'Source'])
        body = {(r[0], r[1]): r[2:] for r in rows[1:]}
        self.assertEqual(body[(months_ago(1).strftime('%Y-%m'), 'Fuel')], ['2000.00', 'default'])
        self.assertEqual(body[(TODAY.strftime('%Y-%m'), 'Fuel')], ['2500.00', 'override'])
        self.assertNotIn((TODAY.strftime('%Y-%m'), 'Fun'), body)


class NotificationPlanLockTests(Scenario):
    tier = 'FREE'

    def test_alerts_only_for_plan_allowed_categories(self):
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'budget_categories')
        for i in range(cap):    # fill the allowance so the next category is locked
            Category.objects.create(user=self.user, name=f'Filler {i}')
        locked = Category.objects.create(user=self.user, name='Locked', limit=D('100'))
        Expense.objects.create(user=self.user, date=TODAY, amount=D('500'), description='x',
                               category='Locked', currency='₹')
        call_command('send_notifications')
        titles = Notification.objects.filter(user=self.user, slug__startswith='budget-').values_list('title', flat=True)
        self.assertNotIn(f'Budget Exceeded: {locked.name}', titles)


class NoDirectReadsTest(TestCase):
    def test_nothing_outside_the_resolver_reads_the_legacy_limit_column(self):
        """Category.limit is a write-only mirror: no `.limit` reads, `limit__` lookups or F('limit')
        on categories outside the resolver, the write service, the model mirror and migrations."""
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2]
        allowed = {'expenses/budget_resolver.py', 'expenses/budget_writes.py', 'expenses/models.py',
                   'expenses/forms.py'}
        pattern = re.compile(r"(?<![\w.])(?:cat|category|c|cat_obj|obj|instance|item)\.limit\b"
                             r"|\blimit__(?:gt|gte|isnull|lt)\b|F\('limit'\)|Sum\('limit'\)")
        offenders = []
        for path in list((root / 'expenses').rglob('*.py')) + list((root / 'finance_tracker').rglob('*.py')):
            rel = path.relative_to(root).as_posix()
            if '/tests/' in rel or '/migrations/' in rel or rel in allowed:
                continue
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f'{rel}:{n}: {line.strip()}')
        self.assertEqual(offenders, [])


class AuditFollowUpTests(TestCase):
    """Fixes from the budgeting audit."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        self.user = get_user_model().objects.create_user('bud_audit', 'bud_audit@example.com', 'pw12345!x')

    def test_saving_a_category_loaded_without_its_limit_does_not_touch_budgets(self):
        from expenses.models import BudgetLimitVersion, BudgetMonthOverride, Category
        cat = Category.objects.create(user=self.user, name='Travel', limit=5000)
        BudgetMonthOverride.objects.create(user=self.user, category=cat, category_name='travel',
                                           month=BudgetLimitVersion.objects.get(category_name='travel').effective_from,
                                           amount=9000)
        partial = Category.objects.only('id', 'user_id', 'name', 'icon').get(pk=cat.pk)
        partial.icon = 'bi-airplane'
        partial.save()
        self.assertEqual(BudgetMonthOverride.objects.filter(user=self.user, category_name='travel').count(), 1)

    def test_month_spend_uses_a_date_range(self):
        from datetime import date
        from decimal import Decimal

        from expenses.budgets import monthly_spend_by_category
        from expenses.models import Account, Category, Expense
        acc = Account.objects.create(user=self.user, name='A', account_type='CASH_WALLET', balance=0)
        Category.objects.get_or_create(user=self.user, name='Food')
        for d in (date(2026, 2, 28), date(2026, 3, 1), date(2026, 3, 31), date(2026, 4, 1)):
            Expense.objects.create(user=self.user, date=d, amount=Decimal('10'), category='Food', account=acc)
        spend, total = monthly_spend_by_category(self.user, 2026, 3)
        self.assertEqual(total, Decimal('20'))
        spend_dec, _ = monthly_spend_by_category(self.user, 2026, 12)
        self.assertEqual(spend_dec, {})

    def test_rebalance_checks_the_plan_once(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.utils import timezone

        from expenses import budget_writes as writes
        from expenses.models import Category
        profile = self.user.profile
        profile.tier, profile.is_lifetime = 'PRO', True
        profile.save()
        a = Category.objects.create(user=self.user, name='A1', limit=1000)
        b = Category.objects.create(user=self.user, name='B1')
        c = Category.objects.create(user=self.user, name='C1')
        month = timezone.localdate().replace(day=1)
        with CaptureQueriesContext(connection) as many:
            writes.rebalance(self.user, month, 'this_month', [
                {'from_category': a, 'to_category': b, 'amount': 100},
                {'from_category': a, 'to_category': c, 'amount': 100},
            ])
        category_lookups = [q for q in many.captured_queries if 'FROM "expenses_category"' in q['sql'] and 'ORDER BY' in q['sql']]
        self.assertLessEqual(len(category_lookups), 1)


class SpendByMonthsPostgresSafeTests(TestCase):
    """The multi-month spend query once UNIONed expenses with capital events, putting the month column in
    a different position in each half; Postgres refused it ("UNION types timestamp with time zone and
    character varying cannot be matched") while SQLite let it through."""

    def test_expenses_and_capital_events_over_several_months(self):
        from datetime import date
        from decimal import Decimal

        from django.contrib.auth import get_user_model
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from expenses.budgets import monthly_spend_by_category_months
        from expenses.models import Account, CapitalEvent, Category, Expense
        user = get_user_model().objects.create_user('pg_safe', 'pg_safe@example.com', 'pw12345!x')
        acc = Account.objects.create(user=user, name='A', account_type='CASH_WALLET', balance=0)
        Category.objects.get_or_create(user=user, name='Food')
        Expense.objects.create(user=user, date=date(2026, 8, 5), amount=Decimal('100'), category='Food', account=acc)
        Expense.objects.create(user=user, date=date(2026, 9, 5), amount=Decimal('40'), category='Food', account=acc)
        CapitalEvent.objects.create(user=user, account=acc, date=date(2026, 9, 9), amount=Decimal('500'),
                                    subtype='GADGET', category='Food', exclude_from_budget=False)
        months = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
        with CaptureQueriesContext(connection) as ctx:
            spend = monthly_spend_by_category_months(user, months)
        self.assertEqual(spend[months[0]]['food'], Decimal('100'))
        self.assertEqual(spend[months[1]]['food'], Decimal('540'))
        self.assertEqual(spend[months[2]], {})
        self.assertFalse([q for q in ctx.captured_queries if ' UNION ' in q['sql']])
