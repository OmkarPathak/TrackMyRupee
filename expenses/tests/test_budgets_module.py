"""Behavioural tests for Budgets: the shared rules (expenses/budgets.py), category limits and the
category screens, the Budget page and its pacing maths, and the budget alerts."""

import calendar
import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.management import call_command
from django.utils import translation
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.budget_page import PACE_BAND
from expenses.budgets import (
    BUDGET_WARNING_PERCENT,
    budget_status,
    bulk_monthly_spend,
    has_limit,
    monthly_spend_by_category,
    normalize_category,
    percent_used,
)
from expenses.forms import CategoryForm
from expenses.models import (
    CapitalEvent,
    Category,
    Expense,
    ExpenseKeywordHint,
    FXRate,
    Notification,
    RecurringTransaction,
    UserProfile,
)
from expenses.tests.budget_helpers import legacy_page_ctx
from finance_tracker.plans import get_limit

D = Decimal


def today():
    return timezone.localdate()


def seed_fx():
    for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': D(rate), 'source': 'test'})
    cache.clear()


class BudgetBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('bud-user', self.tier)
        self.client.force_login(self.user)
        Category.objects.filter(user=self.user).delete()     # start from a clean slate
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        Category.objects.filter(user=user).delete()          # drop the default categories a new user gets
        return user

    def cat(self, name, limit=None, user=None):
        return Category.objects.create(user=user or self.user, name=name, limit=None if limit is None else D(str(limit)))

    def spend(self, category, amount, when=None, user=None, **kw):
        return Expense.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                      description='x', category=category, currency='₹', **kw)

    def event(self, amount, subtype='large_purchase', budget=True, when=None, user=None, **kw):
        return CapitalEvent.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                           subtype=subtype, currency='₹', exclude_from_budget=not budget,
                                           exclude_from_averages=True, **kw)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Shared rules
# ---------------------------------------------------------------------------
class TestBudgetRules(TestCase):
    def test_threshold_is_85(self):
        self.assertEqual(BUDGET_WARNING_PERCENT, 85)

    def test_normalize_and_has_limit(self):
        self.assertEqual(normalize_category('  Food  '), 'food')
        self.assertEqual(normalize_category(None), '')
        self.assertFalse(has_limit(None))
        self.assertFalse(has_limit(D('0')))
        self.assertFalse(has_limit(D('-5')))
        self.assertTrue(has_limit(D('0.01')))

    def test_percent_used(self):
        self.assertEqual(percent_used(D('250'), D('1000')), 25.0)
        self.assertEqual(percent_used(D('2100'), D('1000')), 210.0)
        self.assertEqual(percent_used(D('50'), None), 0.0)
        self.assertEqual(percent_used(D('50'), D('0')), 0.0)

    def test_status_boundaries(self):
        limit = D('1000')
        cases = [('0', 'ontrack'), ('849.99', 'ontrack'), ('850', 'limit'), ('999.99', 'limit'),
                 ('1000', 'limit'), ('1000.01', 'over'), ('5000', 'over')]
        for spent, expected in cases:
            self.assertEqual(budget_status(D(spent), limit), expected, spent)

    def test_status_without_a_usable_limit(self):
        for limit in (None, D('0'), D('-10')):
            self.assertEqual(budget_status(D('500'), limit), 'nolimit')

    def test_status_accepts_floats_and_strings_and_none(self):
        self.assertEqual(budget_status(None, D('100')), 'ontrack')
        self.assertEqual(budget_status('90', '100'), 'limit')
        self.assertEqual(budget_status(101.5, 100), 'over')


