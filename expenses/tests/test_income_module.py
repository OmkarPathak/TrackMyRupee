"""Behavioural tests for the Income module: model maths, form, list totals, create/edit/delete
views, and the "make this recurring" shortcut."""

import calendar
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.forms import IncomeForm
from expenses.models import (
    INCOME_GROUP_TYPES,
    Account,
    FinancialAuditLog,
    FXRate,
    Income,
    RecurringTransaction,
    UserProfile,
)
from expenses.views.mixins import process_user_recurring_transactions
from finance_tracker.plans import get_limit

SOURCE_TYPES = [value for value, _label in Income.SOURCE_TYPE_CHOICES]


def today():
    return timezone.localdate()


def seed_fx():
    rows = {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125', ('EUR', 'INR'): '90', ('INR', 'EUR'): '0.011111'}
    for (src, dst), rate in rows.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': Decimal(rate), 'source': 'test'})
    cache.clear()


class IncomeTestBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('inc-user', self.tier)
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=Decimal('1000.00'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=Decimal('5000.00'), currency='₹')
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        return user

    def income(self, amount='100', account='default', **kw):
        values = dict(user=self.user, date=today(), amount=Decimal(str(amount)), description='Pay',
                      source_type='Salary', currency='₹', account=self.cash if account == 'default' else account)
        values.update(kw)
        return Income.objects.create(**values)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class TestIncomeModel(IncomeTestBase):
    def test_create_credits_the_account(self):
        self.income('250.50')
        self.assertEqual(self.bal(self.cash), Decimal('1250.50'))

    def test_no_account_means_no_balance_change(self):
        self.income('250', account=None)
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))

    def test_same_currency_base_amount(self):
        i = self.income('99.99')
        self.assertEqual((i.exchange_rate, i.base_amount), (Decimal('1.0'), Decimal('99.99')))

    def test_source_defaults_to_the_source_type(self):
        for source_type in SOURCE_TYPES:
            with self.subTest(source_type=source_type):
                self.assertEqual(self.income('1', source_type=source_type, account=None).source, source_type)

    def test_an_explicit_source_is_kept_and_stripped(self):
        self.assertEqual(self.income('1', source='  HDFC dividend ', account=None).source, 'HDFC dividend')

    def test_update_applies_only_the_difference(self):
        i = self.income('100')
        i.amount = Decimal('160')
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1160.00'))
        i.amount = Decimal('40')
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1040.00'))

    def test_update_account_moves_the_money(self):
        i = self.income('300')
        i.account = self.bank
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        self.assertEqual(self.bal(self.bank), Decimal('5300.00'))

    def test_removing_and_adding_an_account(self):
        i = self.income('300')
        i.account = None
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        i.account = self.cash
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1300.00'))

    def test_resaving_is_balance_neutral(self):
        i = self.income('300')
        for _ in range(3):
            i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1300.00'))

    def test_delete_takes_the_money_back(self):
        i = self.income('300')
        i.delete()
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        self.assertFalse(Income.objects.filter(pk=i.pk).exists())

    def test_dedup_key_unique_per_user_null_is_free(self):
        self.income(client_dedup_key='k1', account=None)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.income(client_dedup_key='k1', account=None)
        other = self.make_user('other-dedup')
        Income.objects.create(user=other, date=today(), amount=1, source_type='Salary', client_dedup_key='k1')
        self.income(account=None)
        self.income(account=None)

    def test_audit_log(self):
        i = self.income('100')
        i.amount = Decimal('120')
        i.save()
        logs = list(FinancialAuditLog.objects.filter(model_name='Income', object_id=i.id).order_by('timestamp', 'id'))
        self.assertEqual([x.action for x in logs], ['CREATE', 'UPDATE'])
        self.assertEqual(logs[1].diff['before'], {'amount': '100.00', 'source': 'Salary'})
        self.assertEqual(logs[1].diff['after'], {'amount': '120', 'source': 'Salary'})

    @override_settings(SOFT_DELETE_ENABLED=True)
    def test_soft_delete_restores_balance_once(self):
        i = self.income('300')
        i.delete()
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        self.assertTrue(Income.objects.get(pk=i.pk).is_deleted)
        self.assertFalse(Income.active.filter(pk=i.pk).exists())

    def test_monthly_summary_manager(self):
        self.income('100', account=None)
        self.income('50', account=None)
        self.income('999', account=None, date=today().replace(day=1) - timedelta(days=1))
        other = self.make_user('other-sum')
        Income.objects.create(user=other, date=today(), amount=5000, source_type='Salary')
        summary = Income.objects.get_monthly_summary(self.user, today().year, today().month)
        self.assertEqual((summary['total'], summary['count']), (Decimal('150.00'), 2))

    def test_group_types_partition_the_source_types(self):
        grouped = [t for types in INCOME_GROUP_TYPES.values() for t in types]
        self.assertEqual(sorted(grouped), sorted(SOURCE_TYPES))
        self.assertEqual(len(grouped), len(set(grouped)))


