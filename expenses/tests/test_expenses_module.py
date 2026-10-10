"""Behavioural tests for the Expenses module.

Covers the model (balance + currency maths, audit, caching), the form, the list / edit /
delete / bulk / convert views, the composer API (parse, save, undo, usuals, learning),
and CSV export. Complements the broader suites (test_views, test_expense_composer, ...).
"""

import csv
import io
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.composer import (
    LARGE_AMOUNT_DEFAULT,
    LARGE_AMOUNT_THRESHOLDS,
    UNDO_WINDOW,
    display_amount,
    usual_expenses,
)
from expenses.forms import ExpenseForm
from expenses.models import (
    Account,
    FXRate,
    CapitalEvent,
    Category,
    Expense,
    ExpenseKeywordHint,
    FinancialAuditLog,
    UserProfile,
)
from finance_tracker.plans import get_limit


def today():
    return timezone.localdate()


# Fixed FX table: 1 USD = 80 INR, 1 EUR = 90 INR. Stored as FXRate rows so the model and the
# ledger (which has its own FX lookup) always agree.
FX_ROWS = {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125', ('EUR', 'INR'): '90',
           ('INR', 'EUR'): '0.011111', ('USD', 'EUR'): '0.888889', ('EUR', 'USD'): '1.125'}


def seed_fx():
    for (src, dst), rate in FX_ROWS.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': Decimal(rate), 'source': 'test'})
    cache.clear()


class ExpenseTestBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('exp-user', self.tier)
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=Decimal('10000.00'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=Decimal('50000.00'), currency='₹')
        for name in ('Food', 'Transport'):
            Category.objects.get_or_create(user=self.user, name=name)
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        return user

    def expense(self, amount='100', account='default', **kw):
        values = dict(user=self.user, date=today(), amount=Decimal(str(amount)), description='Item',
                      category='Food', currency='₹', account=self.cash if account == 'default' else account)
        values.update(kw)
        return Expense.objects.create(**values)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance


# ---------------------------------------------------------------------------
# Model: balances, currency, audit, caching
# ---------------------------------------------------------------------------
class TestExpenseModelBalances(ExpenseTestBase):
    def test_create_debits_the_account(self):
        self.expense('250.50')
        self.assertEqual(self.bal(self.cash), Decimal('9749.50'))

    def test_expense_without_account_changes_no_balance(self):
        self.expense('250', account=None)
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertEqual(self.bal(self.bank), Decimal('50000.00'))

    def test_same_currency_base_amount_and_rate(self):
        e = self.expense('99.99')
        self.assertEqual((e.exchange_rate, e.base_amount), (Decimal('1.0'), Decimal('99.99')))

    def test_credit_card_spend_increases_the_liability(self):
        card = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD',
                                      balance=Decimal('-1000'), currency='₹')
        self.expense('400', account=card)
        self.assertEqual(self.bal(card), Decimal('-1400.00'))

    def test_update_amount_applies_only_the_difference(self):
        e = self.expense('100')
        e.amount = Decimal('150')
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('9850.00'))
        e.amount = Decimal('40')
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('9960.00'))

    def test_update_account_refunds_old_and_debits_new(self):
        e = self.expense('300')
        e.account = self.bank
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertEqual(self.bal(self.bank), Decimal('49700.00'))

    def test_update_removing_the_account_refunds_it(self):
        e = self.expense('300')
        e.account = None
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))

    def test_update_adding_an_account_debits_it(self):
        e = self.expense('300', account=None)
        e.account = self.cash
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('9700.00'))

    def test_resaving_without_changes_is_balance_neutral(self):
        e = self.expense('300')
        for _ in range(3):
            e.save()
        self.assertEqual(self.bal(self.cash), Decimal('9700.00'))

    def test_delete_restores_the_balance(self):
        e = self.expense('300')
        e.delete()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())

    def test_delete_without_account_is_safe(self):
        e = self.expense('300', account=None)
        e.delete()
        self.assertEqual(Expense.objects.count(), 0)

    def test_category_is_stripped_on_save(self):
        self.assertEqual(self.expense(category='  Food  ').category, 'Food')

    def test_dedup_key_unique_per_user_but_null_is_free(self):
        self.expense(client_dedup_key='k1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.expense(client_dedup_key='k1')
        other = self.make_user('other-dedup')
        Expense.objects.create(user=other, date=today(), amount=1, description='x', category='Food', client_dedup_key='k1')
        self.expense(client_dedup_key=None)
        self.expense(client_dedup_key=None)
        self.assertEqual(Expense.objects.filter(client_dedup_key__isnull=True).count(), 2)

    def test_audit_log_records_create_and_update(self):
        e = self.expense('100')
        e.amount = Decimal('120')
        e.category = 'Transport'
        e.save()
        logs = list(FinancialAuditLog.objects.filter(model_name='Expense', object_id=e.id).order_by('timestamp', 'id'))
        self.assertEqual([entry.action for entry in logs], ['CREATE', 'UPDATE'])
        self.assertEqual(logs[0].diff['after']['amount'], '100')
        self.assertEqual(logs[1].diff['before'], {'amount': '100.00', 'category': 'Food'})
        self.assertEqual(logs[1].diff['after'], {'amount': '120', 'category': 'Transport'})

    def test_filter_caches_are_dropped_on_save_and_delete(self):
        for op in ('save', 'delete'):
            cache.set(f'filter_categories:{self.user.id}', ['stale'])
            cache.set(f'filter_merchants:{self.user.id}', ['stale'])
            e = self.expense('1') if op == 'save' else Expense.objects.filter(user=self.user).first()
            if op == 'delete':
                cache.set(f'filter_categories:{self.user.id}', ['stale'])
                cache.set(f'filter_merchants:{self.user.id}', ['stale'])
                e.delete()
            self.assertIsNone(cache.get(f'filter_categories:{self.user.id}'), op)
            self.assertIsNone(cache.get(f'filter_merchants:{self.user.id}'), op)

    @override_settings(SOFT_DELETE_ENABLED=True)
    def test_soft_delete_hides_row_restores_balance_once_and_audits(self):
        e = self.expense('300')
        e.delete()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertTrue(Expense.objects.get(pk=e.pk).is_deleted)
        self.assertFalse(Expense.active.filter(pk=e.pk).exists())
        self.assertTrue(FinancialAuditLog.objects.filter(model_name='Expense', object_id=e.id, action='DELETE').exists())


class TestExpenseModelMultiCurrency(ExpenseTestBase):
    def setUp(self):
        super().setUp()
        seed_fx()

    def usd_account(self, balance='1000'):
        return Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT',
                                      balance=Decimal(balance), currency='$')

    def test_foreign_expense_stores_rate_and_base_amount(self):
        e = self.expense('10', currency='$', account=None)
        self.assertEqual((e.exchange_rate, e.base_amount), (Decimal('80'), Decimal('800.00')))

    def test_base_amount_is_rounded_to_paise(self):
        e = self.expense('33.33', currency='€', account=None)
        self.assertEqual(e.base_amount, Decimal('2999.70'))
        e = self.expense('1', currency='$', account=None)
        self.assertEqual(e.base_amount, Decimal('80.00'))

    def test_account_is_debited_in_the_accounts_own_currency(self):
        # ₹ expense on a $ account: 800 * 0.0125 = 10
        usd = self.usd_account()
        self.expense('800', account=usd)
        self.assertEqual(self.bal(usd), Decimal('990.00'))

    def test_dollar_expense_on_rupee_account(self):
        self.expense('10', currency='$', account=self.cash)
        self.assertEqual(self.bal(self.cash), Decimal('9200.00'))

    def test_update_reverses_in_the_original_conversion_and_reapplies(self):
        e = self.expense('10', currency='$', account=self.cash)
        e.amount = Decimal('5')
        e.save()
        self.assertEqual(self.bal(self.cash), Decimal('9600.00'))
        self.assertEqual(e.base_amount, Decimal('400.00'))

    def test_delete_restores_converted_amount(self):
        e = self.expense('10', currency='$', account=self.cash)
        e.delete()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))

    def test_switching_currency_on_update_keeps_balance_consistent(self):
        e = self.expense('100', account=self.cash)  # ₹100
        e.currency = '$'
        e.save()  # now $100 = ₹8000
        self.assertEqual(self.bal(self.cash), Decimal('2000.00'))
        e.delete()
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))

    def test_non_rupee_profile_uses_its_own_currency_as_base(self):
        usd_user = self.make_user('usd-user', currency='$')
        e = Expense.objects.create(user=usd_user, date=today(), amount=Decimal('800'), description='x',
                                   category='Food', currency='₹')
        self.assertEqual((e.exchange_rate, e.base_amount), (Decimal('0.0125'), Decimal('10.00')))


