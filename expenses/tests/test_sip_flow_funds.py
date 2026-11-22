"""The "I started a SIP" guided setup can choose the funds each instalment buys: it creates the account, the schedule
and the fund split together, and the first instalment buys those funds."""
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from expenses.flows.investment import SipRdFlow, SipRdFlowForm
from expenses.models import Account, FinancialFlow, Holding, RecurringFundSplit, RecurringTransaction, Transfer, UserProfile
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal
TODAY = date(2026, 10, 21)


def history():
    rows, d, nav = [], TODAY, D('60.00')
    while d >= date(2026, 6, 1):
        if d.weekday() < 5:
            rows.append((d, nav))
            nav -= D('0.10')
        d -= timedelta(days=1)
    return rows


NIFTY = {'name': 'UTI Nifty 50 Index Fund - Direct Plan - Growth', 'scheme_code': '120716', 'amount': '10000'}
FLEXI = {'name': 'Parag Parikh Flexi Cap Fund - Direct Plan - Growth', 'scheme_code': '122639', 'amount': '5000'}


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sip-flow', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('500000'), currency='₹')
        self.flow = SipRdFlow()

    def data(self, funds=None, **kw):
        values = {'instrument_type': 'SIP', 'name': 'Index SIP', 'amount': D('15000'), 'frequency': 'MONTHLY',
                  'from_account': self.bank, 'deposit_start_date': date(2026, 10, 5)}
        if funds is not None:
            values['fund_splits'] = json.dumps(funds)
        values.update(kw)
        return values

    def commit(self, **kw):
        return self.flow.commit(self.user, self.data(**kw), idempotency_key=uuid.uuid4())

    def form(self, funds=None, **kw):
        post = {'instrument_type': 'SIP', 'name': 'Index SIP', 'amount': '15000', 'frequency': 'MONTHLY',
                'from_account': self.bank.pk, 'deposit_start_date': '2026-10-05', 'deposit_compounding': 'QUARTERLY'}
        if funds is not None:
            post['fund_splits'] = json.dumps(funds)
        post.update(kw)
        return SipRdFlowForm(data=post, user=self.user)


class CommitTests(Base):
    def test_the_chosen_funds_become_the_schedules_split_on_the_new_account(self):
        self.commit(funds=[NIFTY, FLEXI])
        account = Account.objects.get(user=self.user, account_type='MUTUAL_FUND')
        schedule = RecurringTransaction.objects.get(user=self.user, to_account=account)
        splits = {sp.holding.scheme_code: sp.amount for sp in schedule.fund_splits.select_related('holding')}
        self.assertEqual(splits, {'120716': D('10000.00'), '122639': D('5000.00')})
        for holding in account.holdings.all():
            self.assertEqual(holding.units, D('0'))           # created empty: the first instalment buys the first units
            self.assertTrue(holding.is_active)

    def test_without_funds_nothing_changes(self):
        self.commit()
        self.assertEqual(RecurringFundSplit.objects.count(), 0)
        self.assertEqual(Holding.objects.count(), 0)

    def test_an_empty_list_is_the_same_as_none(self):
        self.commit(funds=[])
        self.assertEqual(RecurringFundSplit.objects.count(), 0)

    def test_funds_are_ignored_for_a_recurring_deposit(self):
        form = self.form(funds=[NIFTY], instrument_type='RD', deposit_principal='15000', deposit_rate='7',
                         rd_installment_day='5')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['fund_splits'], '')

    def test_funds_that_cannot_be_bought_undo_the_whole_setup(self):
        too_much = [dict(NIFTY, amount='20000')]
        with self.assertRaises(ValidationError):
            self.commit(funds=too_much)
        self.assertEqual(Account.objects.filter(user=self.user, account_type='MUTUAL_FUND').count(), 0)
        self.assertEqual(RecurringTransaction.objects.count(), 0)
        self.assertEqual(FinancialFlow.objects.count(), 0)

    def test_committing_twice_with_the_same_key_creates_the_funds_once(self):
        key = uuid.uuid4()
        for _ in range(2):
            self.flow.commit(self.user, self.data(funds=[NIFTY]), idempotency_key=key)
        self.assertEqual(RecurringFundSplit.objects.count(), 1)
        self.assertEqual(Holding.objects.count(), 1)

    def test_the_first_instalment_buys_the_chosen_funds(self):
        self.commit(funds=[NIFTY, FLEXI], deposit_start_date=date(2026, 10, 5), create_historical_entries=True)
        with patch('expenses.nav_provider.fetch_scheme_history', side_effect=lambda code: history()), \
                patch('django.utils.timezone.localdate', return_value=TODAY):
            process_user_recurring_transactions(self.user, force=True)
        transfer = Transfer.objects.get(user=self.user)
        self.assertEqual(transfer.amount, D('15000.00'))
        bought = {tx.holding.scheme_code: tx.amount for tx in transfer.holding_allocations.select_related('holding')}
        self.assertEqual(bought, {'120716': D('10000.00'), '122639': D('5000.00')})
        for holding in Holding.objects.all():
            holding.refresh_from_db()
            self.assertGreater(holding.units, 0)


