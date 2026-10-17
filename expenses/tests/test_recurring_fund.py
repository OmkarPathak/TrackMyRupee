"""A recurring transfer into an investment account can buy several funds each time (a SIP split)."""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from expenses import holdings_service as hs
from expenses.forms import RecurringTransactionForm
from expenses.models import (
    AMFIScheme,
    Account,
    Holding,
    HoldingTransaction,
    RecurringFundSplit,
    RecurringTransaction,
    Transfer,
    UserProfile,
)
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal
TODAY = date(2026, 10, 21)         # a Wednesday


def history():
    """Weekday NAVs from June to TODAY, newest first."""
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
                                           balance=D('900000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')
        self.a = hs.create_holding(self.funds, name='Alpha Fund', units='10', price='40', scheme_code='111')
        self.b = hs.create_holding(self.funds, name='Beta Fund', units='10', price='40', scheme_code='222')
        self.c = hs.create_holding(self.funds, name='Gamma Fund', units='10', price='40', scheme_code='333')
        for patcher in (patch('expenses.nav_provider.fetch_scheme_history', side_effect=lambda code: history()),
                        patch('expenses.forms.get_limit', return_value=-1),
                        patch('django.utils.timezone.localdate', return_value=TODAY)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def schedule(self, splits=None, **kw):
        values = dict(user=self.user, transaction_type='TRANSFER', amount=D('45000'), currency='₹',
                      description='Monthly SIP', frequency='MONTHLY', start_date=date(2026, 8, 5),
                      from_account=self.bank, to_account=self.funds, is_active=True, last_processed_date=None)
        values.update(kw)
        rt = RecurringTransaction.objects.create(**values)
        for holding, amount in (splits if splits is not None else [(self.a, '15000'), (self.b, '15000'), (self.c, '15000')]):
            RecurringFundSplit.objects.create(recurring=rt, holding=holding, amount=D(amount))
        return rt

    def rows(self, *items):
        return json.dumps([{'holding_id': h.pk if hasattr(h, 'pk') else h, 'amount': str(a)} for h, a in items])

    def form_data(self, splits=None, **kw):
        data = {'transaction_type': 'TRANSFER', 'amount': '45000', 'currency': '₹', 'description': 'SIP',
                'frequency': 'MONTHLY', 'start_date': '2026-11-05', 'from_account': self.bank.pk,
                'to_account': self.funds.pk, 'is_active': 'on', 'payment_method': 'Cash'}
        if splits is not None:
            data['fund_splits'] = splits
        data.update(kw)
        return data

    def form(self, splits=None, instance=None, **kw):
        return RecurringTransactionForm(data=self.form_data(splits, **kw), instance=instance, user=self.user)

    def save(self, form):
        form.instance.user = self.user          # the view sets this
        return form.save()


class ProcessingTests(Base):
    def test_one_transfer_buys_three_funds_each_time(self):
        self.schedule()
        process_user_recurring_transactions(self.user, force=True)
        transfers = list(Transfer.objects.filter(user=self.user).order_by('date'))
        self.assertEqual([t.date for t in transfers], [date(2026, 8, 5), date(2026, 9, 5), date(2026, 10, 5)])
        self.assertTrue(all(t.amount == D('45000.00') for t in transfers))
        for t in transfers:
            buys = {tx.holding_id: tx for tx in t.holding_allocations.all()}
            self.assertEqual(set(buys), {self.a.pk, self.b.pk, self.c.pk})
            self.assertTrue(all(tx.amount == D('15000.00') for tx in buys.values()))
            self.assertEqual(hs.unallocated_amount(t), D('0.00'))
        for fund in (self.a, self.b, self.c):
            fund.refresh_from_db()
            self.assertEqual(fund.transactions.count(), 4)       # opening + three instalments

    def test_rows_may_add_up_to_less_than_the_transfer(self):
        self.schedule(splits=[(self.a, '10000'), (self.b, '5000')])
        process_user_recurring_transactions(self.user, force=True)
        t = Transfer.objects.order_by('date').first()
        self.assertEqual(hs.unallocated_amount(t), D('30000.00'))

    def test_different_amounts_per_fund(self):
        self.schedule(splits=[(self.a, '25000'), (self.b, '20000')])
        process_user_recurring_transactions(self.user, force=True)
        t = Transfer.objects.order_by('date').first()
        self.assertEqual(sorted(tx.amount for tx in t.holding_allocations.all()), [D('20000.00'), D('25000.00')])

    def test_a_fund_that_fails_does_not_stop_the_others_or_the_transfer(self):
        self.schedule()
        real = hs.estimate_purchase_terms

        def flaky(scheme_code, *args, **kwargs):
            if scheme_code == '222':
                raise RuntimeError('boom')
            return real(scheme_code, *args, **kwargs)

        with patch('expenses.holdings_service.estimate_purchase_terms', side_effect=flaky):
            process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Transfer.objects.count(), 3)
        funds_bought = set(HoldingTransaction.objects.filter(transfer__isnull=False).values_list('holding_id', flat=True))
        self.assertEqual(funds_bought, {self.a.pk, self.c.pk})

    def test_a_removed_fund_is_skipped(self):
        self.schedule()
        self.b.is_active = False
        self.b.save()
        process_user_recurring_transactions(self.user, force=True)
        funds_bought = set(HoldingTransaction.objects.filter(transfer__isnull=False).values_list('holding_id', flat=True))
        self.assertEqual(funds_bought, {self.a.pk, self.c.pk})

    def test_running_again_adds_nothing(self):
        self.schedule()
        process_user_recurring_transactions(self.user, force=True)
        before = (Transfer.objects.count(), HoldingTransaction.objects.count())
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual((Transfer.objects.count(), HoldingTransaction.objects.count()), before)

    def test_without_a_split_only_the_transfer_is_posted(self):
        self.schedule(splits=[])
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(Transfer.objects.count(), 3)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False).count(), 0)

    def test_a_weekend_instalment_is_priced_at_the_next_business_day(self):
        self.schedule(start_date=date(2026, 9, 5))                   # Saturday
        process_user_recurring_transactions(self.user, force=True)
        tx = HoldingTransaction.objects.get(transfer__date=date(2026, 9, 5), holding=self.a)
        self.assertEqual(tx.nav_date, date(2026, 9, 7))

    def test_an_instalment_whose_nav_is_not_out_yet_waits(self):
        self.schedule(start_date=TODAY)
        unpublished = [row for row in history() if row[0] < TODAY]
        with patch('expenses.nav_provider.fetch_scheme_history', return_value=unpublished):
            process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(set(HoldingTransaction.objects.filter(transfer__isnull=False).values_list('status', flat=True)), {'PENDING'})

    def test_a_fund_waiting_for_its_first_instalment_gets_its_units(self):
        empty = hs.create_empty_holding(self.funds, name='Delta Fund', scheme_code='444')
        self.assertEqual((empty.units, empty.avg_cost, empty.is_active), (D('0'), None, True))
        self.schedule(splits=[(empty, '15000')])
        process_user_recurring_transactions(self.user, force=True)
        empty.refresh_from_db()
        self.assertGreater(empty.units, 0)
        self.assertEqual(empty.transactions.first().note, 'Monthly SIP')

    def test_deleting_a_fund_removes_its_row_but_keeps_the_schedule(self):
        rt = self.schedule()
        Holding.objects.filter(pk=self.b.pk).delete()
        rt.refresh_from_db()
        self.assertTrue(rt.is_active)
        self.assertEqual(rt.fund_splits.count(), 2)

    def test_processing_query_count_does_not_grow_with_funds(self):
        from django.test.utils import CaptureQueriesContext
        self.schedule(splits=[(self.a, '15000')], start_date=TODAY)
        with CaptureQueriesContext(connection) as one:
            process_user_recurring_transactions(self.user, force=True)
        Transfer.objects.all().delete()
        RecurringTransaction.objects.all().update(last_processed_date=None)
        RecurringFundSplit.objects.create(recurring=RecurringTransaction.objects.get(), holding=self.b, amount=D('1000'))
        RecurringFundSplit.objects.create(recurring=RecurringTransaction.objects.get(), holding=self.c, amount=D('1000'))
        with CaptureQueriesContext(connection) as three:
            process_user_recurring_transactions(self.user, force=True)
        # each extra fund costs its own purchase queries, but the schedule's rows are fetched once
        splits_fetched = [q for q in three if 'recurringfundsplit' in q['sql'].lower() and q['sql'].lstrip().upper().startswith('SELECT')]
        self.assertEqual(len(splits_fetched), 1)


