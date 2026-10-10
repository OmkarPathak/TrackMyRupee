"""Behavioural tests for Capital Events: model balance/flag maths, form, list, create / edit / delete,
conversion to and from expenses, loan linking, and how the three flags steer the dashboard."""

import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.forms import CapitalEventForm
from expenses.models import (
    CAPITAL_SUBTYPE_CHOICES,
    Account,
    CapitalEvent,
    Expense,
    FinancialAuditLog,
    FXRate,
    Loan,
    LoanRepayment,
    UserProfile,
)
from expenses.services import LoanService

D = Decimal
SUBTYPES = [value for value, _label in CAPITAL_SUBTYPE_CHOICES]


def today():
    return timezone.localdate()


def seed_fx():
    for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
        FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                        defaults={'rate': D(rate), 'source': 'test'})
    cache.clear()


class CapBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('cap-user', self.tier)
        self.client.force_login(self.user)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000.00'), currency='₹')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000.00'), currency='₹')
        cache.clear()

    def make_user(self, name, tier='PRO', currency='₹'):
        user = User.objects.create_user(username=name, password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': currency})
        profile.tier = tier
        profile.currency = currency
        profile.save()
        user.refresh_from_db()
        return user

    def event(self, amount='1000', account='default', **kw):
        values = dict(user=self.user, date=today(), amount=D(str(amount)), subtype='large_purchase', currency='₹',
                      note='Sofa', account=self.cash if account == 'default' else account)
        values.update(kw)
        return CapitalEvent.objects.create(**values)

    def bal(self, account):
        account.refresh_from_db()
        return account.balance

    def loan(self, principal='100000'):
        return Loan.objects.create(user=self.user, name='Home', loan_type='HOME', initial_principal=D(principal),
                                   duration_months=120, start_date=today() - timedelta(days=400), is_active=True)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
class TestCapitalEventModel(CapBase):
    def test_defaults(self):
        e = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('5'))
        self.assertEqual((e.subtype, e.note, e.currency, e.exclude_from_averages, e.exclude_from_budget,
                          e.include_in_net_worth, e.is_deleted), ('other', '', '₹', True, True, True, False))

    def test_subtypes_match_the_guide(self):
        self.assertEqual(SUBTYPES, ['loan_down_payment', 'loan_prepayment', 'large_purchase', 'medical_lump_sum',
                                    'gift_given', 'gift_received', 'investment_lump_sum', 'other'])

    def test_create_debits_the_account(self):
        self.event('2500.50')
        self.assertEqual(self.bal(self.cash), D('97499.50'))

    def test_no_account_no_balance_change(self):
        self.event('2500', account=None)
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_an_event_kept_out_of_cash_flow_does_not_touch_the_balance(self):
        e = self.event('2500', include_in_net_worth=False)
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        e.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_same_currency_base_amount(self):
        e = self.event('99.99')
        self.assertEqual((e.exchange_rate, e.base_amount), (D('1.0'), D('99.99')))

    def test_update_amount_applies_only_the_difference(self):
        e = self.event('1000')
        e.amount = D('1600')
        e.save()
        self.assertEqual(self.bal(self.cash), D('98400.00'))
        e.amount = D('400')
        e.save()
        self.assertEqual(self.bal(self.cash), D('99600.00'))

    def test_update_account_moves_the_money(self):
        e = self.event('1000')
        e.account = self.bank
        e.save()
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D('100000.00'), D('99000.00')))

    def test_toggling_include_in_net_worth_refunds_or_charges_the_account(self):
        e = self.event('1000')
        e.include_in_net_worth = False
        e.save()
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        e.include_in_net_worth = True
        e.save()
        self.assertEqual(self.bal(self.cash), D('99000.00'))

    def test_removing_and_adding_an_account(self):
        e = self.event('1000')
        e.account = None
        e.save()
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        e.account = self.cash
        e.save()
        self.assertEqual(self.bal(self.cash), D('99000.00'))

    def test_resaving_is_balance_neutral(self):
        e = self.event('1000')
        for _ in range(3):
            e.save()
        self.assertEqual(self.bal(self.cash), D('99000.00'))

    def test_delete_restores_the_balance(self):
        e = self.event('1000')
        e.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertFalse(CapitalEvent.objects.filter(pk=e.pk).exists())

    def test_default_ordering_is_newest_first(self):
        old = self.event('1', date=today() - timedelta(days=5), account=None)
        new = self.event('1', account=None)
        self.assertEqual(list(CapitalEvent.objects.filter(user=self.user)), [new, old])

    def test_audit_log(self):
        e = self.event('1000')
        e.amount = D('1200')
        e.save()
        logs = list(FinancialAuditLog.objects.filter(model_name='CapitalEvent', object_id=e.id).order_by('timestamp', 'id'))
        self.assertEqual([x.action for x in logs], ['CREATE', 'UPDATE'])
        self.assertEqual(logs[1].diff['before'], {'amount': '1000.00'})

    @override_settings(SOFT_DELETE_ENABLED=True)
    def test_soft_delete_restores_balance_once(self):
        e = self.event('1000')
        e.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertTrue(CapitalEvent.objects.get(pk=e.pk).is_deleted)
        self.assertFalse(CapitalEvent.active.filter(pk=e.pk).exists())

    def test_str(self):
        self.assertIn('Large Purchase', str(self.event('5', account=None)))