class TestIncomeModelMultiCurrency(IncomeTestBase):
    def setUp(self):
        super().setUp()
        seed_fx()
        self.usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT',
                                          balance=Decimal('100'), currency='$')

    def test_foreign_income_stores_rate_and_base_amount(self):
        i = self.income('10', currency='$', account=None)
        self.assertEqual((i.exchange_rate, i.base_amount), (Decimal('80'), Decimal('800.00')))

    def test_credited_in_the_accounts_own_currency(self):
        self.income('800', account=self.usd)            # ₹800 -> $10
        self.assertEqual(self.bal(self.usd), Decimal('110.00'))
        self.income('5', currency='$', account=self.cash)  # $5 -> ₹400
        self.assertEqual(self.bal(self.cash), Decimal('1400.00'))

    def test_update_and_delete_reverse_at_the_converted_amount(self):
        i = self.income('10', currency='$', account=self.cash)
        i.amount = Decimal('5')
        i.save()
        self.assertEqual(self.bal(self.cash), Decimal('1400.00'))
        self.assertEqual(i.base_amount, Decimal('400.00'))
        i.delete()
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))

    def test_non_rupee_profile_uses_its_own_base_currency(self):
        usd_user = self.make_user('usd-income', currency='$')
        i = Income.objects.create(user=usd_user, date=today(), amount=Decimal('800'), source_type='Salary', currency='₹')
        self.assertEqual((i.exchange_rate, i.base_amount), (Decimal('0.0125'), Decimal('10.00')))


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
class TestIncomeForm(IncomeTestBase):
    def data(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '5000', 'currency': '₹', 'account': str(self.cash.id),
                'source_type': 'Salary', 'description': 'October pay'}
        data.update(overrides)
        return data

    def form(self, user=None, **overrides):
        return IncomeForm(self.data(**overrides), user=user or self.user)

    def test_valid(self):
        self.assertTrue(self.form().is_valid())

    def test_required_fields(self):
        form = IncomeForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        for field in ('date', 'amount', 'source_type'):
            self.assertIn(field, form.errors)
        self.assertNotIn('description', form.errors)
        self.assertIn('account', form.errors)

    def test_amount_must_be_positive_and_in_range(self):
        for bad in ('0', '-1', '-0.01', 'abc', '1.234', '1' * 14):
            self.assertIn('amount', self.form(amount=bad).errors, bad)
        self.assertNotIn('amount', self.form(amount='0.01').errors)

    def test_future_dates_rejected_tomorrow_slack_allowed(self):
        self.assertIn('date', self.form(date=(today() + timedelta(days=2)).isoformat()).errors)
        self.assertTrue(self.form(date=(today() + timedelta(days=1)).isoformat()).is_valid())
        self.assertTrue(self.form(date=(today() - timedelta(days=3000)).isoformat()).is_valid())

    def test_every_source_type_is_accepted_and_unknown_rejected(self):
        for source_type in SOURCE_TYPES:
            self.assertTrue(self.form(source_type=source_type).is_valid(), source_type)
        self.assertIn('source_type', self.form(source_type='Lottery').errors)

    def test_currency_must_be_known(self):
        self.assertIn('currency', self.form(currency='XXX').errors)

    def test_account_must_be_own_and_active(self):
        other = self.make_user('other-form')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertIn('account', self.form(account=str(theirs.id)).errors)
        self.bank.is_active = False
        self.bank.save()
        self.assertIn('account', self.form(account=str(self.bank.id)).errors)

    def test_free_tier_only_offers_unlocked_accounts(self):
        free = self.make_user('free-form', tier='FREE')
        limit = get_limit('FREE', 'accounts')
        accounts = [Account.objects.create(user=free, name=f'A{i}', account_type='CASH_WALLET', balance=0, currency='₹')
                    for i in range(limit + 1)]
        offered = set(IncomeForm(user=free).fields['account'].queryset.values_list('id', flat=True))
        self.assertEqual(offered, {a.id for a in accounts[:limit]})
        self.assertIn('account', self.form(user=free, account=str(accounts[-1].id)).errors)

    def test_defaults(self):
        form = IncomeForm(user=self.user)
        self.assertEqual(form.fields['currency'].initial, '₹')
        self.assertEqual(form.fields['account'].initial, self.cash)
        self.assertTrue(form.fields['client_dedup_key'].initial)

    def test_recurring_requires_a_frequency(self):
        self.assertIn('frequency', self.form(add_to_recurring='on').errors)
        self.assertIn('frequency', self.form(add_to_recurring='on', frequency='').errors)
        self.assertIn('frequency', self.form(add_to_recurring='on', frequency='HOURLY').errors)
        self.assertTrue(self.form(add_to_recurring='on', frequency='MONTHLY').is_valid())
        self.assertTrue(self.form().is_valid())  # frequency is irrelevant when not recurring

    def test_editing_source_type_updates_an_auto_derived_source(self):
        i = self.income('10', account=None)
        form = IncomeForm(self.data(source_type='Business', amount='10'), instance=i, user=self.user)
        self.assertTrue(form.is_valid())
        saved = form.save()
        self.assertEqual((saved.source_type, saved.source), ('Business', 'Business'))

    def test_editing_source_type_keeps_a_custom_source(self):
        i = self.income('10', account=None, source='HDFC dividend', source_type='Investment Returns')
        form = IncomeForm(self.data(source_type='Business', amount='10'), instance=i, user=self.user)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.save().source, 'HDFC dividend')

    def test_unchanged_source_type_leaves_source_alone(self):
        i = self.income('10', account=None)
        form = IncomeForm(self.data(amount='11'), instance=i, user=self.user)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.save().source, 'Salary')