class TestMonthlySpend(BudgetBase):
    def test_sums_per_category_case_insensitively(self):
        self.spend('Food', 100)
        self.spend('food', 50)
        self.spend(' FOOD ', 25)       # stripped on save
        self.spend('Rent', 700)
        spend, total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend, {'food': D('175.00'), 'rent': D('700.00')})
        self.assertEqual(total, D('875.00'))

    def test_only_the_asked_month_and_user(self):
        self.spend('Food', 100)
        self.spend('Food', 999, when=today().replace(day=1) - timedelta(days=1))
        other = self.make_user('other-bud-spend')
        self.spend('Food', 5000, user=other)
        spend, total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual((spend, total), ({'food': D('100.00')}, D('100.00')))

    def test_capital_events_count_only_when_not_excluded_from_budget(self):
        self.event(2000, budget=True)
        self.event(9000, budget=False)
        spend, total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend, {'large purchase': D('2000.00')})
        self.assertEqual(total, D('2000.00'))

    def test_a_capital_event_joins_a_category_named_like_its_subtype(self):
        self.spend('Large Purchase', 500)
        self.event(2000)
        spend, _ = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend, {'large purchase': D('2500.00')})

    def test_uses_base_amounts(self):
        seed_fx()
        Expense.objects.create(user=self.user, date=today(), amount=D('10'), description='x', category='Food', currency='$')
        spend, _ = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(spend['food'], D('800.00'))

    def test_empty_month(self):
        self.assertEqual(monthly_spend_by_category(self.user, 2001, 1), ({}, D('0')))

    def test_bulk_matches_the_per_user_function(self):
        other = self.make_user('other-bud-bulk')
        self.spend('Food', 100)
        self.spend('food', 20)
        self.event(300)
        self.spend('Food', 7, user=other)
        bulk = bulk_monthly_spend(today().year, today().month)
        mine, _ = monthly_spend_by_category(self.user, today().year, today().month)
        theirs, _ = monthly_spend_by_category(other, today().year, today().month)
        self.assertEqual({k[1]: v for k, v in bulk.items() if k[0] == self.user.id}, mine)
        self.assertEqual({k[1]: v for k, v in bulk.items() if k[0] == other.id}, theirs)

    def test_query_count_is_constant(self):
        for i in range(5):
            self.spend(f'C{i}', 10)
        with self.assertNumQueries(1):      # expenses and capital events come back as one UNION
            monthly_spend_by_category(self.user, today().year, today().month)
        with self.assertNumQueries(3):      # who has a salary day, then expenses and events, whatever the number of users
            bulk_monthly_spend(today().year, today().month)


# ---------------------------------------------------------------------------
# Category form
# ---------------------------------------------------------------------------
class TestCategoryForm(BudgetBase):
    def form(self, instance=None, **data):
        payload = {'name': 'Food', 'icon': 'bi-tag', 'limit': '5000'}
        payload.update(data)
        form = CategoryForm(payload, instance=instance or Category(user=self.user))
        return form

    def test_valid(self):
        form = self.form()
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertEqual(form.cleaned_data['limit'], D('5000'))

    def test_name_is_required_and_trimmed(self):
        self.assertIn('name', self.form(name='').errors)
        self.assertIn('name', self.form(name='   ').errors)
        form = self.form(name='  Food  ')
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['name'], 'Food')

    def test_name_must_be_unique_ignoring_case(self):
        self.cat('Food')
        self.assertIn('name', self.form(name='food').errors)
        self.assertIn('name', self.form(name='FOOD ').errors)

    def test_names_are_unique_per_user_only(self):
        other = self.make_user('other-cat-form')
        self.cat('Food', user=other)
        self.assertTrue(self.form(name='Food').is_valid())

    def test_editing_without_renaming_is_not_a_clash(self):
        existing = self.cat('Food')
        self.assertTrue(self.form(instance=existing, name='Food', limit='100').is_valid())
        self.assertTrue(self.form(instance=existing, name='FOOD').is_valid())   # a case-only rename is allowed

    def test_limit_is_optional_and_blank_means_none(self):
        form = self.form(limit='')
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['limit'])

    def test_negative_limits_are_rejected(self):
        for bad in ('-1', '-0.01', '-5000'):
            self.assertIn('limit', self.form(limit=bad).errors, bad)

    def test_a_zero_limit_means_no_limit(self):
        form = self.form(limit='0')
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data['limit'])

    def test_limit_digits_and_garbage(self):
        self.assertIn('limit', self.form(limit='abc').errors)
        self.assertIn('limit', self.form(limit='1.234').errors)
        self.assertIn('limit', self.form(limit='1' * 14).errors)
        self.assertTrue(self.form(limit='0.01').is_valid())