class TestExpenseManagers(ExpenseTestBase):
    def test_monthly_summary_and_category_breakdown_use_base_amount(self):
        self.expense('100', category='Food')
        self.expense('50', category='Food')
        self.expense('70', category='Transport')
        self.expense('999', category='Food', date=today().replace(day=1) - timedelta(days=1))
        other = self.make_user('other-mgr')
        Expense.objects.create(user=other, date=today(), amount=5000, description='x', category='Food')

        summary = Expense.objects.get_monthly_summary(self.user, today().year, today().month)
        self.assertEqual((summary['total'], summary['count']), (Decimal('220.00'), 3))
        rows = list(Expense.objects.get_category_breakdown(self.user, today().year, today().month))
        self.assertEqual(rows, [{'category': 'Food', 'total': Decimal('150.00')},
                                {'category': 'Transport', 'total': Decimal('70.00')}])


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
class TestExpenseForm(ExpenseTestBase):
    def data(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '120.50', 'currency': '₹', 'account': str(self.cash.id),
                'description': 'Lunch', 'category': 'Food', 'payment_method': 'UPI'}
        data.update(overrides)
        return data

    def form(self, **overrides):
        return ExpenseForm(self.data(**overrides), user=self.user)

    def test_valid_form(self):
        self.assertTrue(self.form().is_valid())

    def test_required_fields(self):
        form = ExpenseForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        for field in ('date', 'amount', 'description', 'category'):
            self.assertIn(field, form.errors)

    def test_amount_must_be_positive(self):
        for bad in ('0', '-1', '-0.01'):
            self.assertIn('amount', self.form(amount=bad).errors, bad)
        self.assertNotIn('amount', self.form(amount='0.01').errors)

    def test_amount_digit_limits(self):
        self.assertIn('amount', self.form(amount='1' * 14).errors)
        self.assertIn('amount', self.form(amount='1.234').errors)
        self.assertIn('amount', self.form(amount='abc').errors)

    def test_future_dates_are_rejected_but_today_and_tomorrow_slack_are_not(self):
        self.assertIn('date', self.form(date=(today() + timedelta(days=2)).isoformat()).errors)
        self.assertIn('date', self.form(date=(today() + timedelta(days=30)).isoformat()).errors)
        self.assertTrue(self.form(date=today().isoformat()).is_valid())
        self.assertTrue(self.form(date=(today() + timedelta(days=1)).isoformat()).is_valid())
        self.assertTrue(self.form(date=(today() - timedelta(days=4000)).isoformat()).is_valid())

    def test_category_is_stripped(self):
        form = self.form(category='  Food ')
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['category'], 'Food')

    def test_payment_method_and_currency_must_be_known(self):
        self.assertIn('payment_method', self.form(payment_method='Bitcoin').errors)
        self.assertIn('currency', self.form(currency='XXX').errors)

    def test_account_must_be_own_and_active(self):
        other = self.make_user('other-form')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertIn('account', self.form(account=str(theirs.id)).errors)
        self.bank.is_active = False
        self.bank.save()
        user = User.objects.get(pk=self.user.pk)
        self.assertIn('account', ExpenseForm(self.data(account=str(self.bank.id)), user=user).errors)

    def test_account_is_optional(self):
        self.assertTrue(self.form(account='').is_valid())

    def test_defaults_for_a_new_form(self):
        form = ExpenseForm(user=self.user)
        self.assertEqual(form.fields['currency'].initial, '₹')
        self.assertEqual(form.fields['account'].initial, self.cash.id)
        self.assertTrue(form.fields['client_dedup_key'].initial)
        self.assertNotEqual(ExpenseForm(user=self.user).fields['client_dedup_key'].initial,
                            form.fields['client_dedup_key'].initial)

    def test_free_tier_only_offers_unlocked_accounts(self):
        free = self.make_user('free-form', tier='FREE')
        accounts = [Account.objects.create(user=free, name=f'A{i}', account_type='CASH_WALLET', balance=0, currency='₹')
                    for i in range(get_limit('FREE', 'accounts') + 1)]
        form = ExpenseForm(user=free)
        offered = set(form.fields['account'].queryset.values_list('id', flat=True))
        self.assertEqual(offered, {a.id for a in accounts[:get_limit('FREE', 'accounts')]})

    def test_free_tier_only_offers_unlocked_categories(self):
        free = self.make_user('free-cats', tier='FREE')
        limit = get_limit('FREE', 'budget_categories')
        for i in range(limit + 3):
            Category.objects.get_or_create(user=free, name=f'Extra{i}')
        choices = ExpenseForm(user=free).fields['category'].widget.choices
        self.assertEqual(len(choices), limit)


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------
class TestExpenseListView(ExpenseTestBase):
    url = reverse('expense-list')

    def ids(self, response):
        return [e.id for e in response.context['expenses']]

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_shows_only_own_expenses(self):
        mine = self.expense('10')
        other = self.make_user('other-list')
        Expense.objects.create(user=other, date=today(), amount=5, description='secret', category='Food')
        response = self.client.get(self.url)
        self.assertEqual(self.ids(response), [mine.id])
        self.assertNotContains(response, 'secret')

    def test_default_period_is_this_month(self):
        this_month = self.expense('10')
        self.expense('20', date=today().replace(day=1) - timedelta(days=5))
        self.assertEqual(self.ids(self.client.get(self.url)), [this_month.id])
        response = self.client.get(self.url, {'time_period': 'all'})
        self.assertEqual(len(self.ids(response)), 2)

    def test_custom_date_range(self):
        old = self.expense('10', date=today() - timedelta(days=100))
        self.expense('20')
        start = (today() - timedelta(days=101)).isoformat()
        end = (today() - timedelta(days=99)).isoformat()
        response = self.client.get(self.url, {'time_period': 'custom', 'start_date': start, 'end_date': end})
        self.assertEqual(self.ids(response), [old.id])

    def test_totals_sum_base_amount_of_the_filtered_set(self):
        self.expense('100')
        self.expense('50.25')
        self.expense('999', date=today() - timedelta(days=200))
        response = self.client.get(self.url)
        self.assertEqual(response.context['filtered_count'], 2)
        self.assertEqual(response.context['filtered_amount'], Decimal('150.25'))

    def test_totals_use_converted_amounts_for_foreign_expenses(self):
        seed_fx()
        self.expense('10', currency='$', account=None)
        self.expense('20')
        response = self.client.get(self.url)
        self.assertEqual(response.context['filtered_amount'], Decimal('820.00'))

    def test_empty_state_has_zero_totals(self):
        response = self.client.get(self.url)
        self.assertEqual((response.context['filtered_count'], response.context['filtered_amount']), (0, 0))

    def test_filter_by_category_payment_method_and_account(self):
        a = self.expense('10', category='Food', payment_method='UPI', account=self.cash)
        b = self.expense('20', category='Transport', payment_method='Cash', account=self.bank)
        self.assertEqual(self.ids(self.client.get(self.url, {'category': 'Food'})), [a.id])
        self.assertEqual(self.ids(self.client.get(self.url, {'payment_method': 'Cash'})), [b.id])
        self.assertEqual(self.ids(self.client.get(self.url, {'account': str(self.bank.id)})), [b.id])
        both = self.client.get(self.url, {'category': ['Food', 'Transport']})
        self.assertEqual(set(self.ids(both)), {a.id, b.id})
        self.assertEqual(self.ids(self.client.get(self.url, {'category': 'Food', 'payment_method': 'Cash'})), [])

    def test_amount_range_buckets_use_base_amount(self):
        e = {v: self.expense(v) for v in ('499.99', '500', '2000', '2000.01', '10000', '10000.01')}
        def got(label):
            return {x.base_amount for x in self.client.get(self.url, {'amount_range': label}).context['expenses']}
        self.assertEqual(got('Under ₹500'), {Decimal('499.99')})
        self.assertEqual(got('₹500 to ₹2,000'), {Decimal('500'), Decimal('2000')})
        self.assertEqual(got('₹2,000 to ₹10,000'), {Decimal('2000'), Decimal('2000.01'), Decimal('10000')})
        self.assertEqual(got('Over ₹10,000'), {Decimal('10000.01')})

    def test_recurring_filter(self):
        rec = self.expense('10', description='Recurring: Netflix')
        one = self.expense('20', description='Lunch')
        self.assertEqual(self.ids(self.client.get(self.url, {'recurring': 'Recurring only'})), [rec.id])
        self.assertEqual(self.ids(self.client.get(self.url, {'recurring': 'One-time only'})), [one.id])

    def test_search_matches_description_case_insensitively(self):
        a = self.expense('10', description='Swiggy dinner')
        self.expense('20', description='Uber ride')
        self.assertEqual(self.ids(self.client.get(self.url, {'search': 'SWIGGY'})), [a.id])
        self.assertEqual(self.ids(self.client.get(self.url, {'q': 'swiggy'})), [a.id])
        self.assertEqual(self.ids(self.client.get(self.url, {'search': 'nomatch'})), [])

    def test_sorting(self):
        old_big = self.expense('900', date=today() - timedelta(days=2))
        mid = self.expense('500', date=today() - timedelta(days=1))
        new_small = self.expense('100')
        order = lambda sort: self.ids(self.client.get(self.url, {'sort': sort}))
        self.assertEqual(order('date_desc'), [new_small.id, mid.id, old_big.id])
        self.assertEqual(order('date_asc'), [old_big.id, mid.id, new_small.id])
        self.assertEqual(order('amount_desc'), [old_big.id, mid.id, new_small.id])
        self.assertEqual(order('amount_asc'), [new_small.id, mid.id, old_big.id])

    def test_pagination_is_twenty_per_page(self):
        for i in range(25):
            self.expense('1', description=f'e{i}')
        first = self.client.get(self.url)
        self.assertEqual(len(first.context['expenses']), 20)
        second = self.client.get(self.url, {'page': 2})
        self.assertEqual(len(second.context['expenses']), 5)
        self.assertEqual(first.context['filtered_count'], 25)

    def test_unknown_filter_values_and_garbage_params_do_not_crash(self):
        self.expense('10')
        for params in ({'account': 'abc'}, {'amount_range': 'zzz'}, {'sort': 'bogus'}, {'time_period': 'bogus'},
                       {'start_date': 'not-a-date', 'time_period': 'custom'}, {'page': 'x'}, {'category': ''}):
            with self.subTest(params=params):
                self.assertIn(self.client.get(self.url, params).status_code, (200, 404))

    def test_active_filter_count(self):
        response = self.client.get(self.url, {'category': 'Food', 'search': 'x', 'sort': 'amount_asc', 'time_period': 'all'})
        self.assertEqual(response.context['active_filters_count'], 4)
        self.assertEqual(self.client.get(self.url).context['active_filters_count'], 0)

    def test_current_month_reports_days_left_in_period(self):
        response = self.client.get(self.url)
        self.assertTrue(response.context['is_current_month'])
        self.assertGreaterEqual(response.context['days_left'], 0)
        self.assertFalse(self.client.get(self.url, {'time_period': 'all'}).context['is_current_month'])

    def test_htmx_requests_render_the_partial(self):
        self.expense('10')
        full = self.client.get(self.url)
        partial = self.client.get(self.url, HTTP_HX_REQUEST='true')
        self.assertIn('expenses/expense_list.html', [t.name for t in full.templates])
        self.assertIn('expenses/partials/_expense_list.html', [t.name for t in partial.templates])
        self.assertNotIn('expenses/expense_list.html', [t.name for t in partial.templates])

    def test_bulk_edit_category_options_come_from_the_users_categories_and_expenses(self):
        self.expense('10', category='LegacyCat')
        categories = self.client.get(self.url).context['categories']
        self.assertIn('LegacyCat', categories)
        self.assertIn('Food', categories)