# ---------------------------------------------------------------------------
# List view
# ---------------------------------------------------------------------------
class TestIncomeListView(IncomeTestBase):
    url = reverse('income-list')

    def ids(self, response):
        return [i.id for i in response.context['incomes']]

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_only_own_income(self):
        mine = self.income('10')
        other = self.make_user('other-list')
        Income.objects.create(user=other, date=today(), amount=5, source_type='Salary', description='secret')
        response = self.client.get(self.url)
        self.assertEqual(self.ids(response), [mine.id])
        self.assertNotContains(response, 'secret')

    def test_default_period_is_this_month_and_all_shows_everything(self):
        now = self.income('10')
        self.income('20', date=today().replace(day=1) - timedelta(days=5))
        self.assertEqual(self.ids(self.client.get(self.url)), [now.id])
        self.assertEqual(len(self.ids(self.client.get(self.url, {'time_period': 'all'}))), 2)

    def test_custom_range(self):
        old = self.income('10', date=today() - timedelta(days=100))
        self.income('20')
        params = {'time_period': 'custom', 'start_date': (today() - timedelta(days=101)).isoformat(),
                  'end_date': (today() - timedelta(days=99)).isoformat()}
        self.assertEqual(self.ids(self.client.get(self.url, params)), [old.id])

    def test_totals_split_by_group_and_use_base_amount(self):
        self.income('1000', source_type='Salary')
        self.income('500', source_type='Freelance / Consulting')
        self.income('200', source_type='Business')
        self.income('300', source_type='Investment Returns')
        self.income('50', source_type='Rental Income')
        self.income('25', source_type='Cashback & Rewards')
        self.income('10', source_type='Refund / Reimbursement')
        self.income('5', source_type='Other')
        self.income('9999', source_type='Salary', date=today() - timedelta(days=400))
        ctx = self.client.get(self.url).context
        self.assertEqual(ctx['filtered_count'], 8)
        self.assertEqual(ctx['earned_total'], Decimal('1700.00'))
        self.assertEqual(ctx['passive_total'], Decimal('350.00'))
        self.assertEqual(ctx['one_off_total'], Decimal('40.00'))
        self.assertEqual(ctx['filtered_amount'], Decimal('2090.00'))
        self.assertEqual(ctx['earned_total'] + ctx['passive_total'] + ctx['one_off_total'], ctx['filtered_amount'])

    def test_foreign_income_counts_at_its_converted_value(self):
        seed_fx()
        self.income('10', currency='$', account=None)
        self.income('20')
        self.assertEqual(self.client.get(self.url).context['earned_total'], Decimal('820.00'))

    def test_empty_state_totals_are_zero(self):
        ctx = self.client.get(self.url).context
        for key in ('filtered_amount', 'earned_total', 'passive_total', 'one_off_total'):
            self.assertEqual(ctx[key], Decimal('0.00'), key)
        self.assertEqual(ctx['filtered_count'], 0)

    def test_filters(self):
        sal = self.income('10', source_type='Salary', account=self.cash)
        rent = self.income('20', source_type='Rental Income', account=self.bank)
        gift = self.income('30', source_type='Other', account=self.cash)
        get = lambda **p: set(self.ids(self.client.get(self.url, p)))
        self.assertEqual(get(source_type='Salary'), {sal.id})
        self.assertEqual(get(source_type=['Salary', 'Other']), {sal.id, gift.id})
        self.assertEqual(get(income_group='EARNED'), {sal.id})
        self.assertEqual(get(income_group='PASSIVE'), {rent.id})
        self.assertEqual(get(income_group='ONE_OFF'), {gift.id})
        self.assertEqual(get(income_group=['EARNED', 'PASSIVE']), {sal.id, rent.id})
        self.assertEqual(get(account=str(self.bank.id)), {rent.id})
        self.assertEqual(get(source_type='Salary', account=str(self.bank.id)), set())
        self.assertEqual(get(income_group='BOGUS'), {sal.id, rent.id, gift.id})

    def test_amount_range(self):
        for amount in ('499.99', '500', '10000.01'):
            self.income(amount)
        get = lambda label: {i.base_amount for i in self.client.get(self.url, {'amount_range': label}).context['incomes']}
        self.assertEqual(get('Under ₹500'), {Decimal('499.99')})
        self.assertEqual(get('₹500 to ₹2,000'), {Decimal('500')})
        self.assertEqual(get('Over ₹10,000'), {Decimal('10000.01')})

    def test_search(self):
        a = self.income('10', description='Acme invoice 42')
        self.income('20', description='Other')
        self.assertEqual(self.ids(self.client.get(self.url, {'search': 'ACME'})), [a.id])

    def test_sorting(self):
        old_big = self.income('900', date=today() - timedelta(days=2))
        new_small = self.income('100')
        order = lambda s: self.ids(self.client.get(self.url, {'sort': s}))
        self.assertEqual(order('date_desc'), [new_small.id, old_big.id])
        self.assertEqual(order('date_asc'), [old_big.id, new_small.id])
        self.assertEqual(order('amount_desc'), [old_big.id, new_small.id])
        self.assertEqual(order('amount_asc'), [new_small.id, old_big.id])

    def test_pagination(self):
        for _ in range(25):
            self.income('1', account=None)
        self.assertEqual(len(self.client.get(self.url).context['incomes']), 20)
        self.assertEqual(len(self.client.get(self.url, {'page': 2}).context['incomes']), 5)

    def test_garbage_params_do_not_crash(self):
        self.income('10')
        for params in ({'account': 'abc'}, {'sort': 'zzz'}, {'time_period': 'zzz'}, {'amount_range': 'zzz'},
                       {'time_period': 'custom', 'start_date': 'nope'}):
            with self.subTest(params=params):
                self.assertEqual(self.client.get(self.url, params).status_code, 200)

    def test_active_filter_count(self):
        ctx = self.client.get(self.url, {'search': 'x', 'time_period': 'all', 'source_type': 'Salary',
                                         'income_group': 'EARNED', 'sort': 'amount_asc'}).context
        self.assertEqual(ctx['active_filters_count'], 5)
        self.assertEqual(self.client.get(self.url).context['active_filters_count'], 0)

    def test_htmx_partial(self):
        full = [t.name for t in self.client.get(self.url).templates]
        partial = [t.name for t in self.client.get(self.url, HTTP_HX_REQUEST='true').templates]
        self.assertIn('expenses/income_list.html', full)
        self.assertIn('expenses/partials/_income_list.html', partial)
        self.assertNotIn('expenses/income_list.html', partial)

    def test_recurring_sources_are_reported_with_their_frequency(self):
        RecurringTransaction.objects.create(user=self.user, transaction_type='INCOME', source='Salary', amount=1,
                                            currency='₹', frequency='MONTHLY', start_date=today(), is_active=True)
        RecurringTransaction.objects.create(user=self.user, transaction_type='INCOME', source='Old gig', amount=1,
                                            currency='₹', frequency='WEEKLY', start_date=today(), is_active=False)
        RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', source='Rent', amount=1,
                                            currency='₹', frequency='MONTHLY', start_date=today(), is_active=True)
        self.assertEqual(self.client.get(self.url).context['recurring_data'], {'Salary': 'MONTHLY'})