# ---------------------------------------------------------------------------
# Category screens
# ---------------------------------------------------------------------------
class TestCategoryScreens(BudgetBase):
    def payload(self, **overrides):
        data = {'name': 'Groceries', 'icon': 'bi-tag', 'limit': '8000'}
        data.update(overrides)
        return data

    # list
    def test_list_is_login_only_and_isolated(self):
        self.cat('Mine', 100)
        other = self.make_user('other-cat-list')
        self.cat('Secret', user=other)
        response = self.client.get(reverse('category-list'))
        self.assertEqual([c.name for c in response.context['categories']], ['Mine'])
        self.assertNotContains(response, 'Secret')
        self.client.logout()
        self.assertEqual(self.client.get(reverse('category-list')).status_code, 302)

    def test_list_filter_search_and_sort(self):
        self.cat('Alpha', 100)
        self.cat('Beta')
        self.cat('Gamma', 900)
        names = lambda **p: [c.name for c in self.client.get(reverse('category-list'), p).context['categories']]
        self.assertEqual(names(budget_status='budgeted'), ['Alpha', 'Gamma'])
        self.assertEqual(names(budget_status='unbudgeted'), ['Beta'])
        self.assertEqual(names(search='ga'), ['Gamma'])
        self.assertEqual(names(sort='name_desc'), ['Gamma', 'Beta', 'Alpha'])
        self.assertEqual(names(sort='limit_desc'), ['Gamma', 'Alpha', 'Beta'])
        self.assertEqual(names(sort='limit_asc'), ['Alpha', 'Gamma', 'Beta'])

    def test_out_of_range_page_redirects_to_the_last_page(self):
        for i in range(12):
            self.cat(f'C{i:02d}')
        response = self.client.get(reverse('category-list'), {'page': 9})
        self.assertRedirects(response, reverse('category-list') + '?page=2', fetch_redirect_response=False)
        self.client.get(reverse('category-list'))
        Category.objects.filter(name__in=[f'C{i:02d}' for i in range(2, 12)]).delete()
        response = self.client.get(reverse('category-list'), {'page': 5})
        self.assertRedirects(response, reverse('category-list'), fetch_redirect_response=False)

    def test_nudge_context_on_a_limited_plan(self):
        free = self.make_user('free-cat-list', tier='FREE')
        self.client.force_login(free)
        limit = get_limit('FREE', 'budget_categories')
        for i in range(limit):
            self.cat(f'F{i}', user=free)
        ctx = self.client.get(reverse('category-list')).context
        self.assertEqual((ctx['reached_limit'], ctx['nudge_current'], ctx['nudge_limit'], ctx['nudge_upgrade_tier']),
                         (True, limit, limit, 'PLUS'))

    def test_paid_plan_has_no_nudge(self):
        self.cat('A')
        ctx = self.client.get(reverse('category-list')).context
        self.assertFalse(ctx['reached_limit'])
        self.assertNotIn('nudge_limit', ctx)

    # create
    def test_create_with_a_limit(self):
        response = self.client.post(reverse('category-create'), self.payload())
        self.assertRedirects(response, reverse('category-list'))
        c = Category.objects.get(user=self.user)
        self.assertEqual((c.name, c.limit), ('Groceries', D('8000.00')))
        self.assertIn('Category created successfully!', self.messages(response))

    def test_create_without_a_limit(self):
        self.client.post(reverse('category-create'), self.payload(limit=''))
        self.assertIsNone(Category.objects.get().limit)

    def test_create_rejects_bad_input(self):
        self.cat('Groceries')
        for override in ({'name': ''}, {'name': 'groceries'}, {'limit': '-5'}, {'limit': 'abc'}):
            with self.subTest(override=override):
                self.assertEqual(self.client.post(reverse('category-create'), self.payload(**override)).status_code, 200)
        self.assertEqual(Category.objects.count(), 1)

    def test_a_limit_of_zero_is_stored_as_no_limit(self):
        self.client.post(reverse('category-create'), self.payload(limit='0'))
        self.assertIsNone(Category.objects.get().limit)

    def test_create_is_blocked_at_the_plan_limit(self):
        free = self.make_user('free-cat-create', tier='FREE')
        self.client.force_login(free)
        for i in range(get_limit('FREE', 'budget_categories')):
            self.cat(f'F{i}', user=free)
        before = Category.objects.filter(user=free).count()
        response = self.client.post(reverse('category-create'), self.payload())
        self.assertRedirects(response, reverse('pricing'), fetch_redirect_response=False)
        self.assertEqual(Category.objects.filter(user=free).count(), before)

    # ajax create (the "+ Create category" button in the expense composer)
    def ajax(self, body, method='post'):
        url = reverse('category-create-ajax')
        if method == 'get':
            return self.client.get(url)
        raw = body if isinstance(body, str) else json.dumps(body)
        return self.client.post(url, raw, content_type='application/json')

    def test_ajax_create(self):
        data = self.ajax({'name': '  Pets '}).json()
        self.assertTrue(data['success'])
        self.assertEqual(Category.objects.get().name, 'Pets')
        self.assertEqual(data['id'], Category.objects.get().id)

    def test_ajax_validation(self):
        self.cat('Pets')
        self.assertFalse(self.ajax({'name': ''}).json()['success'])
        self.assertFalse(self.ajax({'name': '   '}).json()['success'])
        self.assertIn('already exists', self.ajax({'name': 'pets'}).json()['error'])
        self.assertEqual(self.ajax('{bad json').status_code, 400)
        self.assertEqual(self.ajax({}, method='get').status_code, 405)
        self.assertEqual(Category.objects.count(), 1)

    def test_ajax_respects_the_plan_limit(self):
        free = self.make_user('free-cat-ajax', tier='FREE')
        self.client.force_login(free)
        for i in range(get_limit('FREE', 'budget_categories')):
            self.cat(f'F{i}', user=free)
        response = self.ajax({'name': 'One more'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Category.objects.filter(name='One more').exists())

    # edit
    def edit(self, c, **overrides):
        data = {'name': c.name, 'icon': c.icon, 'limit': str(c.limit or '')}
        data.update(overrides)
        return self.client.post(reverse('category-edit', kwargs={'pk': c.pk}), data)

    def test_edit_limit(self):
        c = self.cat('Food', 1000)
        self.assertRedirects(self.edit(c, limit='2500'), reverse('category-list'))
        c.refresh_from_db()
        self.assertEqual(c.limit, D('2500.00'))

    def test_clearing_a_limit(self):
        c = self.cat('Food', 1000)
        self.edit(c, limit='')
        c.refresh_from_db()
        self.assertIsNone(c.limit)

    def test_negative_limit_on_edit_is_rejected(self):
        c = self.cat('Food', 1000)
        self.assertEqual(self.edit(c, limit='-1').status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.limit, D('1000.00'))

    def test_rename_cascades_to_expenses_schedules_and_learned_hints(self):
        c = self.cat('Food', 1000)
        e = self.spend('Food', 100)
        rt = RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('5'), currency='₹',
                                                 description='Milk', frequency='MONTHLY', start_date=today(), category='Food')
        hint = ExpenseKeywordHint.objects.create(user=self.user, keyword='swiggy', category='Food')
        other = self.make_user('other-cat-rename')
        theirs = self.spend('Food', 7, user=other)
        self.edit(c, name='Dining')
        for obj in (e, rt, hint):
            obj.refresh_from_db()
            self.assertEqual(obj.category, 'Dining', type(obj).__name__)
        theirs.refresh_from_db()
        self.assertEqual(theirs.category, 'Food')

    def test_renaming_into_an_existing_name_is_refused(self):
        self.cat('Rent')
        c = self.cat('Food')
        self.assertEqual(self.edit(c, name='rent').status_code, 200)
        c.refresh_from_db()
        self.assertEqual(c.name, 'Food')

    def test_locked_categories_cannot_be_edited(self):
        free = self.make_user('free-cat-edit', tier='FREE')
        self.client.force_login(free)
        limit = get_limit('FREE', 'budget_categories')
        made = [self.cat(f'F{i}', user=free) for i in range(limit + 1)]
        self.assertEqual(self.client.get(reverse('category-edit', kwargs={'pk': made[0].pk})).status_code, 200)
        response = self.client.get(reverse('category-edit', kwargs={'pk': made[-1].pk}))
        self.assertRedirects(response, reverse('category-list'), fetch_redirect_response=False)

    def test_edit_next_param_only_same_site(self):
        c = self.cat('Food')
        self.assertEqual(self.edit(c, next='/budget/').url, '/budget/')
        self.assertEqual(self.edit(c, next='https://evil.example/').url, reverse('category-list'))

    def test_other_users_category_is_404(self):
        other = self.make_user('other-cat-edit')
        theirs = self.cat('Theirs', 5, user=other)
        self.assertEqual(self.client.get(reverse('category-edit', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.edit(theirs, limit='1').status_code, 404)
        self.assertEqual(self.client.post(reverse('category-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.limit, D('5.00'))

    # delete
    def test_delete_keeps_the_expenses_but_they_become_unbudgeted(self):
        c = self.cat('Food', 1000)
        e = self.spend('Food', 100)
        response = self.client.post(reverse('category-delete', kwargs={'pk': c.pk}))
        self.assertRedirects(response, reverse('category-list'))
        self.assertFalse(Category.objects.filter(pk=c.pk).exists())
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())
        self.assertIn('Category deleted successfully.', self.messages(response))
        ctx = legacy_page_ctx(self.client.get(reverse('budget')).context)
        self.assertEqual((ctx['total_spent'], ctx['budgeted_spent'], ctx['unbudgeted_spent']), (D('100'), D('0'), D('100')))

    def test_bulk_delete(self):
        a, b, keep = self.cat('A'), self.cat('B'), self.cat('Keep')
        other = self.make_user('other-cat-bulk')
        theirs = self.cat('Theirs', user=other)
        cache.set(f'filter_categories:{self.user.id}', ['stale'])
        response = self.client.post(reverse('category-bulk-delete'), {'category_ids': [a.id, b.id, theirs.id]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Category.objects.filter(user=self.user)), [keep])
        self.assertTrue(Category.objects.filter(pk=theirs.pk).exists())
        self.assertIsNone(cache.get(f'filter_categories:{self.user.id}'))

    def test_bulk_delete_with_nothing_or_junk_is_a_noop(self):
        keep = self.cat('Keep')
        for ids in ([], ['abc'], [''], ['99999999']):
            with self.subTest(ids=ids):
                self.assertEqual(self.client.post(reverse('category-bulk-delete'), {'category_ids': ids}).status_code, 302)
        self.assertTrue(Category.objects.filter(pk=keep.pk).exists())

    def test_login_required(self):
        c = self.cat('Food')
        self.client.logout()
        for name, kwargs in (('category-create', {}), ('category-edit', {'pk': c.pk}), ('category-delete', {'pk': c.pk})):
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 302, name)
        self.assertEqual(self.client.post(reverse('category-bulk-delete'), {'category_ids': [c.id]}).status_code, 302)
        self.assertTrue(Category.objects.filter(pk=c.pk).exists())


# ---------------------------------------------------------------------------
# Budget page
# ---------------------------------------------------------------------------
class TestBudgetPage(BudgetBase):
    url = reverse('budget')

    def ctx(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return legacy_page_ctx(response.context)

    def row(self, ctx, name):
        return next(d for d in ctx['budget_data'] if d['category'].name == name)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_empty_state(self):
        ctx = self.ctx()
        self.assertEqual((ctx['budget_data'], ctx['total_budget'], ctx['total_spent'], ctx['pace_state']), ([], D('0'), D('0'), None))

    def test_every_status_and_its_numbers(self):
        self.cat('Ok', 1000)
        self.cat('Warn', 1000)
        self.cat('Exact', 1000)
        self.cat('Over', 1000)
        self.cat('Free')
        self.spend('Ok', 400)
        self.spend('Warn', 850)
        self.spend('Exact', 1000)
        self.spend('Over', 2100)
        self.spend('Free', 77)
        ctx = self.ctx()
        self.assertEqual([self.row(ctx, n)['status'] for n in ('Ok', 'Warn', 'Exact', 'Over', 'Free')],
                         ['ontrack', 'limit', 'limit', 'over', 'nolimit'])
        self.assertEqual((ctx['over_budget_count'], ctx['at_limit_count'], ctx['on_track_count'], ctx['no_limit_count']), (1, 2, 1, 1))
        over = self.row(ctx, 'Over')
        self.assertEqual((over['percentage'], over['actual_percentage'], over['over_budget'], over['remaining'], over['left_percentage']),
                         (100, 210.0, D('1100'), D('0'), 0.0))
        ok = self.row(ctx, 'Ok')
        self.assertEqual((ok['percentage'], ok['remaining'], ok['over_budget'], ok['left_percentage']), (40.0, D('600'), D('0'), 60.0))
        exact = self.row(ctx, 'Exact')
        self.assertEqual((exact['remaining'], exact['over_budget']), (D('0'), D('0')))
        free = self.row(ctx, 'Free')
        self.assertEqual((free['limit'], free['percentage'], free['remaining'], free['left_percentage']), (None, 0, D('0'), 0))

    def test_needs_attention_lists_over_and_at_limit_but_not_the_rest(self):
        self.cat('Ok', 1000)
        self.cat('Warn', 1000)
        self.cat('Over', 1000)
        self.cat('Free')
        for name, amount in (('Ok', 100), ('Warn', 900), ('Over', 1200)):
            self.spend(name, amount)
        ctx = self.ctx()
        self.assertEqual([d['category'].name for d in ctx['needs_attention']], ['Over', 'Warn'])
        self.assertEqual([d['category'].name for d in ctx['on_track']], ['Ok'])
        self.assertEqual([d['category'].name for d in ctx['no_limit']], ['Free'])

    def test_totals(self):
        self.cat('A', 1000)
        self.cat('B', 2000)
        self.cat('Free')
        self.spend('A', 400)
        self.spend('B', 2500)
        self.spend('Free', 100)
        self.spend('Uncategorised thing', 50)           # not a category at all
        ctx = self.ctx()
        self.assertEqual(ctx['total_budget'], D('3000'))
        self.assertEqual(ctx['budgeted_spent'], D('2900'))        # only categories that have a limit
        self.assertEqual(ctx['total_spent'], D('3050'))
        self.assertEqual(ctx['unbudgeted_spent'], D('150'))       # no-limit category + stray spend
        self.assertEqual(ctx['total_remaining'], D('100'))        # B is over its own limit, but the total is not
        self.assertEqual(ctx['over_budget_amount'], D('0'))
        self.assertAlmostEqual(ctx['actual_total_percentage'], 96.7, places=1)   # the payload rounds to 0.1
        self.assertEqual(ctx['total_percentage'], ctx['actual_total_percentage'])

    def test_total_over_budget_and_percentage_cap(self):
        self.cat('A', 1000)
        self.spend('A', 1500)
        ctx = self.ctx()
        self.assertEqual((ctx['total_remaining'], ctx['over_budget_amount'], ctx['total_percentage'], ctx['actual_total_percentage']),
                         (D('0'), D('500'), 100.0, 150.0))

    def test_total_remaining_when_under(self):
        self.cat('A', 1000)
        self.spend('A', 300)
        self.assertEqual(self.ctx()['total_remaining'], D('700'))

    def test_spend_matches_categories_case_insensitively(self):
        """Regression: 'food' did not count towards the 'Food' budget."""
        self.cat('Food', 1000)
        self.spend('Food', 200)
        self.spend('food', 300)
        ctx = self.ctx()
        self.assertEqual(self.row(ctx, 'Food')['spent'], D('500'))
        self.assertEqual(ctx['unbudgeted_spent'], D('0'))

    def test_capital_event_counts_when_it_is_not_excluded_from_budget(self):
        """Regression: the Exclude from Budget switch had no effect on the Budget page."""
        self.cat('Large Purchase', 5000)
        self.event(2000, budget=True)
        self.event(9000, budget=False)
        ctx = self.ctx()
        self.assertEqual(self.row(ctx, 'Large Purchase')['spent'], D('2000'))
        self.assertEqual(ctx['total_spent'], D('2000'))

    def test_capital_event_without_a_matching_category_is_unbudgeted_spend(self):
        self.cat('Food', 1000)
        self.event(2000, budget=True)
        ctx = self.ctx()
        self.assertEqual((ctx['budgeted_spent'], ctx['unbudgeted_spent'], ctx['total_spent']), (D('0'), D('2000'), D('2000')))

    def test_foreign_currency_spend_counts_in_base_currency(self):
        seed_fx()
        self.cat('SaaS', 1000)
        Expense.objects.create(user=self.user, date=today(), amount=D('10'), description='x', category='SaaS', currency='$')
        self.assertEqual(self.row(self.ctx(), 'SaaS')['spent'], D('800.00'))

    def test_month_and_year_selection(self):
        self.cat('Food', 1000)
        last = today().replace(day=1) - timedelta(days=3)
        self.spend('Food', 111, when=last)
        self.spend('Food', 222)
        self.assertEqual(self.row(self.ctx(), 'Food')['spent'], D('222'))
        past = self.ctx(month=last.month, year=last.year)
        self.assertEqual(self.row(past, 'Food')['spent'], D('111'))
        self.assertEqual((past['current_month'], past['current_year'], past['month_name']),
                         (last.month, last.year, calendar.month_name[last.month]))

    def test_invalid_month_and_year_fall_back_to_now(self):
        for params in ({'month': '13'}, {'month': '0'}, {'month': 'x'}, {'year': '1900'}, {'year': 'abc'}, {'year': '9999'}):
            with self.subTest(params=params):
                ctx = self.ctx(**params)
                self.assertTrue(1 <= ctx['current_month'] <= 12)
                self.assertTrue(2000 <= ctx['current_year'] <= today().year + 5)

    def test_sorting(self):
        self.cat('Bravo', 1000)
        self.cat('Alpha', 5000)
        self.cat('Charlie')
        self.spend('Bravo', 1500)       # over
        self.spend('Alpha', 100)
        self.spend('Charlie', 900)
        names = lambda sort: [d['category'].name for d in self.ctx(sort=sort)['budget_data']]
        self.assertEqual(names('urgent'), ['Bravo', 'Alpha', 'Charlie'])
        self.assertEqual(names('name'), ['Alpha', 'Bravo', 'Charlie'])
        self.assertEqual(names('limit'), ['Alpha', 'Bravo', 'Charlie'])
        self.assertEqual(names('spent'), ['Bravo', 'Charlie', 'Alpha'])

    def test_urgent_sort_orders_by_status_then_percentage(self):
        self.cat('Over-small', 1000)
        self.cat('Over-big', 1000)
        self.cat('Warn', 1000)
        self.spend('Over-small', 1100)
        self.spend('Over-big', 3000)
        self.spend('Warn', 900)
        self.assertEqual([d['category'].name for d in self.ctx()['budget_data']], ['Over-big', 'Over-small', 'Warn'])

    def test_users_are_isolated(self):
        other = self.make_user('other-bud-page')
        self.cat('Food', 10, user=other)
        self.spend('Food', 999, user=other)
        self.cat('Mine', 1000)
        ctx = self.ctx()
        self.assertEqual([d['category'].name for d in ctx['budget_data']], ['Mine'])
        self.assertEqual(ctx['total_spent'], D('0'))

    def test_plan_locked_categories_are_not_part_of_the_budget(self):
        free = self.make_user('free-bud', tier='FREE')
        self.client.force_login(free)
        limit = get_limit('FREE', 'budget_categories')
        made = [self.cat(f'F{i}', 100, user=free) for i in range(limit + 2)]
        Expense.objects.create(user=free, date=today(), amount=D('50'), description='x', category=made[-1].name, currency='₹')
        ctx = self.ctx()
        self.assertEqual([d['category'].name for d in ctx['budget_data']], sorted(c.name for c in made[:limit]))
        self.assertEqual(ctx['total_budget'], D('100') * limit)
        self.assertEqual((ctx['budgeted_spent'], ctx['unbudgeted_spent']), (D('0'), D('50')))

    def test_paid_plans_show_every_category(self):
        for i in range(20):
            self.cat(f'P{i}', 100)
        self.assertEqual(len(self.ctx()['budget_data']), 20)

    def test_context_carries_the_threshold_and_the_unified_filter_toolbar(self):
        response = self.client.get(self.url)
        self.assertEqual(response.context['warning_percent'], BUDGET_WARNING_PERCENT)
        self.assertEqual(response.context['filter_config'].page_key, 'budget')
        self.assertContains(response, 'tmr-toolbar-container')
        self.assertContains(response, 'data-page-key="budget"')

    def test_total_bar_turns_amber_at_the_warning_level_and_red_when_over(self):
        self.cat('A', 1000)
        self.spend('A', 900)
        page = self.client.get(self.url)
        self.assertContains(page, 'bdx-chip--limit')
        self.assertNotContains(page, 'bdx-pace-fill is-over')
        self.spend('A', 200)
        self.assertContains(self.client.get(self.url), 'bdx-pace-fill is-over')

    def test_htmx_partial(self):
        full = [t.name for t in self.client.get(self.url).templates]
        partial = [t.name for t in self.client.get(self.url, HTTP_HX_REQUEST='true').templates]
        self.assertIn('expenses/budget_dashboard.html', full)
        self.assertIn('expenses/partials/_budget_dashboard_content.html', partial)

    def test_new_spend_shows_up_immediately_despite_the_cache(self):
        self.cat('Food', 1000)
        self.assertEqual(self.row(self.ctx(), 'Food')['spent'], D('0'))
        self.spend('Food', 300)
        self.assertEqual(self.row(self.ctx(), 'Food')['spent'], D('300'))


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------
class TestBudgetAlerts(BudgetBase):
    def run_alerts(self):
        call_command('send_notifications')
        return list(Notification.objects.filter(user=self.user, slug__startswith='budget-'))

    def test_nothing_below_the_warning_level(self):
        self.cat('Food', 1000)
        self.spend('Food', 849)
        self.assertEqual(self.run_alerts(), [])

    def test_warning_at_the_same_level_the_page_turns_amber(self):
        self.cat('Food', 1000)
        self.spend('Food', 850)
        alerts = self.run_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].title, 'Budget Alert: Food')
        self.assertIn('85%', alerts[0].message)
        self.assertTrue(alerts[0].slug.startswith('budget-warning-'))
        self.assertEqual(alerts[0].notification_type, 'ANALYTICS')
        self.assertEqual(alerts[0].link, '/expenses/?category=Food')

    def test_exactly_at_the_limit_is_a_warning_not_yet_exceeded(self):
        self.cat('Food', 1000)
        self.spend('Food', 1000)
        alerts = self.run_alerts()
        self.assertEqual([a.title for a in alerts], ['Budget Alert: Food'])
        self.assertIn('100%', alerts[0].message)

    def test_exceeded_only_when_spend_is_over_the_limit(self):
        self.cat('Food', 1000)
        self.spend('Food', 1000.01)
        alerts = self.run_alerts()
        self.assertEqual([a.title for a in alerts], ['Budget Exceeded: Food'])
        self.assertIn('exceeded', alerts[0].message)

    def test_the_link_to_the_category_survives_names_with_symbols(self):
        self.cat('R&D #1', 1000)
        self.spend('R&D #1', 900)
        self.assertEqual(self.run_alerts()[0].link, '/expenses/?category=R%26D%20%231')

    def test_alerts_are_sent_once_per_month_per_state(self):
        self.cat('Food', 1000)
        self.spend('Food', 900)
        self.run_alerts()
        self.run_alerts()
        self.assertEqual(len(Notification.objects.filter(user=self.user, slug__startswith='budget-warning')), 1)
        self.spend('Food', 500)           # now over
        self.run_alerts()
        self.assertEqual(len(Notification.objects.filter(user=self.user, slug__startswith='budget-')), 2)

    def test_alert_attribution_matches_the_budget_page(self):
        self.cat('Food', 1000)
        self.spend('food', 900)                                   # different case
        self.assertEqual(len(self.run_alerts()), 1)

    def test_budget_counted_capital_events_trigger_alerts_too(self):
        self.cat('Large Purchase', 1000)
        self.event(1200, budget=True)
        self.assertEqual([a.title for a in self.run_alerts()], ['Budget Exceeded: Large Purchase'])

    def test_capital_events_excluded_from_budget_do_not(self):
        self.cat('Large Purchase', 1000)
        self.event(1200, budget=False)
        self.assertEqual(self.run_alerts(), [])

    def test_a_capital_event_is_attributed_the_same_whatever_language_is_active(self):
        # A request for a Hindi or Marathi user leaves its language active on the thread, and a later test (or
        # the next request) must not see a different category name because of it: the event's subtype label is
        # translated, so "Large Purchase" would become a Hindi label that no category matches.
        self.cat('Large Purchase', 1000)
        self.event(1200, budget=True)
        for language in ('hi', 'mr'):
            Notification.objects.filter(user=self.user).delete()
            with translation.override(language):
                titles = [a.title for a in self.run_alerts()]
            self.assertEqual(titles, ['Budget Exceeded: Large Purchase'], language)

    def test_the_budget_page_attributes_a_capital_event_the_same_in_every_language(self):
        self.event(1200, budget=True)
        results = {}
        for language in ('en', 'hi', 'mr'):
            with translation.override(language):
                spend, _total = monthly_spend_by_category(self.user, today().year, today().month)
            results[language] = spend
        self.assertEqual(results['hi'], results['en'])
        self.assertEqual(results['mr'], results['en'])
        self.assertIn('large purchase', results['en'])

    def test_categories_without_a_limit_never_alert(self):
        self.cat('Food')
        self.spend('Food', 999999)
        self.assertEqual(self.run_alerts(), [])

    def test_only_this_months_spend_counts(self):
        self.cat('Food', 1000)
        self.spend('Food', 5000, when=today().replace(day=1) - timedelta(days=2))
        self.assertEqual(self.run_alerts(), [])

    def test_plan_locked_categories_do_not_alert(self):
        free = self.make_user('free-bud-alert', tier='FREE')
        limit = get_limit('FREE', 'budget_categories')
        made = [self.cat(f'F{i}', 100, user=free) for i in range(limit + 1)]
        Expense.objects.create(user=free, date=today(), amount=D('500'), description='x', category=made[-1].name, currency='₹')
        call_command('send_notifications')
        self.assertEqual(Notification.objects.filter(user=free, slug__startswith='budget-').count(), 0)

    def test_users_only_get_their_own_alerts(self):
        other = self.make_user('other-bud-alert')
        self.cat('Food', 100, user=other)
        self.spend('Food', 500, user=other)
        self.cat('Food', 1000)
        self.spend('Food', 10)
        call_command('send_notifications')
        self.assertEqual(Notification.objects.filter(user=self.user, slug__startswith='budget-').count(), 0)
        self.assertEqual(Notification.objects.filter(user=other, slug__startswith='budget-').count(), 1)


class TestBudgetDocs(BudgetBase):
    def test_numbers_quoted_in_the_guide(self):
        self.assertEqual(BUDGET_WARNING_PERCENT, 85)
        self.assertEqual(PACE_BAND, D('0.05'))
        self.assertEqual((get_limit('FREE', 'budget_categories'), get_limit('PLUS', 'budget_categories'),
                          get_limit('PRO', 'budget_categories')), (5, 15, -1))


class TestHomeBudgetBarsShareTheRule(BudgetBase):
    """The dashboard's category bars used their own 80%/90% scale; they now follow the budget rule."""

    def setUp(self):
        super().setUp()
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.save()

    def statuses(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return {c['name']: c['status'] for c in response.context['category_limits']}

    def test_status_per_category(self):
        for name, limit, spent in (('Ok', 1000, 400), ('Warn', 1000, 850), ('Over', 1000, 1001), ('Free', None, 50)):
            self.cat(name, limit)
            self.spend(name, spent)
        self.assertEqual(self.statuses(), {'Ok': 'ontrack', 'Warn': 'limit', 'Over': 'over', 'Free': 'nolimit'})

    def test_the_old_ninety_percent_red_is_gone(self):
        self.cat('Food', 1000)
        self.spend('Food', 950)
        self.assertEqual(self.statuses()['Food'], 'limit')       # amber (not red) until it is actually over