# ---------------------------------------------------------------------------
# Edit view
# ---------------------------------------------------------------------------
class TestExpenseUpdateView(ExpenseTestBase):
    def post(self, e, **overrides):
        data = {'date': e.date.isoformat(), 'amount': str(e.amount), 'currency': e.currency,
                'account': str(e.account_id or ''), 'description': e.description, 'category': e.category,
                'payment_method': e.payment_method}
        data.update(overrides)
        return self.client.post(reverse('expense-edit', kwargs={'pk': e.pk}), data)

    def test_get_renders_form_for_owner_by_pk_and_uuid(self):
        e = self.expense('100')
        for key in (e.pk, e.uuid):
            self.assertEqual(self.client.get(reverse('expense-edit', kwargs={'pk': key})).status_code, 200)

    def test_other_users_expense_is_404(self):
        other = self.make_user('other-edit')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        self.assertEqual(self.client.get(reverse('expense-edit', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.post(theirs, amount='1').status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.amount, Decimal('5.00'))

    def test_update_changes_fields_and_balances(self):
        e = self.expense('100')
        response = self.post(e, amount='175.25', account=str(self.bank.id), description='Edited', payment_method='UPI')
        self.assertRedirects(response, reverse('expense-list'))
        e.refresh_from_db()
        self.assertEqual((e.amount, e.account, e.description, e.payment_method),
                         (Decimal('175.25'), self.bank, 'Edited', 'UPI'))
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertEqual(self.bal(self.bank), Decimal('49824.75'))

    def test_invalid_amounts_are_rejected_and_nothing_changes(self):
        e = self.expense('100')
        for bad in ('0', '-500', 'abc', ''):
            with self.subTest(amount=bad):
                self.assertEqual(self.post(e, amount=bad).status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.amount, Decimal('100.00'))
        self.assertEqual(self.bal(self.cash), Decimal('9900.00'))

    def test_future_date_is_rejected(self):
        e = self.expense('100')
        self.assertEqual(self.post(e, date=(today() + timedelta(days=10)).isoformat()).status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.date, today())

    def test_cannot_move_an_expense_onto_someone_elses_account(self):
        other = self.make_user('other-acct')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=100, currency='₹')
        e = self.expense('100')
        self.assertEqual(self.post(e, account=str(theirs.id)).status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.account, self.cash)
        self.assertEqual(self.bal(theirs), Decimal('100.00'))

    def test_next_param_is_honoured_only_for_same_site_urls(self):
        e = self.expense('100')
        self.assertRedirects(self.post(e, next='/transactions/'), '/transactions/', fetch_redirect_response=False)
        response = self.post(e, next='https://evil.example/')
        self.assertEqual(response.url, reverse('expense-list'))
        response = self.post(e, next='//evil.example/')
        self.assertEqual(response.url, reverse('expense-list'))

    def test_currency_conversion_failure_is_reported_not_a_500(self):
        e = self.expense('100')
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.post(e, currency='$')
        self.assertEqual(response.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.currency, '₹')


# ---------------------------------------------------------------------------
# Delete / bulk delete / bulk edit / convert
# ---------------------------------------------------------------------------
class TestExpenseDeleteViews(ExpenseTestBase):
    def test_delete_confirmation_page_and_deletion(self):
        e = self.expense('300')
        url = reverse('expense-delete', kwargs={'pk': e.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(Expense.objects.filter(pk=e.pk).count(), 1)
        self.assertRedirects(self.client.post(url), reverse('expense-list'))
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))

    def test_delete_by_uuid(self):
        e = self.expense('300')
        self.client.post(reverse('expense-delete', kwargs={'pk': e.uuid}))
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())

    def test_cannot_delete_someone_elses_expense(self):
        other = self.make_user('other-del')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        self.assertEqual(self.client.post(reverse('expense-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())

    def test_delete_redirect_keeps_filters_and_rejects_offsite_next(self):
        e = self.expense('1')
        response = self.client.post(reverse('expense-delete', kwargs={'pk': e.pk}) + '?category=Food&sort=date_asc')
        self.assertEqual(response.url, reverse('expense-list') + '?category=Food&sort=date_asc')

        e2 = self.expense('1')
        response = self.client.post(reverse('expense-delete', kwargs={'pk': e2.pk}) + '?next=/transactions/')
        self.assertEqual(response.url, '/transactions/')

        e3 = self.expense('1')
        response = self.client.post(reverse('expense-delete', kwargs={'pk': e3.pk}) + '?next=https://evil.example/')
        self.assertEqual(response.url, reverse('expense-list'))
        self.assertFalse(Expense.objects.filter(pk=e3.pk).exists())

    def test_bulk_delete_removes_selected_and_restores_balances(self):
        a, b, keep = self.expense('100'), self.expense('200', account=self.bank), self.expense('50')
        response = self.client.post(reverse('expense-bulk-delete'), {'expense_ids': [a.id, b.id]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(Expense.objects.values_list('id', flat=True)), [keep.id])
        self.assertEqual(self.bal(self.cash), Decimal('9950.00'))
        self.assertEqual(self.bal(self.bank), Decimal('50000.00'))

    def test_bulk_delete_ignores_other_users_ids(self):
        other = self.make_user('other-bulk')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        mine = self.expense('10')
        self.client.post(reverse('expense-bulk-delete'), {'expense_ids': [theirs.id, mine.id]})
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())
        self.assertFalse(Expense.objects.filter(pk=mine.pk).exists())

    def test_bulk_delete_with_nothing_or_junk_selected_is_a_noop_not_a_500(self):
        e = self.expense('10')
        for ids in ([], ['abc'], [''], ['1; DROP TABLE'], ['99999999']):
            with self.subTest(ids=ids):
                self.assertEqual(self.client.post(reverse('expense-bulk-delete'), {'expense_ids': ids}).status_code, 302)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())

    def test_bulk_delete_keeps_the_list_query_string(self):
        e = self.expense('10')
        response = self.client.post(reverse('expense-bulk-delete') + '?category=Food', {'expense_ids': [e.id]})
        self.assertEqual(response.url, reverse('expense-list') + '?category=Food')

    def test_bulk_endpoints_require_login_and_post(self):
        self.client.logout()
        self.assertEqual(self.client.post(reverse('expense-bulk-delete'), {}).status_code, 302)
        self.assertEqual(self.client.post(reverse('expense-bulk-edit'), {}).status_code, 302)


class TestExpenseBulkEdit(ExpenseTestBase):
    url = reverse('expense-bulk-edit')

    def test_updates_category_and_payment_method(self):
        a, b = self.expense('10'), self.expense('20')
        self.client.post(self.url, {'expense_ids': [a.id, b.id], 'bulk_category': 'Transport', 'bulk_payment_method': 'UPI'})
        for e in (a, b):
            e.refresh_from_db()
            self.assertEqual((e.category, e.payment_method), ('Transport', 'UPI'))

    def test_each_field_can_be_updated_on_its_own(self):
        e = self.expense('10', payment_method='Cash')
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'Transport'})
        e.refresh_from_db()
        self.assertEqual((e.category, e.payment_method), ('Transport', 'Cash'))
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_payment_method': 'UPI'})
        e.refresh_from_db()
        self.assertEqual((e.category, e.payment_method), ('Transport', 'UPI'))

    def test_balances_amounts_and_dates_are_untouched(self):
        e = self.expense('123.45', date=today() - timedelta(days=3))
        before = self.bal(self.cash)
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'Transport'})
        e.refresh_from_db()
        self.assertEqual((e.amount, e.date), (Decimal('123.45'), today() - timedelta(days=3)))
        self.assertEqual(self.bal(self.cash), before)

    def test_edit_is_audited(self):
        e = self.expense('10')
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'Transport'})
        log = FinancialAuditLog.objects.filter(model_name='Expense', object_id=e.id, action='UPDATE').latest('id')
        self.assertEqual(log.diff['after']['category'], 'Transport')

    def test_rejects_unknown_category_and_payment_method(self):
        e = self.expense('10')
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'NoSuchCategory'})
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_payment_method': 'Bitcoin'})
        e.refresh_from_db()
        self.assertEqual((e.category, e.payment_method), ('Food', 'Cash'))

    def test_nothing_selected_or_no_fields_changes_nothing(self):
        e = self.expense('10')
        self.assertEqual(self.client.post(self.url, {'bulk_category': 'Transport'}).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'expense_ids': [e.id]}).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'expense_ids': ['abc'], 'bulk_category': 'Food'}).status_code, 302)
        e.refresh_from_db()
        self.assertEqual(e.category, 'Food')

    def test_other_users_expenses_are_never_edited(self):
        other = self.make_user('other-bulkedit')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        self.client.post(self.url, {'expense_ids': [theirs.id], 'bulk_category': 'Transport'})
        theirs.refresh_from_db()
        self.assertEqual(theirs.category, 'Food')

    def test_a_category_created_moments_ago_is_accepted_despite_stale_filter_cache(self):
        e = self.expense('10')
        cache.set(f'filter_categories:{self.user.id}', ['Food'])  # stale: does not know the new one
        Category.objects.create(user=self.user, name='Brand New')
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'Brand New'})
        e.refresh_from_db()
        self.assertEqual(e.category, 'Brand New')

    def test_category_filter_cache_is_refreshed(self):
        e = self.expense('10')
        cache.set(f'filter_categories:{self.user.id}', ['stale'])
        self.client.post(self.url, {'expense_ids': [e.id], 'bulk_category': 'Transport'})
        self.assertIsNone(cache.get(f'filter_categories:{self.user.id}'))