class FormTests(Base):
    def test_valid_funds_pass(self):
        form = self.form(funds=[NIFTY, FLEXI])
        self.assertTrue(form.is_valid(), form.errors)

    def test_funds_adding_up_to_more_than_the_instalment_are_refused_on_the_form(self):
        form = self.form(funds=[dict(NIFTY, amount='10000'), dict(FLEXI, amount='6000')])
        self.assertFalse(form.is_valid())
        self.assertIn('more than the transfer', str(form.errors['fund_splits']))

    def test_the_same_fund_twice_is_refused(self):
        form = self.form(funds=[NIFTY, dict(NIFTY, amount='1000')])
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_a_fund_without_a_searchable_code_is_refused(self):
        with patch('expenses.holdings_service.resolve_scheme_code', return_value=None):
            form = self.form(funds=[{'name': 'Made Up Fund', 'scheme_code': '', 'amount': '1000'}])
            self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_an_amount_is_required_for_every_fund(self):
        form = self.form(funds=[dict(NIFTY, amount='')])
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_an_existing_holding_cannot_be_named_because_the_account_is_new(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='MUTUAL_FUND', balance=D('0'), currency='₹')
        held = Holding.objects.create(account=other, instrument_name='X', instrument_type='MF', units=D('1'),
                                      currency='₹', scheme_code='555', is_active=True)
        form = self.form(funds=[{'holding_id': held.pk, 'amount': '1000'}])
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_unreadable_text_is_refused_not_a_crash(self):
        form = self.form(fund_splits='{not json')
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_too_many_funds_are_refused(self):
        many = [dict(NIFTY, scheme_code=str(100000 + n), name=f'Fund {n}', amount='10') for n in range(30)]
        form = self.form(funds=many)
        self.assertFalse(form.is_valid())


class ReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def preview(self, **extra):
        post = {'instrument_type': 'SIP', 'name': 'Index SIP', 'amount': '15000', 'frequency': 'MONTHLY',
                'from_account': self.bank.pk, 'deposit_start_date': '2026-11-01', 'deposit_compounding': 'SIMPLE',
                'fund_splits': json.dumps([NIFTY, FLEXI])}
        post.update(extra)
        return self.client.post(reverse('flow-preview', kwargs={'key': 'sip'}), post)

    def test_the_review_lists_each_fund_with_its_amount(self):
        response = self.preview()
        self.assertContains(response, 'UTI Nifty 50 Index Fund')
        self.assertContains(response, 'Parag Parikh Flexi Cap Fund')
        self.assertContains(response, '₹10,000')
        self.assertContains(response, 'Funds bought each time')

    def test_the_review_does_not_show_raw_json(self):
        self.assertNotContains(self.preview(), 'scheme_code')

    def test_a_recurring_deposit_review_shows_no_funds(self):
        response = self.preview(instrument_type='RD', deposit_principal='15000', deposit_rate='7',
                                deposit_compounding='QUARTERLY', rd_installment_day='5')
        self.assertNotContains(response, 'Funds bought each time')

    def test_no_funds_no_funds_card(self):
        self.assertNotContains(self.preview(fund_splits=''), 'Funds bought each time')

    def test_the_setup_page_has_the_funds_step_and_search(self):
        response = self.client.get(reverse('flow-detail', kwargs={'key': 'sip'}))
        self.assertContains(response, 'data-step-key="sip_funds"')
        self.assertContains(response, 'flowSplitAdd')
        self.assertContains(response, 'id_fund_splits')

    def test_the_funds_step_only_shows_for_a_sip(self):
        source = (Path(settings.BASE_DIR) / 'static' / 'tmr_flows.js').read_text(encoding='utf-8')
        self.assertIn("stepKey === 'sip_funds'", source)
        self.assertIn("return this.instrumentType === 'SIP'", source)

    def test_other_setups_are_unaffected(self):
        response = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'}))
        self.assertNotContains(response, 'flowSplitAdd')


class ConfirmErrorTests(Base):
    """Confirm on a form with a problem has to say what the problem is, even when the field is hidden (the funds) or on an
    earlier step."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def commit_post(self, **extra):
        post = {'instrument_type': 'SIP', 'name': 'Index SIP', 'amount': '15000', 'frequency': 'MONTHLY',
                'from_account': self.bank.pk, 'deposit_start_date': '2026-11-01', 'deposit_compounding': 'SIMPLE',
                'fund_splits': json.dumps([NIFTY])}
        post.update(extra)
        return self.client.post(reverse('flow-commit', kwargs={'key': 'sip'}), post, HTTP_HX_REQUEST='true')

    def error_in(self, response, field):
        import re
        match = re.search(rf'id="field-errors-{field}" hx-swap-oob="true">(.*?)</div>\s*(?=<div id=|$)', response.content.decode(), re.S)
        self.assertIsNotNone(match, f'no error spot for {field}')
        return match.group(1)

    def test_funds_adding_up_to_more_than_the_instalment_are_explained_on_confirm(self):
        response = self.commit_post(fund_splits=json.dumps([dict(NIFTY, amount='20000')]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('more than', self.error_in(response, 'fund_splits').lower())

    def test_a_fund_typed_without_picking_a_suggestion_is_explained_on_confirm(self):
        with patch('expenses.holdings_service.resolve_scheme_code', return_value=None):       # (no fund by that name)
            response = self.commit_post(fund_splits=json.dumps([{'name': 'HDFC Flexi', 'amount': '5000'}]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.error_in(response, 'fund_splits').strip())

    def test_the_error_spot_exists_on_the_page_for_the_funds_field(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'sip'})).content.decode()
        self.assertIn('id="field-errors-fund_splits"', html)