class FormTests(Base):
    def test_three_funds_from_one_form(self):
        form = self.form(self.rows((self.a, 15000), (self.b, 15000), (self.c, 15000)))
        self.assertTrue(form.is_valid(), form.errors)
        rt = self.save(form)
        self.assertEqual(sorted((sp.holding.instrument_name, sp.amount) for sp in rt.fund_splits.all()),
                         [('Alpha Fund', D('15000.00')), ('Beta Fund', D('15000.00')), ('Gamma Fund', D('15000.00'))])

    def test_the_rows_cannot_add_up_to_more_than_the_transfer(self):
        form = self.form(self.rows((self.a, 30000), (self.b, 20000)))
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_a_fund_can_appear_only_once(self):
        form = self.form(self.rows((self.a, 1000), (self.a, 2000)))
        self.assertFalse(form.is_valid())

    def test_bad_rows_are_refused(self):
        for raw in ('{broken', json.dumps(['x']), json.dumps([{'holding_id': self.a.pk}]),
                    json.dumps([{'holding_id': self.a.pk, 'amount': '0'}]),
                    json.dumps([{'holding_id': self.a.pk, 'amount': 'abc'}]),
                    json.dumps([{'holding_id': None, 'name': '', 'amount': '100'}]),
                    json.dumps([{'holding_id': 999999, 'amount': '100'}]),
                    json.dumps([{'holding_id': self.a.pk, 'amount': '1'}] * 2)):
            with self.subTest(raw=raw):
                self.assertFalse(self.form(raw).is_valid())

    def test_too_many_rows_are_refused(self):
        rows = json.dumps([{'holding_id': None, 'name': f'F{i}', 'scheme_code': str(i), 'amount': '1'} for i in range(21)])
        self.assertFalse(self.form(rows).is_valid())

    def test_the_fund_must_be_in_the_receiving_account(self):
        other = Account.objects.create(user=self.user, name='Other MF', account_type='MUTUAL_FUND', balance=D('0'))
        form = self.form(self.rows((self.a, 1000)), to_account=other.pk)
        self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_funds_only_for_accounts_valued_from_holdings(self):
        savings = Account.objects.create(user=self.user, name='S2', account_type='SAVINGS_ACCOUNT', balance=D('0'))
        self.assertFalse(self.form(self.rows((self.a, 1000)), to_account=savings.pk).is_valid())

    def test_cross_currency_is_refused(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=D('10'), currency='$')
        self.assertFalse(self.form(self.rows((self.a, 1000)), from_account=usd.pk).is_valid())

    def test_other_users_funds_and_funds_without_a_feed_are_refused(self):
        other = User.objects.create_user(username='someone', password='password')
        o_acct = Account.objects.create(user=other, name='MF', account_type='MUTUAL_FUND', balance=D('0'))
        theirs = hs.create_holding(o_acct, name='Theirs', units='1', price='1', scheme_code='999')
        nofeed = Holding.objects.create(account=self.funds, instrument_name='NoFeed', units=D('1'), avg_cost=D('1'), currency='₹')
        for h in (theirs, nofeed):
            with self.subTest(h=h.instrument_name):
                self.assertFalse(self.form(self.rows((h, 1000))).is_valid())

    def test_a_new_fund_is_created_empty_only_when_saved(self):
        raw = json.dumps([{'holding_id': None, 'name': 'Epsilon Fund', 'scheme_code': '555', 'amount': '15000'},
                          {'holding_id': self.a.pk, 'amount': '15000'}])
        form = self.form(raw)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(Holding.objects.filter(instrument_name='Epsilon Fund').exists())
        rt = self.save(form)
        new = Holding.objects.get(instrument_name='Epsilon Fund')
        self.assertEqual((new.scheme_code, new.units, new.account), ('555', D('0'), self.funds))
        self.assertEqual(rt.fund_splits.count(), 2)

    def test_a_new_fund_name_is_looked_up_when_no_code_was_picked(self):
        AMFIScheme.objects.create(scheme_code='666', scheme_name='Zeta Fund Direct Growth')
        raw = json.dumps([{'holding_id': None, 'name': 'Zeta Fund', 'amount': '1000'}])
        form = self.form(raw)
        self.assertTrue(form.is_valid(), form.errors)
        self.save(form)
        self.assertEqual(Holding.objects.get(instrument_name='Zeta Fund').scheme_code, '666')

    def test_a_new_fund_that_cannot_be_found_is_refused(self):
        raw = json.dumps([{'holding_id': None, 'name': 'Nonexistent Thing', 'amount': '1000'}])
        with patch('expenses.holdings_service.resolve_scheme_code', return_value=''):
            form = self.form(raw)
            self.assertFalse(form.is_valid())
        self.assertIn('fund_splits', form.errors)

    def test_funds_on_a_non_transfer_are_dropped(self):
        form = self.form(self.rows((self.a, 1000)), transaction_type='EXPENSE', category='Food', account=self.bank.pk)
        self.assertTrue(form.is_valid(), form.errors)
        rt = self.save(form)
        self.assertEqual(rt.fund_splits.count(), 0)

    def test_editing_replaces_the_split(self):
        rt = self.schedule(start_date=date(2026, 11, 5))
        form = self.form(self.rows((self.b, 40000)), instance=rt)
        self.assertTrue(form.is_valid(), form.errors)
        self.save(form)
        self.assertEqual([(sp.holding_id, sp.amount) for sp in rt.fund_splits.all()], [(self.b.pk, D('40000.00'))])

    def test_an_empty_list_clears_the_split(self):
        rt = self.schedule(start_date=date(2026, 11, 5))
        form = self.form('[]', instance=rt)
        self.assertTrue(form.is_valid(), form.errors)
        self.save(form)
        self.assertEqual(rt.fund_splits.count(), 0)

    def test_leaving_the_field_out_keeps_the_split(self):
        rt = self.schedule(start_date=date(2026, 11, 5))
        form = self.form(None, instance=rt)
        self.assertTrue(form.is_valid(), form.errors)
        self.save(form)
        self.assertEqual(rt.fund_splits.count(), 3)

    def test_the_amount_cannot_drop_below_the_split_when_the_field_is_left_out(self):
        rt = self.schedule(start_date=date(2026, 11, 5))
        form = self.form(None, instance=rt, amount='30000')
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)

    def test_the_edit_form_starts_with_the_saved_rows(self):
        rt = self.schedule()
        form = RecurringTransactionForm(instance=rt, user=self.user)
        saved = json.loads(form.fields['fund_splits'].initial)
        self.assertEqual({r['holding_id'] for r in saved}, {self.a.pk, self.b.pk, self.c.pk})

    def test_creating_with_past_entries_buys_every_fund_for_each(self):
        data = self.form_data(self.rows((self.a, 15000), (self.b, 15000), (self.c, 15000)),
                              start_date='2026-08-05', create_historical_entries='on')
        form = RecurringTransactionForm(data=data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.save(form)
        self.assertEqual(HoldingTransaction.objects.filter(transfer__isnull=False).count(), 9)


class PageTests(Base):
    def test_the_form_page_carries_the_split_picker_and_fund_data(self):
        response = self.client.get(reverse('recurring-create'))
        self.assertContains(response, 'id="fundGroup"')
        self.assertContains(response, 'id="fund-data"')
        self.assertContains(response, 'id="splitAdd"')
        data = response.context['form'].fund_data
        self.assertEqual({f['name'] for f in data[str(self.funds.pk)]}, {'Alpha Fund', 'Beta Fund', 'Gamma Fund'})
        self.assertNotIn(str(self.bank.pk), data)

    def test_posting_the_form_through_the_view_saves_the_split(self):
        data = self.form_data(self.rows((self.a, 15000), (self.b, 15000), (self.c, 15000)))
        response = self.client.post(reverse('recurring-create'), data)
        self.assertEqual(response.status_code, 302)
        rt = RecurringTransaction.objects.get(user=self.user)
        self.assertEqual(rt.fund_splits.count(), 3)

    def test_the_edit_page_lists_the_saved_funds(self):
        rt = self.schedule()
        response = self.client.get(reverse('recurring-edit', args=[rt.uuid]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.a.pk))

    def test_the_list_shows_the_funds_a_transfer_buys(self):
        self.schedule()
        response = self.client.get(reverse('recurring-list'))
        for name in ('Alpha Fund', 'Beta Fund', 'Gamma Fund'):
            self.assertContains(response, name)


class CopyMigrationTests(TransactionTestCase):
    """0117 turns the old single fund of a recurring transfer into a split of its whole amount."""

    def test_forward_and_back(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('expenses', '0116_recurring_fund_split')])
        old_apps = executor.loader.project_state([('expenses', '0116_recurring_fund_split')]).apps
        U, Acc, H = old_apps.get_model('auth', 'User'), old_apps.get_model('expenses', 'Account'), old_apps.get_model('expenses', 'Holding')
        RT, Split = old_apps.get_model('expenses', 'RecurringTransaction'), old_apps.get_model('expenses', 'RecurringFundSplit')
        user = U.objects.create(username='migrating')
        acct = Acc.objects.create(user=user, name='MF', account_type='MUTUAL_FUND', balance=0)
        fund = H.objects.create(account=acct, instrument_name='Old Fund', scheme_code='1')
        with_fund = RT.objects.create(user=user, transaction_type='TRANSFER', amount=D('7000'), description='a',
                                      frequency='MONTHLY', start_date=date(2026, 1, 1), to_account=acct, fund=fund)
        without = RT.objects.create(user=user, transaction_type='TRANSFER', amount=D('7000'), description='b',
                                    frequency='MONTHLY', start_date=date(2026, 1, 1), to_account=acct)
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([('expenses', '0117_copy_recurring_fund_to_splits')])
            new_apps = executor.loader.project_state([('expenses', '0117_copy_recurring_fund_to_splits')]).apps
            Split = new_apps.get_model('expenses', 'RecurringFundSplit')
            rows = list(Split.objects.values_list('recurring_id', 'holding_id', 'amount'))
            self.assertEqual(rows, [(with_fund.pk, fund.pk, D('7000.00'))])
            self.assertFalse(Split.objects.filter(recurring_id=without.pk).exists())
        finally:
            executor = MigrationExecutor(connection)
            executor.loader.build_graph()
            executor.migrate(executor.loader.graph.leaf_nodes())