class TestExpenseConvertToCapitalEvent(ExpenseTestBase):
    def convert(self, e, query=''):
        return self.client.post(reverse('expense-convert', kwargs={'pk': e.pk}) + query)

    def test_converts_and_removes_the_expense(self):
        e = self.expense('50000', category='Large Purchase', description='Laptop', account=self.bank,
                         date=today() - timedelta(days=2))
        response = self.convert(e)
        self.assertRedirects(response, reverse('capital-event-list'), fetch_redirect_response=False)
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())
        event = CapitalEvent.objects.get(user=self.user)
        self.assertEqual((event.amount, event.currency, event.note, event.account, event.date, event.subtype),
                         (Decimal('50000.00'), '₹', 'Laptop', self.bank, today() - timedelta(days=2), 'large_purchase'))

    def test_net_balance_effect_is_a_single_debit(self):
        e = self.expense('5000', account=self.bank)
        self.assertEqual(self.bal(self.bank), Decimal('45000.00'))
        self.convert(e)
        self.assertEqual(self.bal(self.bank), Decimal('45000.00'), 'money must be debited once, not twice or zero times')

    def test_subtype_is_matched_from_category_else_other(self):
        cases = {'Medical Lump Sum': 'medical_lump_sum', 'gift given to friend': 'gift_given',
                 'Loan Prepayment': 'loan_prepayment', 'Groceries': 'other'}
        for category, expected in cases.items():
            with self.subTest(category=category):
                e = self.expense('10', category=category, account=None)
                self.convert(e)
                self.assertEqual(CapitalEvent.objects.filter(user=self.user).latest('id').subtype, expected)

    def test_get_is_not_allowed(self):
        e = self.expense('10')
        self.assertEqual(self.client.get(reverse('expense-convert', kwargs={'pk': e.pk})).status_code, 405)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())

    def test_other_users_expense_is_not_convertible(self):
        other = self.make_user('other-convert')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        self.assertEqual(self.convert(theirs).status_code, 404)
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())
        self.assertFalse(CapitalEvent.objects.exists())

    def test_next_redirect_only_for_same_site(self):
        e = self.expense('10', account=None)
        self.assertEqual(self.convert(e, '?next=/expenses/').url, '/expenses/')
        e = self.expense('10', account=None)
        self.assertEqual(self.convert(e, '?next=https://evil.example/').url, reverse('capital-event-list'))