class TestCapitalEventMultiCurrency(CapBase):
    def setUp(self):
        super().setUp()
        seed_fx()
        self.usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT',
                                          balance=D('1000'), currency='$')

    def test_foreign_event_stores_rate_and_base_amount(self):
        e = self.event('10', currency='$', account=None)
        self.assertEqual((e.exchange_rate, e.base_amount), (D('80'), D('800.00')))

    def test_account_is_charged_in_its_own_currency(self):
        self.event('800', account=self.usd)                     # ₹800 -> $10
        self.assertEqual(self.bal(self.usd), D('990.00'))
        self.event('5', currency='$', account=self.cash)        # $5 -> ₹400
        self.assertEqual(self.bal(self.cash), D('99600.00'))

    def test_update_and_delete_reverse_at_the_converted_amount(self):
        e = self.event('10', currency='$', account=self.cash)
        e.amount = D('5')
        e.save()
        self.assertEqual(self.bal(self.cash), D('99600.00'))
        e.delete()
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_non_rupee_base_currency(self):
        usd_user = self.make_user('usd-cap', currency='$')
        e = CapitalEvent.objects.create(user=usd_user, date=today(), amount=D('800'), currency='₹')
        self.assertEqual((e.exchange_rate, e.base_amount), (D('0.0125'), D('10.00')))


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
class TestCapitalEventForm(CapBase):
    def data(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '400000', 'currency': '₹', 'account': str(self.bank.id),
                'subtype': 'large_purchase', 'note': 'Renovation advance'}
        data.update(overrides)
        return data

    def form(self, user=None, instance=None, **overrides):
        return CapitalEventForm(self.data(**overrides), user=user or self.user, instance=instance)

    def test_valid(self):
        self.assertTrue(self.form().is_valid())

    def test_required_and_optional_fields(self):
        form = CapitalEventForm({}, user=self.user)
        self.assertFalse(form.is_valid())
        for field in ('date', 'amount', 'subtype', 'currency'):
            self.assertIn(field, form.errors)
        for field in ('account', 'linked_loan', 'note'):
            self.assertNotIn(field, form.errors)

    def test_amount_must_be_positive_and_in_range(self):
        for bad in ('0', '-1', 'abc', '1.234', '1' * 14):
            self.assertIn('amount', self.form(amount=bad).errors, bad)

    def test_future_dates_rejected_tomorrow_slack_allowed(self):
        self.assertIn('date', self.form(date=(today() + timedelta(days=2)).isoformat()).errors)
        self.assertTrue(self.form(date=(today() + timedelta(days=1)).isoformat()).is_valid())
        self.assertTrue(self.form(date=(today() - timedelta(days=4000)).isoformat()).is_valid())

    def test_every_subtype_accepted_unknown_rejected(self):
        for subtype in SUBTYPES:
            self.assertTrue(self.form(subtype=subtype).is_valid(), subtype)
        self.assertIn('subtype', self.form(subtype='vacation').errors)

    def test_currency_must_be_known(self):
        self.assertIn('currency', self.form(currency='XXX').errors)

    def test_account_must_be_own_and_active(self):
        other = self.make_user('other-cap-form')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertIn('account', self.form(account=str(theirs.id)).errors)
        self.bank.is_active = False
        self.bank.save()
        self.assertIn('account', self.form(account=str(self.bank.id)).errors)

    def test_linked_loan_must_be_own_and_active(self):
        mine = self.loan()
        self.assertTrue(self.form(linked_loan=str(mine.id), subtype='loan_prepayment').is_valid())
        other = self.make_user('other-cap-loan')
        theirs = Loan.objects.create(user=other, name='X', loan_type='PERSONAL', initial_principal=1, duration_months=1,
                                     start_date=today(), is_active=True)
        self.assertIn('linked_loan', self.form(linked_loan=str(theirs.id)).errors)
        Loan.objects.filter(pk=mine.pk).update(is_active=False)
        self.assertIn('linked_loan', self.form(linked_loan=str(mine.id)).errors)

    def test_flags_default_off_when_unchecked_in_a_post(self):
        """Unchecked boxes are absent from a POST: the form reads that as "off"."""
        form = self.form()
        self.assertTrue(form.is_valid())
        self.assertEqual((form.cleaned_data['exclude_from_averages'], form.cleaned_data['exclude_from_budget'],
                          form.cleaned_data['include_in_net_worth']), (False, False, False))

    def test_new_form_defaults(self):
        form = CapitalEventForm(user=self.user)
        self.assertEqual(form.fields['currency'].initial, '₹')
        self.assertEqual(CapitalEvent._meta.get_field('exclude_from_averages').default, True)
        self.assertEqual(CapitalEvent._meta.get_field('include_in_net_worth').default, True)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
