"""A recurring transfer into an investment account can buy a fund each time (a SIP)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses import holdings_service as hs
from expenses.forms import RecurringTransactionForm
from expenses.models import (
    AMFIScheme,
    Account,
    Holding,
    HoldingTransaction,
    RecurringTransaction,
    Transfer,
    UserProfile,
)
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal
TODAY = date(2026, 10, 21)         # a Wednesday
# weekdays only, newest first, back to the start of the SIP
def history():
    rows, d, nav = [], TODAY, D('60.00')
    while d >= date(2026, 6, 1):
        if d.weekday() < 5:
            rows.append((d, nav))
            nav -= D('0.10')
        d -= timedelta(days=1)
    return rows


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='sipper', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('500000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')
        self.fund = hs.create_holding(self.funds, name='Alpha Fund', units='10', price='40', scheme_code='111')
        for patcher in (patch('expenses.nav_provider.fetch_scheme_history', side_effect=lambda code: history()),
                        patch('expenses.forms.get_limit', return_value=-1),
                        patch('django.utils.timezone.localdate', return_value=TODAY)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def schedule(self, **kw):
        values = dict(user=self.user, transaction_type='TRANSFER', amount=D('5000'), currency='₹',
                      description='Monthly SIP', frequency='MONTHLY', start_date=date(2026, 8, 5),
                      from_account=self.bank, to_account=self.funds, fund=self.fund, is_active=True,
                      last_processed_date=None)
        values.update(kw)
        return RecurringTransaction.objects.create(**values)

    def form_data(self, **kw):
        data = {'transaction_type': 'TRANSFER', 'amount': '5000', 'currency': '₹', 'description': 'SIP',
                'frequency': 'MONTHLY', 'start_date': '2026-11-05', 'from_account': self.bank.pk,
                'to_account': self.funds.pk, 'is_active': 'on', 'payment_method': 'Cash',
                'fund': self.fund.pk}
        data.update(kw)
        return data

    def save(self, form):
        form.instance.user = self.user          # the view sets this
        return form.save()

    def form(self, **kw):
        return RecurringTransactionForm(data=self.form_data(**kw), user=self.user)


class ProcessingTests(Base):
    def test_every_instalment_buys_the_fund(self):
        rt = self.schedule()
        process_user_recurring_transactions(self.user, force=True)
        transfers = Transfer.objects.filter(user=self.user).order_by('date')
        self.assertEqual([t.date for t in transfers], [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)])
        buys = HoldingTransaction.objects.filter(transfer__in=transfers)
        self.assertEqual(buys.count(), 3)
        self.assertTrue(all(tx.amount == D('5000.00') and tx.holding_id == self.fund.pk for tx in buys))
        self.assertEqual({tx.status for tx in buys}, {'ESTIMATED'})
        self.fund.refresh_from_db()
        self.assertGreater(self.fund.units, D('10'))
        rt.refresh_from_db()
        self.assertEqual(rt.last_processed_date, date(2026, 10, 5))

    def test_an_instalment_on_a_weekend_is_priced_at_the_next_business_day(self):
        self.schedule(start_date=date(2026, 9, 5), frequency='MONTHLY')            # Saturday 5 Sep 2026
        process_user_recurring_transactions(self.user, force=True)
        tx = HoldingTransaction.objects.filter(transfer__date=date(2026, 9, 5)).get()
        self.assertEqual(tx.nav_date, date(2026, 9, 7))

    def test_running_again_adds_nothing(self):
        self.schedule()
        process_user_recurring_transactions(self.user, force=True)
        before = (Transfer.objects.count(), HoldingTransaction.objects.count())
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual((Transfer.objects.count(), HoldingTransaction.objects.count()), before)

    def test_without_a_fund_only_the_transfer_is_posted(self):
        self.schedule(fund=None)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Transfer.objects.count(), 3)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False).count(), 0)

    def test_a_removed_fund_does_not_stop_the_transfer(self):
        self.schedule()
        self.fund.is_active = False
        self.fund.save()
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Transfer.objects.count(), 3)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False).count(), 0)

    def test_a_failing_nav_lookup_never_undoes_the_transfer(self):
        self.schedule()
        with patch('expenses.holdings_service.estimate_purchase_terms', side_effect=RuntimeError('boom')):
            process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Transfer.objects.count(), 3)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False).count(), 0)

    def test_an_instalment_whose_nav_is_not_out_yet_waits(self):
        self.schedule(start_date=TODAY)
        unpublished = [row for row in history() if row[0] < TODAY]
        with patch('expenses.nav_provider.fetch_scheme_history', return_value=unpublished):
            process_user_recurring_transactions(self.user, force=True)
        tx = HoldingTransaction.objects.get(transfer__isnull=False)
        self.assertEqual((tx.status, tx.units, tx.nav_date), ('PENDING', None, TODAY))
        self.assertEqual(Transfer.objects.count(), 1)

    def test_a_new_fund_waiting_for_its_first_instalment_starts_empty(self):
        empty = hs.create_empty_holding(self.funds, name='Beta Fund', scheme_code='222')
        self.assertEqual((empty.units, empty.avg_cost, empty.is_active), (D('0'), None, True))
        self.schedule(fund=empty)
        process_user_recurring_transactions(self.user, force=True)
        empty.refresh_from_db()
        self.assertGreater(empty.units, 0)
        self.assertEqual(empty.transactions.count(), 3)
        self.assertEqual(empty.transactions.first().note, 'Monthly SIP')

    def test_deleting_the_schedule_keeps_the_purchases_and_deleting_the_fund_keeps_the_schedule(self):
        rt = self.schedule()
        process_user_recurring_transactions(self.user, force=True)
        Holding.objects.filter(pk=self.fund.pk).delete()
        rt.refresh_from_db()
        self.assertIsNone(rt.fund)
        self.assertTrue(rt.is_active)


class FormTests(Base):
    def test_a_valid_sip(self):
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        rt = self.save(form)
        self.assertEqual(rt.fund, self.fund)

    def test_the_fund_must_be_in_the_receiving_account(self):
        other = Account.objects.create(user=self.user, name='Other MF', account_type='MUTUAL_FUND', balance=D('0'))
        form = self.form(to_account=other.pk)
        self.assertFalse(form.is_valid())
        self.assertIn('fund', form.errors)

    def test_funds_only_for_accounts_valued_from_holdings(self):
        savings = Account.objects.create(user=self.user, name='S2', account_type='SAVINGS_ACCOUNT', balance=D('0'))
        form = self.form(to_account=savings.pk)
        self.assertFalse(form.is_valid())
        self.assertIn('fund', form.errors)

    def test_cross_currency_is_refused(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=D('10'), currency='$')
        form = self.form(from_account=usd.pk)
        self.assertFalse(form.is_valid())
        self.assertIn('fund', form.errors)

    def test_other_users_funds_and_funds_without_a_feed_are_not_choices(self):
        other = User.objects.create_user(username='someone', password='password')
        o_acct = Account.objects.create(user=other, name='MF', account_type='MUTUAL_FUND', balance=D('0'))
        theirs = hs.create_holding(o_acct, name='Theirs', units='1', price='1', scheme_code='999')
        nofeed = Holding.objects.create(account=self.funds, instrument_name='NoFeed', units=D('1'), avg_cost=D('1'), currency='₹')
        for h in (theirs, nofeed):
            form = self.form(fund=h.pk)
            self.assertFalse(form.is_valid(), h)
            self.assertIn('fund', form.errors)

    def test_a_fund_on_a_non_transfer_is_dropped(self):
        from expenses.models import Category
        Category.objects.get_or_create(user=self.user, name='Food')
        form = self.form(transaction_type='EXPENSE', category='Food', account=self.bank.pk)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(self.save(form).fund)

    def test_a_new_fund_is_created_empty_when_saved(self):
        form = self.form(fund='', new_fund_name='Gamma Fund', new_fund_scheme_code='333')
        self.assertTrue(form.is_valid(), form.errors)
        rt = self.save(form)
        self.assertEqual((rt.fund.instrument_name, rt.fund.scheme_code, rt.fund.account), ('Gamma Fund', '333', self.funds))
        self.assertEqual(rt.fund.units, D('0'))

    def test_validation_alone_creates_nothing(self):
        form = self.form(fund='', new_fund_name='Gamma Fund', new_fund_scheme_code='333')
        form.is_valid()
        self.assertFalse(Holding.objects.filter(instrument_name='Gamma Fund').exists())

    def test_a_new_fund_name_is_looked_up_when_no_code_was_picked(self):
        AMFIScheme.objects.create(scheme_code='444', scheme_name='Delta Fund Direct Growth')
        form = self.form(fund='', new_fund_name='Delta Fund')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(self.save(form).fund.scheme_code, '444')

    def test_a_new_fund_that_cannot_be_found_is_refused(self):
        with patch('expenses.holdings_service.resolve_scheme_code', return_value=''):
            form = self.form(fund='', new_fund_name='Nonexistent Thing')
            self.assertFalse(form.is_valid())
        self.assertIn('new_fund_name', form.errors)

    def test_a_new_fund_wins_over_a_selected_one(self):
        form = self.form(new_fund_name='Gamma Fund', new_fund_scheme_code='333')
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(self.save(form).fund.instrument_name, 'Gamma Fund')

    def test_editing_can_assign_and_clear_a_fund(self):
        rt = self.schedule(fund=None)
        form = RecurringTransactionForm(data=self.form_data(), instance=rt, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(self.save(form).fund, self.fund)
        form = RecurringTransactionForm(data=self.form_data(fund=''), instance=rt, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(self.save(form).fund)

    def test_creating_with_past_entries_buys_the_fund_for_each(self):
        data = self.form_data(start_date='2026-08-05', create_historical_entries='on')
        form = RecurringTransactionForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        rt = self.save(form)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False, holding=rt.fund).count(), 3)


class PageTests(Base):
    def test_the_form_page_has_the_fund_picker_and_the_account_list(self):
        response = self.client.get(reverse('recurring-create'))
        self.assertContains(response, 'id="fundGroup"')
        self.assertContains(response, 'id="fund-accounts"')
        self.assertEqual(response.context['form'].holdings_account_ids, [self.funds.pk])

    def test_the_fund_options_know_their_account(self):
        response = self.client.get(reverse('recurring-create'))
        self.assertContains(response, f'data-account="{self.funds.pk}"')

    def test_the_list_shows_the_fund_a_transfer_buys(self):
        self.schedule()
        response = self.client.get(reverse('recurring-list'))
        self.assertContains(response, 'Alpha Fund')