# ---------------------------------------------------------------------------
# Composer API
# ---------------------------------------------------------------------------
class ComposerBase(ExpenseTestBase):
    def post_json(self, name, body, **kwargs):
        raw = body if isinstance(body, (str, bytes)) else json.dumps(body)
        return self.client.post(reverse(name), raw, content_type='application/json', **kwargs)

    def save(self, **overrides):
        body = {'key': str(uuid.uuid4()), 'amount': '450', 'category': 'Food', 'description': 'Swiggy',
                'account_id': self.cash.id, 'payment_method': 'UPI', 'date': today().isoformat()}
        body.update(overrides)
        return self.post_json('expense-composer-save', body)


class TestComposerBootstrap(ComposerBase):
    def data(self):
        response = self.client.get(reverse('expense-composer-data'))
        self.assertEqual(response.status_code, 200)
        return response.json()['data']

    def test_requires_login_and_get(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('expense-composer-data')).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('expense-composer-data')).status_code, 405)

    def test_payload_defaults_for_a_fresh_user(self):
        data = self.data()
        self.assertEqual(data['today'], today().isoformat())
        self.assertEqual(data['default_currency'], '₹')
        self.assertEqual(data['default_payment_method'], 'Cash')
        self.assertEqual((data['default_account_id'], data['default_account_source']), (self.cash.id, 'first'))
        self.assertEqual([a['id'] for a in data['accounts']], [self.cash.id, self.bank.id])
        self.assertEqual(data['usuals'], [])
        self.assertIn('Food', data['categories'])

    def test_defaults_follow_the_last_expense(self):
        self.expense('10', account=self.bank, payment_method='UPI')
        data = self.data()
        self.assertEqual((data['default_account_id'], data['default_account_source']), (self.bank.id, 'last_used'))
        self.assertEqual(data['default_payment_method'], 'UPI')

    def test_large_amount_thresholds_per_currency(self):
        thresholds = self.data()['large_amount_thresholds']
        self.assertEqual(thresholds['₹'], LARGE_AMOUNT_THRESHOLDS['₹'])
        self.assertEqual(thresholds['$'], LARGE_AMOUNT_DEFAULT)
        self.assertEqual(thresholds['¥'], LARGE_AMOUNT_THRESHOLDS['¥'])

    def test_free_tier_sees_only_unlocked_accounts(self):
        free = self.make_user('free-composer', tier='FREE')
        self.client.force_login(free)
        limit = get_limit('FREE', 'accounts')
        for i in range(limit + 2):
            Account.objects.create(user=free, name=f'F{i}', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertEqual(len(self.data()['accounts']), limit)

    def test_never_leaks_other_users_accounts(self):
        other = self.make_user('other-composer')
        Account.objects.create(user=other, name='Secret Acct', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertNotIn('Secret Acct', json.dumps(self.data()))


class TestComposerUsuals(ExpenseTestBase):
    def test_needs_two_repeats_of_the_same_description_and_amount(self):
        self.expense('320', description='Swiggy')
        self.assertEqual(usual_expenses(self.user), [])
        cache.clear()
        self.expense('320', description='Swiggy', category='Food', account=self.bank, payment_method='UPI')
        usuals = usual_expenses(self.user)
        self.assertEqual(len(usuals), 1)
        self.assertEqual((usuals[0]['label'], usuals[0]['amount'], usuals[0]['payment_method'], usuals[0]['account_id']),
                         ('swiggy 320', '320.00', 'UPI', self.bank.id))

    def test_different_amounts_do_not_count_as_the_same_usual(self):
        self.expense('320', description='Swiggy')
        self.expense('330', description='Swiggy')
        self.assertEqual(usual_expenses(self.user), [])

    def test_only_the_last_sixty_days_count(self):
        old = today() - timedelta(days=61)
        self.expense('80', description='Auto', date=old)
        self.expense('80', description='Auto', date=old)
        self.assertEqual(usual_expenses(self.user), [])

    def test_ranked_by_frequency_and_limited(self):
        for desc, n in (('A', 2), ('B', 3), ('C', 2), ('D', 2), ('E', 2)):
            for _ in range(n):
                self.expense('10', description=desc)
        usuals = usual_expenses(self.user)
        self.assertEqual(len(usuals), 4)
        self.assertEqual(usuals[0]['description'], 'B')

    def test_other_users_expenses_are_not_included(self):
        other = self.make_user('other-usual')
        for _ in range(3):
            Expense.objects.create(user=other, date=today(), amount=10, description='Theirs', category='Food')
        self.assertEqual(usual_expenses(self.user), [])

    def test_fractional_amount_label(self):
        self.expense('99.50', description='Tea')
        self.expense('99.50', description='Tea')
        self.assertEqual(usual_expenses(self.user)[0]['label'], 'tea 99.50')


class TestComposerParse(ComposerBase):
    def parse(self, text, **extra):
        return self.post_json('parse-expense', {'text': text, **extra})

    def test_requires_login(self):
        self.client.logout()
        self.assertEqual(self.parse('x 10').status_code, 302)

    def test_parses_amount_date_payment_and_never_saves(self):
        response = self.parse('uber to airport 450 upi yesterday')
        data = response.json()['data']
        self.assertEqual((data['amount'], data['payment_method'], data['date']),
                         ('450.00', 'UPI', (today() - timedelta(days=1)).isoformat()))
        self.assertEqual(Expense.objects.count(), 0)

    def test_category_is_limited_to_the_users_own_categories(self):
        data = self.parse('swiggy 320').json()['data']
        self.assertIn(data['category'], (None, *Category.objects.filter(user=self.user).values_list('name', flat=True)))
        Category.objects.filter(user=self.user).delete()
        data = self.parse('swiggy 320').json()['data']
        self.assertIsNone(data['category'])
        self.assertEqual(data['confidence']['category'], 'low')
        self.assertIn('category', data['check'])

    def test_matches_account_by_name(self):
        Account.objects.filter(pk=self.bank.pk).update(name='HDFC Savings')
        data = self.parse('rent 1.5k hdfc').json()['data']
        self.assertEqual(data['amount'], '1500.00')
        self.assertEqual(data['account_id'], self.bank.id)

    def test_learned_hint_beats_generic_guess(self):
        ExpenseKeywordHint.objects.create(user=self.user, keyword='swiggy', category='Food',
                                          account=self.bank, payment_method='UPI')
        data = self.parse('swiggy 320').json()['data']
        self.assertEqual((data['account_id'], data['payment_method']), (self.bank.id, 'UPI'))

    def test_empty_and_invalid_input(self):
        self.assertFalse(self.parse('   ').json()['success'])
        self.assertFalse(self.parse('').json()['success'])
        response = self.post_json('parse-expense', '{not json')
        self.assertEqual(response.status_code, 400)
        response = self.post_json('parse-expense', '[1,2]')
        self.assertEqual(response.status_code, 400)

    def test_very_long_input_is_truncated_not_rejected(self):
        self.assertEqual(self.parse('coffee 250 ' + 'x' * 5000).status_code, 200)

    def test_foreign_currency_symbol_is_detected(self):
        data = self.parse('lunch $12').json()['data']
        self.assertEqual((data['amount'], data['currency']), ('12.00', '$'))

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse('parse-expense')).status_code, 405)