class TestCapitalEventList(CapBase):
    url = reverse('capital-event-list')

    def ids(self, **params):
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        return [e.id for e in response.context['events']]

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_only_own_events(self):
        mine = self.event(account=None)
        other = self.make_user('other-list-cap')
        CapitalEvent.objects.create(user=other, date=today(), amount=D('9'), note='secret')
        self.assertEqual(self.ids(), [mine.id])
        self.assertNotContains(self.client.get(self.url), 'secret')

    def test_default_period_is_this_month(self):
        now = self.event(account=None)
        self.event(account=None, date=today().replace(day=1) - timedelta(days=5))
        self.assertEqual(self.ids(), [now.id])
        self.assertEqual(len(self.ids(time_period='all')), 2)

    def test_totals_use_base_amount_of_the_filtered_set(self):
        seed_fx()
        self.event('1000', account=None)
        self.event('10', currency='$', account=None)
        self.event('999', account=None, date=today() - timedelta(days=300))
        ctx = self.client.get(self.url).context
        self.assertEqual((ctx['filtered_count'], ctx['filtered_amount']), (2, D('1800.00')))

    def test_empty_totals(self):
        ctx = self.client.get(self.url).context
        self.assertEqual((ctx['filtered_count'], ctx['filtered_amount']), (0, 0))

    def test_filter_by_subtype_and_account(self):
        a = self.event(subtype='gift_given', account=self.cash)
        b = self.event(subtype='medical_lump_sum', account=self.bank)
        self.assertEqual(self.ids(subtype='gift_given'), [a.id])
        self.assertEqual(set(self.ids(subtype=['gift_given', 'medical_lump_sum'])), {a.id, b.id})
        self.assertEqual(self.ids(account=str(self.bank.id)), [b.id])
        self.assertEqual(self.ids(subtype='gift_given', account=str(self.bank.id)), [])

    def test_amount_range(self):
        for amount in ('499.99', '500', '10000.01'):
            self.event(amount, account=None)
        got = lambda label: {e.base_amount for e in self.client.get(self.url, {'amount_range': label}).context['events']}
        self.assertEqual(got('Under ₹500'), {D('499.99')})
        self.assertEqual(got('Over ₹10,000'), {D('10000.01')})

    def test_search_looks_in_the_note(self):
        a = self.event(note='Kitchen renovation', account=None)
        self.event(note='Other', account=None)
        self.assertEqual(self.ids(search='RENOVATION'), [a.id])

    def test_sorting(self):
        old_big = self.event('900', date=today() - timedelta(days=2), account=None)
        new_small = self.event('100', account=None)
        self.assertEqual(self.ids(sort='date_desc'), [new_small.id, old_big.id])
        self.assertEqual(self.ids(sort='date_asc'), [old_big.id, new_small.id])
        self.assertEqual(self.ids(sort='amount_desc'), [old_big.id, new_small.id])
        self.assertEqual(self.ids(sort='amount_asc'), [new_small.id, old_big.id])

    def test_pagination(self):
        for _ in range(25):
            self.event('1', account=None)
        self.assertEqual(len(self.ids()), 20)
        self.assertEqual(len(self.ids(page=2)), 5)

    def test_context_for_the_filter_bar(self):
        ctx = self.client.get(self.url, {'subtype': 'gift_given', 'time_period': 'all', 'search': 'x'}).context
        self.assertEqual((ctx['selected_subtype'], ctx['selected_subtypes'], ctx['active_filters_count']),
                         ('gift_given', ['gift_given'], 3))
        self.assertEqual(self.client.get(self.url).context['active_filters_count'], 0)

    def test_garbage_params_do_not_crash(self):
        self.event(account=None)
        for params in ({'subtype': 'zzz'}, {'account': 'abc'}, {'sort': 'zzz'}, {'time_period': 'zzz'}, {'page': 'x'}):
            with self.subTest(params=params):
                self.assertIn(self.client.get(self.url, params).status_code, (200, 404))

    def test_htmx_partial(self):
        partial = [t.name for t in self.client.get(self.url, HTTP_HX_REQUEST='true').templates]
        self.assertIn('expenses/partials/_capital_event_list.html', partial)
        self.assertNotIn('expenses/capital_event_list.html', partial)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