class TestIncomeSparkline(IncomeTestBase):
    url = reverse('income-list')

    def test_six_chronological_months_ending_this_month(self):
        data = self.client.get(self.url).context['sparkline_data']
        self.assertEqual(len(data), 6)
        expected = [today() - relativedelta(months=n) for n in range(5, -1, -1)]
        self.assertEqual([d['month_name'] for d in data],
                         [f"{calendar.month_name[m.month][:3]} '{str(m.year)[2:]}" for m in expected])

    def test_counts_only_earned_income_per_month(self):
        self.income('1000', source_type='Salary')
        self.income('500', source_type='Business')
        self.income('777', source_type='Rental Income')       # passive: excluded
        self.income('555', source_type='Other')               # one-off: excluded
        last = today().replace(day=1) - timedelta(days=1)
        self.income('300', source_type='Freelance / Consulting', date=last)
        self.income('999', source_type='Salary', date=today() - relativedelta(months=7))  # outside window
        data = self.client.get(self.url).context['sparkline_data']
        self.assertEqual([d['amount'] for d in data], [0, 0, 0, 0, 300.0, 1500.0])

    def test_months_without_income_are_zero_and_line_scales(self):
        self.income('100')
        ctx = self.client.get(self.url).context
        ys = [d['y'] for d in ctx['sparkline_data']]
        self.assertEqual(ys[-1], 2.0)               # max -> top (padding 2)
        self.assertEqual(ys[0], 28.0)               # min -> bottom
        self.assertTrue(ctx['sparkline_path'].startswith('M '))
        self.assertEqual(ctx['sparkline_path'].count('L'), 5)

    def test_flat_line_when_every_month_is_equal(self):
        data = self.client.get(self.url).context['sparkline_data']
        self.assertEqual({d['y'] for d in data}, {15.0})

    def test_uses_converted_amounts(self):
        seed_fx()
        self.income('10', currency='$', account=None)
        self.assertEqual(self.client.get(self.url).context['sparkline_data'][-1]['amount'], 800.0)

    def test_other_users_income_is_not_charted(self):
        other = self.make_user('other-spark')
        Income.objects.create(user=other, date=today(), amount=5000, source_type='Salary')
        self.assertEqual(self.client.get(self.url).context['sparkline_data'][-1]['amount'], 0)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class TestIncomeCreateView(IncomeTestBase):
    url = reverse('income-create')

    def payload(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '5000', 'currency': '₹', 'account': str(self.cash.id),
                'source_type': 'Salary', 'description': 'October pay'}
        data.update(overrides)
        return data

    def test_login_required_and_get_renders(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_creates_credits_account_and_redirects(self):
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, reverse('income-list'))
        i = Income.objects.get()
        self.assertEqual((i.user, i.amount, i.source, i.source_type, i.account, i.currency, i.description),
                         (self.user, Decimal('5000.00'), 'Salary', 'Salary', self.cash, '₹', 'October pay'))
        self.assertEqual(self.bal(self.cash), Decimal('6000.00'))
        self.assertIn('Income record added successfully!', self.messages(response))

    def test_invalid_input_creates_nothing(self):
        for override in ({'amount': '0'}, {'amount': '-5'}, {'amount': ''}, {'source_type': ''},
                         {'date': (today() + timedelta(days=30)).isoformat()}, {'date': ''}):
            with self.subTest(override=override):
                self.assertEqual(self.client.post(self.url, self.payload(**override)).status_code, 200)
        self.assertEqual(Income.objects.count(), 0)
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))

    def test_cannot_credit_someone_elses_account(self):
        other = self.make_user('other-acct')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=10, currency='₹')
        self.assertEqual(self.client.post(self.url, self.payload(account=str(theirs.id))).status_code, 200)
        self.assertEqual(Income.objects.count(), 0)
        self.assertEqual(self.bal(theirs), Decimal('10.00'))

    def test_double_submit_with_the_same_key_creates_one(self):
        payload = self.payload(client_dedup_key=str(uuid.uuid4()))
        self.client.post(self.url, payload)
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Income.objects.count(), 1)
        self.assertEqual(self.bal(self.cash), Decimal('6000.00'))
        self.assertTrue(any('already exists' in m for m in self.messages(response)))

    def test_foreign_currency(self):
        seed_fx()
        self.client.post(self.url, self.payload(currency='$', amount='10'))
        i = Income.objects.get()
        self.assertEqual((i.currency, i.base_amount), ('$', Decimal('800.00')))
        self.assertEqual(self.bal(self.cash), Decimal('1800.00'))

    def test_currency_conversion_failure_is_reported(self):
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.client.post(self.url, self.payload(currency='$'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Income.objects.count(), 0)
        self.assertTrue(any('currency conversion failed' in m for m in self.messages(response)))

    def test_next_param_only_for_same_site(self):
        self.assertEqual(self.client.post(self.url + '?next=/dashboard/', self.payload()).url, '/dashboard/')
        self.assertEqual(self.client.post(self.url, self.payload(next='/transactions/')).url, '/transactions/')
        self.assertEqual(self.client.post(self.url, self.payload(next='https://evil.example/')).url, reverse('income-list'))
        self.assertEqual(self.client.post(self.url, self.payload(next='//evil.example/')).url, reverse('income-list'))

    def test_each_source_type_round_trips(self):
        for source_type in SOURCE_TYPES:
            self.client.post(self.url, self.payload(source_type=source_type, client_dedup_key=str(uuid.uuid4())))
        self.assertEqual(sorted(Income.objects.values_list('source_type', flat=True)), sorted(SOURCE_TYPES))


# ---------------------------------------------------------------------------
# Recurring shortcut
# ---------------------------------------------------------------------------
class TestIncomeMakeRecurring(IncomeTestBase):
    url = reverse('income-create')

    def post(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '45000', 'currency': '₹', 'account': str(self.cash.id),
                'source_type': 'Freelance / Consulting', 'description': 'Retainer', 'add_to_recurring': 'on',
                'frequency': 'MONTHLY', 'client_dedup_key': str(uuid.uuid4())}
        data.update(overrides)
        return self.client.post(self.url, data)

    def schedules(self):
        return RecurringTransaction.objects.filter(user=self.user, transaction_type='INCOME')

    def test_creates_a_matching_schedule(self):
        response = self.post()
        rt = self.schedules().get()
        self.assertEqual((rt.source, rt.amount, rt.currency, rt.account, rt.frequency, rt.start_date,
                          rt.description, rt.is_active),
                         ('Freelance / Consulting', Decimal('45000.00'), '₹', self.cash, 'MONTHLY', today(),
                          'Retainer', True))
        self.assertTrue(any('recurring income subscription' in m for m in self.messages(response)))

    def test_not_requested_means_no_schedule(self):
        self.post(add_to_recurring='')
        self.assertEqual(self.schedules().count(), 0)

    def test_frequency_is_required_and_nothing_is_saved_without_it(self):
        response = self.post(frequency='')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Income.objects.count(), 0)
        self.assertEqual(self.schedules().count(), 0)

    def test_a_missing_frequency_cannot_wipe_an_existing_schedule(self):
        self.post()
        self.post(frequency='')
        self.assertEqual(self.schedules().get().frequency, 'MONTHLY')

    def test_every_frequency_is_supported(self):
        frequencies = [f for f, _label in RecurringTransaction.FREQUENCY_CHOICES]
        for frequency, source_type in zip(frequencies, SOURCE_TYPES):  # one source type per schedule
            self.post(frequency=frequency, source_type=source_type, description=frequency)
        self.assertEqual({rt.frequency for rt in self.schedules()}, set(frequencies))
        for rt in self.schedules():
            rt.refresh_from_db()
            self.assertGreaterEqual(rt.next_due_date, today(), rt.frequency)

    def test_one_active_schedule_per_source_is_refreshed_not_duplicated(self):
        self.post()
        past = (today() - timedelta(days=40)).isoformat()
        response = self.post(amount='50000', frequency='WEEKLY', date=past, description='Raise')
        rt = self.schedules().get()
        self.assertEqual((rt.amount, rt.frequency, rt.start_date, rt.description),
                         (Decimal('50000.00'), 'WEEKLY', today() - timedelta(days=40), 'Raise'))
        self.assertTrue(any('schedule updated' in m for m in self.messages(response)))

    def test_refreshing_a_past_dated_schedule_leaves_nothing_due_in_the_past(self):
        self.post()
        self.post(date=(today() - timedelta(days=40)).isoformat())
        rt = self.schedules().get()
        rt.refresh_from_db()
        self.assertGreater(rt.next_due_date, today())

    def test_a_different_source_gets_its_own_schedule(self):
        self.post()
        self.post(source_type='Salary', description='Salary pay')
        self.assertEqual(self.schedules().count(), 2)

    def test_identical_schedule_for_another_source_is_reported_not_a_500(self):
        """The DB rule ignores `source`; the second income must still be saved, with a warning."""
        self.post()
        response = self.post(source_type='Salary')          # same amount, description, frequency, date
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Income.objects.count(), 2)
        self.assertEqual(self.schedules().count(), 1)
        self.assertTrue(any('no new schedule was created' in m for m in self.messages(response)))

    def test_an_inactive_schedule_is_not_reused(self):
        self.post()
        self.schedules().update(is_active=False)
        self.post()
        self.assertEqual(self.schedules().count(), 2)
        self.assertEqual(self.schedules().filter(is_active=True).count(), 1)

    def test_other_users_schedules_are_never_touched(self):
        other = self.make_user('other-rec')
        theirs = RecurringTransaction.objects.create(user=other, transaction_type='INCOME', source='Freelance / Consulting',
                                                     amount=1, currency='₹', frequency='MONTHLY', start_date=today(), is_active=True)
        self.post()
        theirs.refresh_from_db()
        self.assertEqual(theirs.amount, Decimal('1.00'))
        self.assertEqual(self.schedules().count(), 1)

    def test_processing_does_not_post_a_duplicate_of_the_income_just_entered(self):
        self.post()
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(self.bal(self.cash), Decimal('46000.00'))

    def test_schedule_posts_the_next_income_when_due(self):
        self.post(date=(today() - timedelta(days=40)).isoformat())
        before = Income.objects.count()
        process_user_recurring_transactions(self.user, force=True)
        self.assertGreater(Income.objects.count(), before)
        rt = self.schedules().get()
        rt.refresh_from_db()
        self.assertGreater(rt.next_due_date, today())

    def test_editing_an_income_can_also_create_a_schedule(self):
        i = self.income('100', source_type='Business')
        response = self.client.post(reverse('income-edit', kwargs={'pk': i.pk}), {
            'date': i.date.isoformat(), 'amount': '100', 'currency': '₹', 'account': str(self.cash.id),
            'source_type': 'Business', 'description': 'Pay', 'add_to_recurring': 'on', 'frequency': 'QUARTERLY'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.schedules().get().frequency, 'QUARTERLY')


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------
class TestIncomeUpdateView(IncomeTestBase):
    def post(self, i, **overrides):
        data = {'date': i.date.isoformat(), 'amount': str(i.amount), 'currency': i.currency,
                'account': str(i.account_id or ''), 'source_type': i.source_type, 'description': i.description or ''}
        data.update(overrides)
        return self.client.post(reverse('income-edit', kwargs={'pk': i.pk}), data)

    def test_get_by_pk_and_uuid(self):
        i = self.income('100')
        for key in (i.pk, i.uuid):
            self.assertEqual(self.client.get(reverse('income-edit', kwargs={'pk': key})).status_code, 200)

    def test_other_users_income_is_404(self):
        other = self.make_user('other-edit')
        theirs = Income.objects.create(user=other, date=today(), amount=5, source_type='Salary')
        self.assertEqual(self.client.get(reverse('income-edit', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.post(theirs, amount='1').status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.amount, Decimal('5.00'))

    def test_update_changes_fields_and_balances(self):
        i = self.income('100')
        response = self.post(i, amount='175.25', account=str(self.bank.id), source_type='Business', description='New')
        self.assertRedirects(response, reverse('income-list'))
        i.refresh_from_db()
        self.assertEqual((i.amount, i.account, i.source_type, i.source, i.description),
                         (Decimal('175.25'), self.bank, 'Business', 'Business', 'New'))
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        self.assertEqual(self.bal(self.bank), Decimal('5175.25'))
        self.assertIn('Income record updated successfully!', self.messages(response))

    def test_invalid_updates_change_nothing(self):
        i = self.income('100')
        for override in ({'amount': '0'}, {'amount': '-500'}, {'amount': 'x'}, {'date': (today() + timedelta(days=9)).isoformat()}):
            with self.subTest(override=override):
                self.assertEqual(self.post(i, **override).status_code, 200)
        i.refresh_from_db()
        self.assertEqual((i.amount, i.date), (Decimal('100.00'), today()))
        self.assertEqual(self.bal(self.cash), Decimal('1100.00'))

    def test_cannot_move_income_to_someone_elses_account(self):
        other = self.make_user('other-move')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=10, currency='₹')
        i = self.income('100')
        self.assertEqual(self.post(i, account=str(theirs.id)).status_code, 200)
        self.assertEqual(self.bal(theirs), Decimal('10.00'))

    def test_next_param(self):
        i = self.income('100')
        self.assertEqual(self.post(i, next='/transactions/').url, '/transactions/')
        self.assertEqual(self.post(i, next='https://evil.example/').url, reverse('income-list'))

    def test_conversion_failure_is_reported(self):
        i = self.income('100')
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.post(i, currency='$')
        self.assertEqual(response.status_code, 200)
        i.refresh_from_db()
        self.assertEqual(i.currency, '₹')


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------
class TestIncomeDeleteView(IncomeTestBase):
    def test_confirmation_page_then_delete_restores_balance(self):
        i = self.income('300')
        url = reverse('income-delete', kwargs={'pk': i.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(Income.objects.filter(pk=i.pk).exists())
        response = self.client.post(url)
        self.assertRedirects(response, reverse('income-list'))
        self.assertFalse(Income.objects.filter(pk=i.pk).exists())
        self.assertEqual(self.bal(self.cash), Decimal('1000.00'))
        self.assertIn('Income record deleted successfully.', self.messages(response))

    def test_delete_by_uuid(self):
        i = self.income('300')
        self.client.post(reverse('income-delete', kwargs={'pk': i.uuid}))
        self.assertFalse(Income.objects.filter(pk=i.pk).exists())

    def test_cannot_delete_someone_elses_income(self):
        other = self.make_user('other-del')
        theirs = Income.objects.create(user=other, date=today(), amount=5, source_type='Salary')
        self.assertEqual(self.client.post(reverse('income-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertTrue(Income.objects.filter(pk=theirs.pk).exists())

    def test_next_redirect_only_for_same_site(self):
        for target, expected in (('/transactions/', '/transactions/'), ('https://evil.example/', reverse('income-list')),
                                 ('//evil.example/', reverse('income-list'))):
            i = self.income('1', account=None)
            with self.subTest(target=target):
                response = self.client.post(reverse('income-delete', kwargs={'pk': i.pk}) + f'?next={target}')
                self.assertEqual(response.url, expected)
                self.assertFalse(Income.objects.filter(pk=i.pk).exists())

    def test_login_required(self):
        i = self.income('1')
        self.client.logout()
        self.assertEqual(self.client.post(reverse('income-delete', kwargs={'pk': i.pk})).status_code, 302)
        self.assertTrue(Income.objects.filter(pk=i.pk).exists())


# ---------------------------------------------------------------------------
# Documented behaviour
# ---------------------------------------------------------------------------
class TestIncomeDocs(IncomeTestBase):
    def test_source_types_match_the_guide(self):
        self.assertEqual(SOURCE_TYPES, ['Salary', 'Freelance / Consulting', 'Business', 'Investment Returns',
                                        'Rental Income', 'Cashback & Rewards', 'Refund / Reimbursement', 'Other'])

    def test_income_groups_match_the_guide(self):
        self.assertEqual(INCOME_GROUP_TYPES['EARNED'], ['Salary', 'Freelance / Consulting', 'Business'])
        self.assertEqual(INCOME_GROUP_TYPES['PASSIVE'], ['Investment Returns', 'Rental Income'])
        self.assertEqual(INCOME_GROUP_TYPES['ONE_OFF'], ['Cashback & Rewards', 'Refund / Reimbursement', 'Other'])

    def test_recurring_toggle_label(self):
        self.assertEqual(str(IncomeForm(user=self.user).fields['add_to_recurring'].label), 'Make this a recurring income')


class TestIncomeInSavingsRate(IncomeTestBase):
    """Guide: Cashback & Rewards and Refund / Reimbursement are left out of the savings-rate denominator."""

    def metrics(self):
        from expenses.models import Expense
        from expenses.services import SalaryAnalysisService
        Expense.objects.create(user=self.user, date=today(), amount=Decimal('400'), description='x', category='Food')
        return SalaryAnalysisService.calculate_salary_cycle_metrics(self.user, today())

    def test_cashback_and_refunds_do_not_inflate_the_denominator(self):
        self.income('1000', source_type='Salary', account=None)
        self.income('200', source_type='Cashback & Rewards', account=None)
        self.income('100', source_type='Refund / Reimbursement', account=None)
        m = self.metrics()
        self.assertEqual(m['total_income'], 1300.0)            # all income counts as money in
        self.assertEqual(m['savings'], 900.0)                  # 1300 - 400
        self.assertEqual(m['savings_rate'], 90.0)              # 900 / 1000, not 900 / 1300

    def test_every_other_source_type_is_in_the_denominator(self):
        for source_type in ('Salary', 'Freelance / Consulting', 'Business', 'Investment Returns', 'Rental Income', 'Other'):
            self.income('100', source_type=source_type, account=None)
        m = self.metrics()
        self.assertEqual(m['savings_rate'], round((600 - 400) / 600 * 100, 2))