class TestComposerSave(ComposerBase):
    def test_requires_login_and_post(self):
        self.assertEqual(self.client.get(reverse('expense-composer-save')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.save().status_code, 302)

    def test_saves_one_expense_and_debits_the_account(self):
        response = self.save()
        body = response.json()
        self.assertTrue(body['success'])
        self.assertFalse(body['duplicate'])
        e = Expense.objects.get(user=self.user)
        self.assertEqual((e.amount, e.category, e.description, e.payment_method, e.account, e.currency, e.date),
                         (Decimal('450'), 'Food', 'Swiggy', 'UPI', self.cash, '₹', today()))
        self.assertEqual(self.bal(self.cash), Decimal('9550.00'))
        self.assertEqual(body['expense']['amount_display'], '₹450')
        self.assertEqual(body['expense']['id'], str(e.uuid))
        self.assertEqual(body['expense']['account'], 'Cash')
        self.assertEqual(body['undo_url'], reverse('expense-composer-undo', args=[e.uuid]))
        self.assertEqual(body['edit_url'], reverse('expense-edit', args=[e.uuid]))
        self.assertIn('9,550', body['account_label'])

    def test_replaying_the_same_key_never_double_posts(self):
        key = str(uuid.uuid4())
        first = self.save(key=key).json()
        second = self.save(key=key).json()
        self.assertFalse(first['duplicate'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(first['expense']['id'], second['expense']['id'])
        self.assertEqual(Expense.objects.count(), 1)
        self.assertEqual(self.bal(self.cash), Decimal('9550.00'))

    def test_same_key_from_two_users_is_independent(self):
        key = str(uuid.uuid4())
        self.save(key=key)
        other = self.make_user('other-key')
        self.client.force_login(other)
        Category.objects.get_or_create(user=other, name='Food')
        response = self.save(key=key, account_id='')
        self.assertTrue(response.json()['success'])
        self.assertFalse(response.json()['duplicate'])
        self.assertEqual(Expense.objects.count(), 2)

    def test_missing_key_still_saves(self):
        body = {'amount': '10', 'category': 'Food', 'date': today().isoformat()}
        self.assertTrue(self.post_json('expense-composer-save', body).json()['success'])

    def test_amount_parsing(self):
        for raw, ok in (('450', True), ('1,250', True), (' 99.5 ', True), ('0', False), ('-5', False),
                        ('', False), ('abc', False), ('1e3x', False), (None, False)):
            with self.subTest(amount=raw):
                response = self.save(amount=raw)
                self.assertEqual(response.status_code, 200 if ok else 400)
                if not ok:
                    self.assertIn('amount', response.json()['field_errors'])
        self.assertEqual(Expense.objects.count(), 3)
        self.assertEqual(Expense.objects.get(amount=Decimal('1250')).amount, Decimal('1250.00'))

    def test_category_must_be_the_users_own_and_is_case_insensitive(self):
        self.assertEqual(self.save(category='Nope').status_code, 400)
        self.assertEqual(self.save(category='').status_code, 400)
        self.assertEqual(self.save(category=None).status_code, 400)
        response = self.save(category='fOOd')
        self.assertTrue(response.json()['success'])
        self.assertEqual(Expense.objects.get().category, 'Food')

    def test_blank_description_falls_back_to_category(self):
        self.save(description='   ')
        self.assertEqual(Expense.objects.get().description, 'Food')

    def test_future_date_is_rejected(self):
        response = self.save(date=(today() + timedelta(days=30)).isoformat())
        self.assertEqual(response.status_code, 400)
        self.assertIn('date', response.json()['field_errors'])
        self.assertEqual(Expense.objects.count(), 0)

    def test_bad_date_and_payment_method_and_currency_are_rejected(self):
        for field, value in (('date', 'garbage'), ('payment_method', 'Bitcoin'), ('currency', 'XXX')):
            with self.subTest(field=field):
                response = self.save(**{field: value})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()['code'], 'invalid')
        self.assertEqual(Expense.objects.count(), 0)

    def test_account_must_belong_to_the_user_and_be_unlocked(self):
        other = self.make_user('other-save')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=100, currency='₹')
        self.assertEqual(self.save(account_id=theirs.id).status_code, 400)
        self.assertEqual(self.bal(theirs), Decimal('100.00'))
        self.assertEqual(Expense.objects.count(), 0)

    def test_account_is_optional(self):
        self.assertTrue(self.save(account_id='').json()['success'])
        self.assertIsNone(Expense.objects.get().account)

    def test_foreign_currency_is_converted(self):
        seed_fx()
        response = self.save(currency='$', amount='10', account_id='')
        self.assertTrue(response.json()['success'])
        e = Expense.objects.get()
        self.assertEqual((e.currency, e.base_amount), ('$', Decimal('800.00')))
        self.assertEqual(response.json()['expense']['amount_display'], '$10')

    def test_currency_conversion_failure_returns_a_clear_error(self):
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.save(currency='$')
        self.assertEqual((response.status_code, response.json()['code']), (400, 'currency'))
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))

    def test_malformed_json_is_a_400(self):
        self.assertEqual(self.post_json('expense-composer-save', '{oops').status_code, 400)
        self.assertEqual(self.post_json('expense-composer-save', '"str"').status_code, 400)

    def test_free_plan_monthly_cap_blocks_with_upgrade_link(self):
        free = self.make_user('free-cap', tier='FREE')
        self.client.force_login(free)
        Category.objects.get_or_create(user=free, name='Food')
        cap = get_limit('FREE', 'expenses_per_month')
        Expense.objects.bulk_create([
            Expense(user=free, date=today(), amount=1, description='x', category='Food', base_amount=1) for _ in range(cap)
        ])
        response = self.save(account_id='')
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body['code'], 'limit')
        self.assertEqual(body['upgrade_url'], reverse('pricing'))
        self.assertIn(str(cap), body['error'])
        self.assertEqual(Expense.objects.filter(user=free).count(), cap)

    def test_cap_counts_only_the_current_month_and_backdated_adds_are_free(self):
        free = self.make_user('free-cap2', tier='FREE')
        self.client.force_login(free)
        Category.objects.get_or_create(user=free, name='Food')
        cap = get_limit('FREE', 'expenses_per_month')
        Expense.objects.bulk_create([
            Expense(user=free, date=today(), amount=1, description='x', category='Food', base_amount=1) for _ in range(cap - 1)
        ])
        self.assertTrue(self.save(account_id='').json()['success'])          # the last allowed one
        self.assertEqual(self.save(account_id='').status_code, 403)          # one over
        last_month = (today().replace(day=1) - timedelta(days=1)).isoformat()
        self.assertTrue(self.save(account_id='', date=last_month).json()['success'])  # other months unaffected

    def test_paid_plans_have_no_cap(self):
        self.assertIn(get_limit('PRO', 'expenses_per_month'), (-1, None))

    def test_saving_teaches_the_keyword_hint_and_latest_choice_wins(self):
        self.save(description='Swiggy dinner', payment_method='UPI')
        hint = ExpenseKeywordHint.objects.get(user=self.user)
        self.assertEqual((hint.category, hint.account, hint.payment_method, hint.use_count),
                         ('Food', self.cash, 'UPI', 1))
        self.save(description='Swiggy dinner', payment_method='Credit Card', account_id=self.bank.id, category='Transport')
        hint.refresh_from_db()
        self.assertEqual((hint.category, hint.account, hint.payment_method, hint.use_count),
                         ('Transport', self.bank, 'Credit Card', 2))
        self.assertEqual(ExpenseKeywordHint.objects.filter(user=self.user).count(), 1)

    def test_hints_are_pruned_to_the_cap_dropping_the_least_used(self):
        with patch.object(ExpenseKeywordHint, 'MAX_PER_USER', 3):
            for word in ('alpha', 'bravo', 'charlie'):
                self.save(description=word)
            self.save(description='alpha')   # alpha now used twice
            self.save(description='delta')
            keywords = set(ExpenseKeywordHint.objects.filter(user=self.user).values_list('keyword', flat=True))
        self.assertEqual(len(keywords), 3)
        self.assertIn('alpha', keywords)
        self.assertIn('delta', keywords)

    def test_save_clears_the_usual_cache(self):
        cache.set(f'composer_usual:{self.user.id}', ['stale'])
        self.save()
        self.assertIsNone(cache.get(f'composer_usual:{self.user.id}'))

    def test_display_amount_formats(self):
        mk = lambda amount, cur: Expense(amount=Decimal(amount), currency=cur)
        self.assertEqual(display_amount(mk('450', '₹')), '₹450')
        self.assertEqual(display_amount(mk('450.50', '₹')), '₹450.50')
        self.assertEqual(display_amount(mk('1234567', '₹')), '₹12,34,567')
        self.assertEqual(display_amount(mk('1234567.25', '₹')), '₹12,34,567.25')
        self.assertEqual(display_amount(mk('10.50', '$')), '$10.50')
        self.assertEqual(display_amount(mk('1500', '$')), '$1,500')