class TestCapitalEventCreate(CapBase):
    url = reverse('capital-event-create')

    def payload(self, **overrides):
        data = {'date': today().isoformat(), 'amount': '400000', 'currency': '₹', 'account': str(self.bank.id),
                'subtype': 'large_purchase', 'note': 'Renovation advance', 'exclude_from_averages': 'on',
                'exclude_from_budget': 'on', 'include_in_net_worth': 'on'}
        data.update(overrides)
        return data

    def test_login_required_and_get(self):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_create_saves_debits_and_redirects(self):
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, reverse('capital-event-list'))
        e = CapitalEvent.objects.get()
        self.assertEqual((e.user, e.amount, e.subtype, e.note, e.account, e.currency),
                         (self.user, D('400000.00'), 'large_purchase', 'Renovation advance', self.bank, '₹'))
        self.assertEqual(self.bal(self.bank), D('-300000.00'))
        self.assertIn('Capital event recorded successfully.', self.messages(response))

    def test_flags_are_stored_as_ticked(self):
        self.client.post(self.url, self.payload(exclude_from_averages='', exclude_from_budget='', include_in_net_worth=''))
        e = CapitalEvent.objects.get()
        self.assertEqual((e.exclude_from_averages, e.exclude_from_budget, e.include_in_net_worth), (False, False, False))
        self.assertEqual(self.bal(self.bank), D('100000.00'))

    def test_invalid_input_creates_nothing(self):
        for override in ({'amount': '0'}, {'amount': '-5'}, {'subtype': 'zzz'}, {'date': ''},
                         {'date': (today() + timedelta(days=30)).isoformat()}):
            with self.subTest(override=override):
                self.assertEqual(self.client.post(self.url, self.payload(**override)).status_code, 200)
        self.assertEqual(CapitalEvent.objects.count(), 0)
        self.assertEqual(self.bal(self.bank), D('100000.00'))

    def test_cannot_use_someone_elses_account_or_loan(self):
        other = self.make_user('other-cap-create')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=5, currency='₹')
        their_loan = Loan.objects.create(user=other, name='X', loan_type='PERSONAL', initial_principal=1, duration_months=1,
                                         start_date=today(), is_active=True)
        self.assertEqual(self.client.post(self.url, self.payload(account=str(theirs.id))).status_code, 200)
        self.assertEqual(self.client.post(self.url, self.payload(linked_loan=str(their_loan.id))).status_code, 200)
        self.assertEqual(CapitalEvent.objects.count(), 0)
        self.assertEqual(self.bal(theirs), D('5.00'))

    def test_currency_conversion_failure_is_reported_and_saves_nothing(self):
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.client.post(self.url, self.payload(currency='$'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CapitalEvent.objects.count(), 0)
        self.assertTrue(any('currency conversion failed' in m for m in self.messages(response)))

    def test_prefill_from_an_expense_by_pk_and_by_uuid(self):
        e = Expense.objects.create(user=self.user, date=today(), amount=D('50000'), description='Laptop',
                                   category='Shopping', currency='₹', account=self.cash)
        for key in (e.pk, e.uuid):
            response = self.client.get(self.url, {'from_expense': str(key)})
            initial = response.context['form'].initial
            self.assertEqual((initial['amount'], initial['note'], initial['account'], initial['subtype']),
                             (D('50000.00'), 'Laptop', self.cash.id, 'other'))
            self.assertEqual(response.context['from_expense_id'], str(key))

    def test_prefill_ignores_junk_and_other_users_expenses(self):
        other = self.make_user('other-cap-pre')
        theirs = Expense.objects.create(user=other, date=today(), amount=D('7'), description='x', category='Food')
        for raw in ('abc', 'not-a-valid-uuid-but-36-chars-long-xxxx', str(uuid.uuid4()), '99999999', str(theirs.pk)):
            with self.subTest(raw=raw):
                response = self.client.get(self.url, {'from_expense': raw})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('amount', response.context['form'].initial)

    def test_query_prefill_of_amount_and_subtype(self):
        response = self.client.get(self.url, {'amount': '2500', 'subtype': 'gift_given'})
        self.assertEqual(response.context['form'].initial, {'amount': '2500', 'subtype': 'gift_given'})

    def test_conversion_flow_deletes_the_source_expense_and_charges_once(self):
        e = Expense.objects.create(user=self.user, date=today(), amount=D('5000'), description='Laptop',
                                   category='Shopping', currency='₹', account=self.cash)
        self.assertEqual(self.bal(self.cash), D('95000.00'))
        response = self.client.post(self.url, self.payload(amount='5000', account=str(self.cash.id), note='Laptop',
                                                            from_expense_id=str(e.pk), delete_source_expense='1'))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Expense.objects.filter(pk=e.pk).exists())
        self.assertEqual(self.bal(self.cash), D('95000.00'), 'debited exactly once')
        self.assertIn('Original expense deleted after conversion.', self.messages(response))

    def test_source_expense_is_kept_unless_asked_to_delete_it(self):
        e = Expense.objects.create(user=self.user, date=today(), amount=D('5'), description='x', category='Food', currency='₹')
        self.client.post(self.url, self.payload(from_expense_id=str(e.pk)))
        self.assertTrue(Expense.objects.filter(pk=e.pk).exists())

    def test_junk_or_foreign_source_id_on_post_is_ignored(self):
        other = self.make_user('other-cap-post')
        theirs = Expense.objects.create(user=other, date=today(), amount=D('7'), description='x', category='Food')
        for raw in ('abc', str(theirs.pk), str(uuid.uuid4())):
            with self.subTest(raw=raw):
                response = self.client.post(self.url, self.payload(from_expense_id=raw, delete_source_expense='1',
                                                                    note=f'n-{raw[:5]}'))
                self.assertEqual(response.status_code, 302)
        self.assertTrue(Expense.objects.filter(pk=theirs.pk).exists())

    def test_loan_dropdown_endpoint_lists_only_own_active_loans(self):
        mine = self.loan()
        Loan.objects.create(user=self.user, name='Closed', loan_type='PERSONAL', initial_principal=1, duration_months=1,
                            start_date=today(), is_active=False)
        other = self.make_user('other-cap-ajax')
        Loan.objects.create(user=other, name='Theirs', loan_type='PERSONAL', initial_principal=1, duration_months=1,
                            start_date=today(), is_active=True)
        data = self.client.get(reverse('capital-event-loans-ajax')).json()
        self.assertEqual(data, {'loans': [{'id': mine.id, 'name': 'Home', 'loan_type': 'HOME'}]})
        self.client.logout()
        self.assertEqual(self.client.get(reverse('capital-event-loans-ajax')).json(), {'loans': []})