class TestComposerUndo(ComposerBase):
    def undo(self, e):
        return self.client.post(reverse('expense-composer-undo', args=[e.uuid]))

    def test_undo_removes_expense_and_restores_balance(self):
        self.save()
        e = Expense.objects.get()
        response = self.undo(e)
        self.assertTrue(response.json()['success'])
        self.assertFalse(Expense.objects.exists())
        self.assertEqual(self.bal(self.cash), Decimal('10000.00'))
        self.assertEqual(response.json()['account_id'], self.cash.id)

    def test_cannot_undo_after_the_window(self):
        self.save()
        e = Expense.objects.get()
        Expense.objects.filter(pk=e.pk).update(created_at=timezone.now() - UNDO_WINDOW - timedelta(seconds=5))
        response = self.undo(e)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())
        self.assertEqual(self.bal(self.cash), Decimal('9550.00'))

    def test_can_undo_just_inside_the_window(self):
        self.save()
        e = Expense.objects.get()
        Expense.objects.filter(pk=e.pk).update(created_at=timezone.now() - UNDO_WINDOW + timedelta(seconds=30))
        self.assertEqual(self.undo(e).status_code, 200)

    def test_cannot_undo_someone_elses_expense(self):
        other = self.make_user('other-undo')
        theirs = Expense.objects.create(user=other, date=today(), amount=5, description='x', category='Food')
        self.assertEqual(self.undo(theirs).status_code, 404)
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())

    def test_undo_is_post_only_and_second_undo_is_404(self):
        self.save()
        e = Expense.objects.get()
        self.assertEqual(self.client.get(reverse('expense-composer-undo', args=[e.uuid])).status_code, 405)
        self.undo(e)
        self.assertEqual(self.undo(e).status_code, 404)