# ---------------------------------------------------------------------------
# Edit / delete
# ---------------------------------------------------------------------------
class TestCapitalEventEditDelete(CapBase):
    def edit(self, e, **overrides):
        data = {'date': e.date.isoformat(), 'amount': str(e.amount), 'currency': e.currency,
                'account': str(e.account_id or ''), 'subtype': e.subtype, 'note': e.note,
                'exclude_from_averages': 'on', 'exclude_from_budget': 'on', 'include_in_net_worth': 'on'}
        data.update(overrides)
        return self.client.post(reverse('capital-event-edit', kwargs={'pk': e.pk}), data)

    def test_get_by_pk_and_uuid(self):
        e = self.event()
        for key in (e.pk, e.uuid):
            self.assertEqual(self.client.get(reverse('capital-event-edit', kwargs={'pk': key})).status_code, 200)

    def test_update_changes_fields_and_balances(self):
        e = self.event('1000')
        response = self.edit(e, amount='1750.25', account=str(self.bank.id), subtype='gift_given', note='Wedding')
        self.assertRedirects(response, reverse('capital-event-list'))
        e.refresh_from_db()
        self.assertEqual((e.amount, e.account, e.subtype, e.note), (D('1750.25'), self.bank, 'gift_given', 'Wedding'))
        self.assertEqual((self.bal(self.cash), self.bal(self.bank)), (D('100000.00'), D('98249.75')))
        self.assertIn('Capital event updated.', self.messages(response))

    def test_unticking_net_worth_on_edit_refunds_the_account(self):
        e = self.event('1000')
        self.edit(e, include_in_net_worth='')
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_invalid_edit_changes_nothing(self):
        e = self.event('1000')
        for override in ({'amount': '0'}, {'amount': '-5'}, {'date': (today() + timedelta(days=9)).isoformat()}):
            with self.subTest(override=override):
                self.assertEqual(self.edit(e, **override).status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.amount, D('1000.00'))
        self.assertEqual(self.bal(self.cash), D('99000.00'))

    def test_other_users_event_is_404_for_every_action(self):
        other = self.make_user('other-cap-edit')
        theirs = CapitalEvent.objects.create(user=other, date=today(), amount=D('5'))
        self.assertEqual(self.client.get(reverse('capital-event-edit', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.edit(theirs, amount='1').status_code, 404)
        self.assertEqual(self.client.post(reverse('capital-event-delete', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertEqual(self.client.post(reverse('capital-event-convert', kwargs={'pk': theirs.pk})).status_code, 404)
        self.assertTrue(CapitalEvent.objects.filter(pk=theirs.pk).exists())

    def test_edit_next_param_only_same_site(self):
        e = self.event(account=None)
        self.assertEqual(self.edit(e, next='/transactions/').url, '/transactions/')
        self.assertEqual(self.edit(e, next='https://evil.example/').url, reverse('capital-event-list'))
        self.assertEqual(self.edit(e, next='//evil.example/').url, reverse('capital-event-list'))

    def test_conversion_failure_on_edit_is_reported(self):
        e = self.event(account=None)
        with patch('expenses.models.get_exchange_rate', side_effect=RuntimeError('fx down')):
            response = self.edit(e, currency='$')
        self.assertEqual(response.status_code, 200)
        e.refresh_from_db()
        self.assertEqual(e.currency, '₹')

    def test_delete_confirmation_then_delete_with_message(self):
        e = self.event('1000')
        url = reverse('capital-event-delete', kwargs={'pk': e.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertTrue(CapitalEvent.objects.filter(pk=e.pk).exists())
        response = self.client.post(url)
        self.assertRedirects(response, reverse('capital-event-list'))
        self.assertFalse(CapitalEvent.objects.filter(pk=e.pk).exists())
        self.assertEqual(self.bal(self.cash), D('100000.00'))
        self.assertIn('Capital event deleted.', self.messages(response))

    def test_delete_by_uuid(self):
        e = self.event(account=None)
        self.client.post(reverse('capital-event-delete', kwargs={'pk': e.uuid}))
        self.assertFalse(CapitalEvent.objects.filter(pk=e.pk).exists())

    def test_delete_next_param_only_same_site(self):
        for target, expected in (('/transactions/', '/transactions/'),
                                 ('https://evil.example/', reverse('capital-event-list'))):
            e = self.event(account=None)
            self.assertEqual(self.client.post(reverse('capital-event-delete', kwargs={'pk': e.pk}) + f'?next={target}').url, expected)

    def test_login_required(self):
        e = self.event(account=None)
        self.client.logout()
        for name in ('capital-event-edit', 'capital-event-delete', 'capital-event-convert'):
            self.assertEqual(self.client.post(reverse(name, kwargs={'pk': e.pk})).status_code, 302, name)
        self.assertTrue(CapitalEvent.objects.filter(pk=e.pk).exists())


# ---------------------------------------------------------------------------
# Conversions between expenses and capital events
# ---------------------------------------------------------------------------
class TestConversions(CapBase):
    def to_expense(self, e):
        return self.client.post(reverse('capital-event-convert', kwargs={'pk': e.pk}))

    def test_capital_event_becomes_an_expense_without_changing_the_balance(self):
        e = self.event('5000', subtype='medical_lump_sum', note='Surgery', date=today() - timedelta(days=3), account=self.bank)
        self.assertEqual(self.bal(self.bank), D('95000.00'))
        response = self.to_expense(e)
        self.assertRedirects(response, reverse('expense-list'), fetch_redirect_response=False)
        self.assertFalse(CapitalEvent.objects.filter(pk=e.pk).exists())
        x = Expense.objects.get()
        self.assertEqual((x.amount, x.currency, x.description, x.category, x.account, x.date),
                         (D('5000.00'), '₹', 'Surgery', 'Medical Lump Sum', self.bank, today() - timedelta(days=3)))
        self.assertEqual(self.bal(self.bank), D('95000.00'), 'one debit before, one after')
        self.assertIn('Capital event converted to a regular expense.', self.messages(response))

    def test_a_blank_note_falls_back_to_the_subtype_name(self):
        self.to_expense(self.event('5', note='', subtype='gift_given', account=None))
        self.assertEqual(Expense.objects.get().description, 'Gift Given')

    def test_an_event_kept_out_of_cash_flow_does_not_start_moving_money(self):
        """Regression: converting used to debit the account for an event that never had."""
        e = self.event('5000', include_in_net_worth=False)
        self.to_expense(e)
        x = Expense.objects.get()
        self.assertIsNone(x.account)
        self.assertEqual(self.bal(self.cash), D('100000.00'))

    def test_foreign_currency_event_converts(self):
        seed_fx()
        self.to_expense(self.event('10', currency='$', account=None))
        x = Expense.objects.get()
        self.assertEqual((x.currency, x.base_amount), ('$', D('800.00')))

    def test_failed_conversion_keeps_the_event(self):
        e = self.event('5000', account=self.bank)
        with patch('expenses.views.capital_events.Expense.save', side_effect=RuntimeError('boom')):
            response = self.to_expense(e)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CapitalEvent.objects.filter(pk=e.pk).exists())
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(self.bal(self.bank), D('95000.00'))

    def test_convert_is_post_only(self):
        e = self.event(account=None)
        self.assertEqual(self.client.get(reverse('capital-event-convert', kwargs={'pk': e.pk})).status_code, 405)

    def test_round_trip_expense_to_event_and_back_leaves_the_balance_alone(self):
        x = Expense.objects.create(user=self.user, date=today(), amount=D('2000'), description='Sofa',
                                   category='Home', currency='₹', account=self.cash)
        self.assertEqual(self.bal(self.cash), D('98000.00'))
        self.client.post(reverse('expense-convert', kwargs={'pk': x.pk}))
        self.assertEqual(self.bal(self.cash), D('98000.00'))
        event = CapitalEvent.objects.get()
        self.client.post(reverse('capital-event-convert', kwargs={'pk': event.pk}))
        self.assertEqual(self.bal(self.cash), D('98000.00'))
        self.assertEqual((Expense.objects.count(), CapitalEvent.objects.count()), (1, 0))


# ---------------------------------------------------------------------------
# Loan linking
# ---------------------------------------------------------------------------
class TestLoanLinking(CapBase):
    def summary(self, loan):
        loan = Loan.objects.get(pk=loan.pk)
        return LoanService.get_loan_summary(loan)

    def test_down_payment_and_prepayment_reduce_the_remaining_principal(self):
        loan = self.loan('100000')
        self.event('20000', subtype='loan_down_payment', linked_loan=loan, account=None)
        self.event('5000', subtype='loan_prepayment', linked_loan=loan, account=None)
        s = self.summary(loan)
        self.assertEqual((s['capital_prepaid'], s['remaining_principal']), (25000.0, 75000.0))

    def test_other_subtypes_linked_to_a_loan_do_not_reduce_principal(self):
        loan = self.loan('100000')
        self.event('20000', subtype='large_purchase', linked_loan=loan, account=None)
        self.event('20000', subtype='other', linked_loan=loan, account=None)
        self.assertEqual(self.summary(loan)['remaining_principal'], 100000.0)

    def test_unlinked_events_do_not_touch_any_loan(self):
        loan = self.loan('100000')
        self.event('20000', subtype='loan_prepayment', account=None)
        self.assertEqual(self.summary(loan)['remaining_principal'], 100000.0)

    def test_events_are_combined_with_emi_principal(self):
        loan = self.loan('100000')
        LoanRepayment.objects.create(loan=loan, date=today(), amount=D('4000'), principal_portion=D('3000'),
                                     interest_portion=D('1000'))
        self.event('10000', subtype='loan_prepayment', linked_loan=loan, account=None)
        s = self.summary(loan)
        self.assertEqual((s['principal_paid'], s['capital_prepaid'], s['remaining_principal']), (3000.0, 10000.0, 87000.0))

    def test_deleting_the_event_gives_the_principal_back(self):
        loan = self.loan('100000')
        e = self.event('10000', subtype='loan_prepayment', linked_loan=loan, account=None)
        e.delete()
        self.assertEqual(self.summary(loan)['remaining_principal'], 100000.0)

    def test_total_liabilities_include_the_prepayment(self):
        loan = self.loan('100000')
        self.event('40000', subtype='loan_down_payment', linked_loan=loan, account=None)
        self.assertEqual(LoanService.get_total_liabilities(self.user), 60000.0)

    def test_over_prepaying_never_goes_negative(self):
        loan = self.loan('1000')
        self.event('5000', subtype='loan_prepayment', linked_loan=loan, account=None)
        self.assertEqual(self.summary(loan)['remaining_principal'], 0.0)

    def test_deleting_the_loan_keeps_the_event_unlinked(self):
        loan = self.loan()
        e = self.event(subtype='loan_prepayment', linked_loan=loan, account=None)
        loan.delete()
        e.refresh_from_db()
        self.assertIsNone(e.linked_loan)


# ---------------------------------------------------------------------------
# The three flags and what they steer
# ---------------------------------------------------------------------------
class TestFlagsSteerTheDashboard(CapBase):
    def setUp(self):
        super().setUp()
        profile = self.user.profile
        profile.has_seen_tutorial = True      # a user with no data is otherwise sent to onboarding
        profile.save()

    def home(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return response.context

    def test_default_event_is_a_marker_not_a_spend(self):
        self.event('50000', exclude_from_averages=True, exclude_from_budget=True, account=None)
        ctx = self.home()
        self.assertEqual(ctx['hero_metrics']['spent'], D('0'))
        self.assertEqual([a['amount'] for a in ctx['capital_event_annotations']], [50000.0])
        self.assertNotIn('Large Purchase', [c['category'] for c in ctx['category_data']])

    def test_unticking_exclude_from_averages_counts_it_as_spending_and_drops_the_marker(self):
        self.event('50000', exclude_from_averages=False, exclude_from_budget=True, account=None)
        ctx = self.home()
        self.assertEqual(ctx['hero_metrics']['spent'], D('50000.00'))
        self.assertEqual(ctx['capital_event_annotations'], [])

    def test_unticking_exclude_from_budget_adds_a_category_line_named_after_the_subtype(self):
        self.event('50000', exclude_from_averages=True, exclude_from_budget=False, account=None)
        ctx = self.home()
        self.assertIn(('Large Purchase', 50000.0), [(c['category'], c['total']) for c in ctx['category_data']])
        self.assertEqual(ctx['hero_metrics']['spent'], D('0'))

    def test_savings_rate_uses_only_events_counted_in_averages(self):
        from expenses.models import Income
        Income.objects.create(user=self.user, date=today(), amount=D('100000'), source_type='Salary', currency='₹')
        self.event('20000', exclude_from_averages=False, account=None)
        self.event('90000', exclude_from_averages=True, account=None)
        self.assertEqual(self.home()['hero_metrics']['savings_rate'], 80.0)

    def test_only_events_in_the_viewed_period_appear(self):
        self.event('7', account=None, date=today() - timedelta(days=90))
        self.assertEqual(self.home()['capital_event_annotations'], [])

    def test_other_users_events_never_appear(self):
        other = self.make_user('other-cap-dash')
        CapitalEvent.objects.create(user=other, date=today(), amount=D('77777'), note='secret')
        self.assertEqual(self.home()['capital_event_annotations'], [])


class TestCapitalEventDocs(CapBase):
    def test_form_offers_every_documented_field(self):
        self.assertEqual(list(CapitalEventForm(user=self.user).fields),
                         ['date', 'amount', 'currency', 'account', 'subtype', 'linked_loan', 'note',
                          'exclude_from_averages', 'exclude_from_budget', 'include_in_net_worth'])