class TestComposerEvents(ComposerBase):
    def event(self, name, props=None):
        return self.post_json('expense-composer-event', {'event': name, 'props': props or {}})

    def test_allowed_events_are_accepted(self):
        for name, props in (('expense_form_opened', {'entry': 'fab'}), ('quick_add_parsed', {'success': True, 'fields_filled': 99}),
                            ('expense_field_edited', {'field': 'amount'}), ('voice_used', {'lang': 'hi'}),
                            ('your_usual_chip_used', {})):
            with self.subTest(event=name):
                self.assertEqual(self.event(name, props).status_code, 200)

    def test_unknown_events_and_fields_are_rejected(self):
        self.assertEqual(self.event('drop_database').status_code, 400)
        self.assertEqual(self.event('expense_field_edited', {'field': 'password'}).status_code, 400)
        self.assertEqual(self.post_json('expense-composer-event', '{bad').status_code, 400)

    def test_props_are_sanitised_before_sending(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.event('expense_form_opened', {'entry': '<script>', 'amount': 123})
            self.event('quick_add_parsed', {'success': 1, 'fields_filled': 99, 'check_count': -4, 'text': 'swiggy 320'})
            self.event('voice_used', {'lang': 'xx'})
        (_u, _n, p1), (_u2, _n2, p2), (_u3, _n3, p3) = [c.args for c in capture.call_args_list]
        self.assertEqual(p1, {'entry': 'other'})
        self.assertEqual(p2, {'success': True, 'fields_filled': 7, 'check_count': 0})
        self.assertEqual(p3, {'lang': 'en'})

    def test_save_analytics_never_contain_amount_description_or_account_name(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.save(description='Very private thing', amount='98765')
        name, props = capture.call_args.args[1], capture.call_args.args[2]
        self.assertEqual(name, 'expense_added')
        blob = json.dumps(props, default=str)
        for secret in ('Very private thing', '98765', 'Cash'):
            self.assertNotIn(secret, blob)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
class TestExpenseExport(ExpenseTestBase):
    url = reverse('export-expenses')

    def rows(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        return list(csv.reader(io.StringIO(response.content.decode())))

    def descriptions(self, **params):
        return {row[1] for row in self.rows(**params)[1:]}

    def test_free_plan_is_sent_to_pricing(self):
        free = self.make_user('free-export', tier='FREE')
        self.client.force_login(free)
        self.assertRedirects(self.client.get(self.url), reverse('pricing'), fetch_redirect_response=False)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_columns_and_values(self):
        seed_fx()
        self.expense('10', currency='$', description='Foreign', account=self.cash)
        rows = self.rows()
        self.assertEqual([c for c in rows[0]], ['Date', 'Description', 'Amount', 'Currency', 'Exchange Rate',
                                                'Base Amount', 'Category', 'Payment Method', 'Account', 'Account Currency'])
        row = rows[1]
        self.assertEqual((row[1], row[2], row[3], row[4], row[5], row[6], row[8], row[9]),
                         ('Foreign', '10.00', '$', '80.000000', '800.00', 'Food', 'Cash', '₹'))

    def test_only_own_expenses_are_exported(self):
        other = self.make_user('other-export')
        Expense.objects.create(user=other, date=today(), amount=5, description='Secret', category='Food')
        self.expense('10', description='Mine')
        self.assertEqual(self.descriptions(), {'Mine'})

    def test_default_export_matches_the_default_list_view_period(self):
        self.expense('10', description='This month')
        self.expense('10', description='Old', date=today() - timedelta(days=200))
        self.assertEqual(self.descriptions(), {'This month'})
        self.assertEqual(self.descriptions(time_period='all'), {'This month', 'Old'})

    def test_export_applies_every_filter_the_list_view_applies(self):
        self.expense('100', description='match', category='Food', payment_method='UPI', account=self.bank)
        self.expense('100', description='wrong-cat', category='Transport', payment_method='UPI', account=self.bank)
        self.expense('100', description='wrong-pm', category='Food', payment_method='Cash', account=self.bank)
        self.expense('100', description='wrong-acct', category='Food', payment_method='UPI', account=self.cash)
        self.expense('9000', description='wrong-amt', category='Food', payment_method='UPI', account=self.bank)
        params = {'category': 'Food', 'payment_method': 'UPI', 'account': str(self.bank.id), 'amount_range': 'Under ₹500'}
        self.assertEqual(self.descriptions(**params), {'match'})
        listed = self.client.get(reverse('expense-list'), params).context['expenses']
        self.assertEqual({e.description for e in listed}, {'match'}, 'export and list must agree')

    def test_search_and_date_range(self):
        self.expense('10', description='coffee', date=today() - timedelta(days=10))
        self.expense('10', description='tea', date=today() - timedelta(days=1))
        self.assertEqual(self.descriptions(search='COF', time_period='all'), {'coffee'})
        start = (today() - timedelta(days=3)).isoformat()
        self.assertEqual(self.descriptions(time_period='custom', start_date=start), {'tea'})

    def test_legacy_year_and_month_parameters_still_work(self):
        self.expense('10', description='this-year')
        self.expense('10', description='older', date=today().replace(year=today().year - 2))
        self.assertEqual(self.descriptions(time_period='all', year=str(today().year)), {'this-year'})
        self.assertEqual(self.descriptions(time_period='all', month=str(today().month), year=str(today().year)), {'this-year'})

    def test_export_is_ordered_newest_first_by_default_and_honours_sort(self):
        self.expense('50', description='old', date=today() - timedelta(days=2))
        self.expense('10', description='new')
        newest_first = [r[1] for r in self.rows(time_period='all')[1:]]
        self.assertEqual(newest_first, ['new', 'old'])
        by_amount = [r[1] for r in self.rows(time_period='all', sort='amount_desc')[1:]]
        self.assertEqual(by_amount, ['old', 'new'])


# ---------------------------------------------------------------------------
# Templates that the Expense flows rely on
# ---------------------------------------------------------------------------
class TestConfirmDeletePagesRender(ExpenseTestBase):
    """Regression: these pages used {% trans %} without {% load i18n %} and crashed on GET."""

    def test_confirm_delete_pages_render(self):
        from expenses.models import Income, RecurringTransaction
        income = Income.objects.create(user=self.user, date=today(), amount=Decimal('5'), description='x',
                                       source_type='SALARY' if False else Income._meta.get_field('source_type').choices[0][0])
        recurring = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=Decimal('5'), currency='₹', account=self.cash,
            category='Food', description='r', frequency='MONTHLY', start_date=today())
        category = Category.objects.get(user=self.user, name='Transport')
        e = self.expense('5')
        for name, pk in (('expense-delete', e.pk), ('income-delete', income.pk),
                         ('recurring-delete', recurring.pk), ('category-delete', category.pk)):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name, kwargs={'pk': pk})).status_code, 200)

    def test_every_template_that_uses_trans_loads_i18n(self):
        import os
        import re
        from django.conf import settings
        offenders = []
        for root in settings.TEMPLATES[0]['DIRS']:
            for folder, _dirs, files in os.walk(root):
                for name in files:
                    if not name.endswith('.html'):
                        continue
                    path = os.path.join(folder, name)
                    with open(path, encoding='utf-8') as handle:
                        text = handle.read()
                    if re.search(r'{%\s*(trans|blocktrans|blocktranslate)\b', text) and not re.search(r'{%\s*load[^%]*\bi18n\b', text):
                        offenders.append(os.path.relpath(path, root))
        self.assertEqual(offenders, [])


class TestDocumentedNumbers(ExpenseTestBase):
    """guide-src/03-transactions-expenses quotes these figures; fail loudly if they drift."""

    def test_numbers_quoted_in_the_guide(self):
        from expenses.composer import USUAL_WINDOW_DAYS
        from expenses.views.expenses import ExpenseListView
        self.assertEqual(UNDO_WINDOW, timedelta(minutes=10))            # "Undo for ten minutes"
        self.assertEqual(LARGE_AMOUNT_THRESHOLDS['₹'], 100000)           # "₹1,00,000 or more"
        self.assertEqual(USUAL_WINDOW_DAYS, 60)                          # "last two months"
        self.assertEqual(ExpenseListView.paginate_by, 20)                # "20 to a page"
        from expenses.models import Expense as E
        self.assertEqual([k for k, _ in E.PAYMENT_OPTIONS], ['Cash', 'Credit Card', 'Debit Card', 'UPI', 'NetBanking'])

    def test_usual_needs_exactly_two_repeats(self):
        self.expense('10', description='Chai')
        self.assertEqual(usual_expenses(self.user), [])
        cache.clear()
        self.expense('10', description='Chai')
        self.assertEqual(len(usual_expenses(self.user)), 1)
