"""Holding purchases: the service, the holding page, and allocating a transfer to funds."""
import importlib
import json
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses import holdings_service as hs
from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, Holding, HoldingTransaction, Transfer, UserProfile, Valuation

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='investor', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')

    def fund(self, name='Alpha Fund', units='10', price='100', **kw):
        return hs.create_holding(self.funds, name=name, units=units, price=price, scheme_code=kw.pop('scheme_code', '111'), **kw)

    def transfer(self, amount='10000', **kw):
        return Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.funds,
                                       amount=D(amount), date=kw.pop('date', self.today), **kw)


class DerivePurchaseTests(TestCase):
    def test_any_two_of_three_give_the_third(self):
        self.assertEqual(hs.derive_purchase(units='10', price='50'), (D('10.000000'), D('50.000000'), D('500.00')))
        self.assertEqual(hs.derive_purchase(units='10', amount='500'), (D('10.000000'), D('50.000000'), D('500.00')))
        self.assertEqual(hs.derive_purchase(price='50', amount='500'), (D('10.000000'), D('50.000000'), D('500.00')))

    def test_units_and_amount_win_when_all_three_are_given(self):
        u, p, a = hs.derive_purchase(units='3', price='999', amount='100')
        self.assertEqual((u, a, p), (D('3.000000'), D('100.00'), D('33.333333')))

    def test_one_or_none_is_refused(self):
        for kw in ({}, {'units': '5'}, {'amount': '5'}, {'price': '5'}):
            with self.assertRaises(hs.HoldingError):
                hs.derive_purchase(**kw)

    def test_bad_numbers_are_refused(self):
        for bad in ('abc', '0', '-1', 'NaN', 'Infinity', '1e30'):
            with self.subTest(bad=bad), self.assertRaises(hs.HoldingError):
                hs.derive_purchase(units=bad, price='10')

    def test_dust_is_refused(self):
        with self.assertRaises(hs.HoldingError):
            hs.derive_purchase(units='0.0000001', price='1')


class CreateAndAddPurchaseTests(Base):
    def test_new_holding_has_an_opening_purchase_and_matching_units_and_cost(self):
        h = self.fund()
        self.assertEqual(h.transactions.count(), 1)
        self.assertEqual((h.units, h.avg_cost), (D('10.000000'), D('100.000000')))
        self.assertEqual(h.cost_basis, D('1000.00'))

    def test_second_purchase_gives_the_weighted_average(self):
        h = self.fund()
        hs.add_purchase(h, units='10', price='120', date=self.today)
        h.refresh_from_db()
        self.assertEqual((h.units, h.avg_cost, h.cost_basis), (D('20.000000'), D('110.000000'), D('2200.00')))

    def test_average_cost_keeps_six_places_so_cost_basis_does_not_drift(self):
        h = hs.create_holding(self.funds, name='Odd', units='33.333', amount='10000')
        h.refresh_from_db()
        self.assertEqual(h.cost_basis, D('10000.00'))

    def test_a_legacy_holding_gets_an_opening_purchase_first(self):
        h = Holding.objects.create(account=self.funds, instrument_name='Old', units=D('5'), avg_cost=D('200'), currency='₹')
        self.assertEqual(h.transactions.count(), 0)
        hs.add_purchase(h, units='5', price='300', date=self.today)
        h.refresh_from_db()
        self.assertEqual(h.transactions.count(), 2)
        self.assertEqual((h.units, h.avg_cost), (D('10.000000'), D('250.000000')))

    def test_future_dates_are_refused(self):
        h = self.fund()
        with self.assertRaises(hs.HoldingError):
            hs.add_purchase(h, units='1', price='1', date=self.today + timedelta(days=1))

    def test_accounts_that_ignore_holdings_are_refused(self):
        with self.assertRaises(hs.HoldingError):
            hs.create_holding(self.bank, name='X', units='1', price='1')

    def test_stock_names_are_not_matched_to_funds(self):
        demat = Account.objects.create(user=self.user, name='Demat', account_type='DEMAT', balance=D('0'))
        with patch('requests.get') as net:
            h = hs.create_holding(demat, name='Reliance', units='1', price='10')
        net.assert_not_called()
        self.assertIsNone(h.scheme_code)

    def test_edit_and_delete_recompute(self):
        h = self.fund()
        second = hs.add_purchase(h, units='10', price='120', date=self.today)
        hs.update_purchase(second, units='10', price='200')
        h.refresh_from_db()
        self.assertEqual(h.avg_cost, D('150.000000'))
        hs.delete_purchase(second)
        h.refresh_from_db()
        self.assertEqual((h.units, h.avg_cost), (D('10.000000'), D('100.000000')))

    def test_the_last_purchase_cannot_be_deleted(self):
        h = self.fund()
        with self.assertRaises(hs.HoldingError):
            hs.delete_purchase(h.transactions.get())

    def test_sells_use_average_cost_and_close_a_fully_sold_holding(self):
        h = self.fund()
        HoldingTransaction.objects.create(holding=h, kind='SELL', date=self.today, units=D('4'), price=D('150'), amount=D('600'))
        h.refresh_from_db()
        self.assertEqual((h.units, h.avg_cost, h.is_active), (D('6.000000'), D('100.000000'), True))
        HoldingTransaction.objects.create(holding=h, kind='SELL', date=self.today, units=D('6'), price=D('150'), amount=D('900'))
        h.refresh_from_db()
        self.assertEqual((h.units, h.is_active), (D('0.000000'), False))

    def test_opening_migration_adds_one_purchase_per_holding_with_units_and_cost(self):
        legacy = Holding.objects.create(account=self.funds, instrument_name='Legacy', units=D('5'), avg_cost=D('20'), currency='₹')
        no_cost = Holding.objects.create(account=self.funds, instrument_name='NoCost', units=D('5'), avg_cost=None, currency='₹')
        migration = importlib.import_module('expenses.migrations.0113_holding_opening_transactions')
        migration.add_opening_transactions(django_apps, None)
        migration.add_opening_transactions(django_apps, None)         # safe to run twice
        self.assertEqual(legacy.transactions.count(), 1)
        self.assertEqual(legacy.transactions.get().amount, D('100.00'))
        self.assertEqual(no_cost.transactions.count(), 0)


class TransferLinkTests(Base):
    def test_purchases_cannot_exceed_the_transfer(self):
        t = self.transfer('1000')
        a, b = self.fund('A'), self.fund('B', scheme_code='222')
        hs.add_purchase(a, units='6', price='100', transfer=t, date=self.today)
        with self.assertRaises(hs.HoldingError):
            hs.add_purchase(b, units='5', price='100', transfer=t, date=self.today)
        hs.add_purchase(b, units='4', price='100', transfer=t, date=self.today)
        self.assertEqual(hs.unallocated_amount(t), D('0.00'))

    def test_editing_a_linked_purchase_counts_the_rest_of_the_transfer(self):
        t = self.transfer('1000')
        a = self.fund('A')
        tx = hs.add_purchase(a, units='6', price='100', transfer=t, date=self.today)
        hs.update_purchase(tx, units='10', price='100')                 # up to the full 1000
        with self.assertRaises(hs.HoldingError):
            hs.update_purchase(tx, units='11', price='100')

    def test_a_transfer_into_another_account_is_refused(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='MUTUAL_FUND', balance=D('0'))
        t = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=other, amount=D('1000'), date=self.today)
        with self.assertRaises(hs.HoldingError):
            hs.add_purchase(self.fund(), units='1', price='100', transfer=t, date=self.today)

    def test_a_foreign_currency_transfer_is_refused(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=D('100'), currency='$')
        t = Transfer.objects.create(user=self.user, from_account=usd, to_account=self.funds, amount=D('10'), date=self.today)
        with patch('expenses.utils.get_exchange_rate', return_value=D('80')), self.assertRaises(hs.HoldingError):
            hs.add_purchase(self.fund(), units='1', price='100', transfer=t, date=self.today)

    def test_deleting_the_transfer_removes_its_purchases(self):
        t = self.transfer('1000')
        a = self.fund('A')
        hs.add_purchase(a, units='5', price='100', transfer=t, date=self.today)
        a.refresh_from_db()
        self.assertEqual(a.units, D('15.000000'))
        t.delete()
        a.refresh_from_db()
        self.assertEqual((a.units, a.avg_cost, a.is_active), (D('10.000000'), D('100.000000'), True))

    def test_deleting_the_only_purchase_closes_the_holding(self):
        t = self.transfer('1000')
        h = hs.create_holding(self.funds, name='FromTransfer', units='10', price='100', transfer=t, date=self.today)
        t.delete()
        h.refresh_from_db()
        self.assertEqual((h.units, h.is_active), (D('0.000000'), False))


class AllocateOnTransferFormTests(Base):
    def post(self, rows, amount='10000', **kw):
        data = {'date': self.today.isoformat(), 'amount': amount, 'from_account': self.bank.pk,
                'to_account': self.funds.pk, 'description': 'SIP', 'client_dedup_key': str(uuid.uuid4()),
                'allocations': json.dumps(rows)}
        data.update(kw)
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            return self.client.post(reverse('transfer-create'), data)

    def test_allocates_to_an_existing_and_a_new_fund(self):
        a = self.fund('Alpha')
        response = self.post([{'holding_id': a.pk, 'amount': '6000', 'units': '50'},
                              {'holding_id': None, 'name': 'Brand New Fund', 'amount': '4000', 'units': '20',
                               'scheme_code': '555'}])
        self.assertEqual(response.status_code, 302)
        t = Transfer.objects.get()
        rows = {tx.holding.instrument_name: tx for tx in t.holding_allocations.select_related('holding')}
        self.assertEqual(rows['Alpha'].amount, D('6000.00'))
        self.assertEqual(rows['Alpha'].price, D('120.000000'))
        self.assertEqual(rows['Brand New Fund'].holding.scheme_code, '555')
        a.refresh_from_db()
        self.assertEqual((a.units, a.cost_basis), (D('60.000000'), D('7000.00')))
        self.assertEqual(hs.unallocated_amount(t), D('0.00'))

    def test_a_transfer_without_allocations_still_works(self):
        self.assertEqual(self.post([]).status_code, 302)
        self.assertEqual(Transfer.objects.count(), 1)
        self.assertEqual(HoldingTransaction.objects.count(), 0)

    def test_more_than_the_transfer_is_refused_and_nothing_is_saved(self):
        a = self.fund('Alpha')
        before = a.transactions.count()
        response = self.post([{'holding_id': a.pk, 'amount': '12000', 'units': '50'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transfer.objects.exists())
        self.assertEqual(a.transactions.count(), before)
        self.assertContains(response, 'more than the transfer')

    def test_bad_rows_are_refused(self):
        a = self.fund('Alpha')
        nofeed = Holding.objects.create(account=self.funds, instrument_name='No Feed', units=D('1'),
                                        avg_cost=D('10'), currency='₹')
        for rows in ([{'holding_id': a.pk, 'amount': '0', 'units': '1'}],
                     [{'holding_id': nofeed.pk, 'amount': '100'}],
                     [{'holding_id': None, 'name': '', 'amount': '100', 'units': '1'}],
                     [{'holding_id': 999999, 'amount': '100', 'units': '1'}],
                     ['junk']):
            with self.subTest(rows=rows):
                self.assertEqual(self.post(rows).status_code, 200)
        self.assertFalse(Transfer.objects.exists())
        self.assertEqual(self.post([{'holding_id': a.pk}], allocations='{not json').status_code, 200)

    def test_another_accounts_holding_is_refused(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='DEMAT', balance=D('0'))
        theirs = hs.create_holding(other, name='Elsewhere', units='1', price='10')
        self.assertEqual(self.post([{'holding_id': theirs.pk, 'amount': '100', 'units': '1'}]).status_code, 200)
        self.assertFalse(Transfer.objects.exists())

    def test_allocation_into_an_account_that_ignores_holdings_is_refused(self):
        savings = Account.objects.create(user=self.user, name='Savings2', account_type='SAVINGS_ACCOUNT', balance=D('0'))
        response = self.post([{'holding_id': None, 'name': 'X', 'amount': '100', 'units': '1'}], to_account=savings.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transfer.objects.exists())

    def test_too_many_rows_are_refused(self):
        rows = [{'holding_id': None, 'name': f'F{i}', 'amount': '1', 'units': '1'} for i in range(21)]
        self.assertEqual(self.post(rows, amount='100').status_code, 200)

    def test_net_worth_is_unchanged_by_allocating(self):
        a = self.fund('Alpha')
        self.post([{'holding_id': a.pk, 'amount': '6000', 'units': '60'},
                   {'holding_id': None, 'name': 'Beta', 'amount': '4000', 'units': '40'}])
        Valuation.objects.create(holding=a, value=D('7000'), as_of_date=self.today, unit_nav=D('100'))
        beta = Holding.objects.get(instrument_name='Beta')
        Valuation.objects.create(holding=beta, value=D('4000'), as_of_date=self.today, unit_nav=D('100'))
        total, balances = LedgerReadService.get_net_worth(self.user)
        # bank 90,000 + Alpha (70 units x 100, cost 7,000) + Beta 4,000 + nothing uninvested
        self.assertEqual(D(str(total)), D('101000.00'))

    def test_the_transfer_list_shows_where_the_money_went(self):
        a = self.fund('Alpha')
        self.post([{'holding_id': a.pk, 'amount': '6000', 'units': '60'}])
        response = self.client.get(reverse('transfer-list'))
        self.assertContains(response, 'Alpha')
        self.assertContains(response, 'bi-pie-chart')

    def test_transfer_list_queries_do_not_grow_with_allocations(self):
        a = self.fund('Alpha')
        for _ in range(3):
            self.post([{'holding_id': a.pk, 'amount': '100', 'units': '1'}], amount='100')
        self.client.get(reverse('transfer-list'))
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('transfer-list'))
        for _ in range(4):
            self.post([{'holding_id': a.pk, 'amount': '100', 'units': '1'}], amount='100')
        self.client.get(reverse('transfer-list'))       # the navbar badge cache is rebuilt once after writes
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse('transfer-list'))
        self.assertEqual(len(many), len(few))

    def test_the_form_page_lists_holdings_per_account(self):
        a = self.fund('Alpha')
        response = self.client.get(reverse('transfer-create'))
        self.assertEqual(response.context['allocation_data'][str(self.funds.pk)]['holdings'],
                         [{'id': a.pk, 'name': 'Alpha'}])
        self.assertContains(response, 'id="allocSection"')
        self.assertContains(response, 'id="allocation-data"')


class EditAllocatedTransferTests(Base):
    def setUp(self):
        super().setUp()
        self.a = self.fund('Alpha')
        self.t = self.transfer('10000')
        hs.add_purchase(self.a, units='60', price='100', transfer=self.t, date=self.today)   # 6,000

    def edit(self, **kw):
        data = {'date': self.today.isoformat(), 'amount': '10000', 'from_account': self.bank.pk,
                'to_account': self.funds.pk, 'description': 'edited', 'client_dedup_key': self.t.client_dedup_key or ''}
        data.update(kw)
        return self.client.post(reverse('transfer-edit', args=[self.t.uuid]), data)

    def test_editing_other_details_keeps_the_purchases(self):
        self.assertEqual(self.edit().status_code, 302)
        self.assertEqual(self.t.holding_allocations.count(), 1)

    def test_amount_cannot_drop_below_what_is_allocated(self):
        self.assertEqual(self.edit(amount='5000').status_code, 200)
        self.t.refresh_from_db()
        self.assertEqual(self.t.amount, D('10000.00'))

    def test_the_account_cannot_change_while_allocated(self):
        other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=D('0'))
        self.assertEqual(self.edit(to_account=other.pk).status_code, 200)

    def test_more_can_be_allocated_from_the_edit_page(self):
        b_rows = json.dumps([{'holding_id': None, 'name': 'Beta', 'amount': '4000', 'units': '40'}])
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            self.assertEqual(self.edit(allocations=b_rows).status_code, 302)
        self.assertEqual(self.t.holding_allocations.count(), 2)
        self.assertEqual(hs.unallocated_amount(self.t), D('0.00'))

    def test_the_edit_page_lists_existing_allocations(self):
        response = self.client.get(reverse('transfer-edit', args=[self.t.uuid]))
        self.assertContains(response, 'Alpha')
        self.assertEqual(len(response.context['existing_allocations']), 1)


class HoldingPageTests(Base):
    def setUp(self):
        super().setUp()
        self.h = self.fund('Alpha')
        self.url = reverse('holding-detail', args=[self.h.pk])

    def test_page_lists_purchases_and_figures(self):
        hs.add_purchase(self.h, units='10', price='120', date=self.today)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Alpha')
        self.assertEqual(len(response.context['transactions']), 2)
        self.assertEqual(response.context['cost'], D('2200.00'))

    def test_a_holding_without_purchase_rows_explains_its_opening_position(self):
        legacy = Holding.objects.create(account=self.funds, instrument_name='Legacy', units=D('5'), avg_cost=D('20'), currency='₹')
        response = self.client.get(reverse('holding-detail', args=[legacy.pk]))
        self.assertTrue(response.context['opening_only'])
        self.assertContains(response, 'opening position')

    def test_other_users_and_removed_holdings_are_404(self):
        other = User.objects.create_user(username='nosy', password='password')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.client.force_login(self.user)
        self.h.is_active = False
        self.h.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_funding_options_only_list_transfers_with_money_left(self):
        full = self.transfer('1000')
        hs.add_purchase(self.h, units='10', price='100', transfer=full, date=self.today)
        part = self.transfer('2000')
        hs.add_purchase(self.h, units='5', price='100', transfer=part, date=self.today)
        response = self.client.get(self.url)
        left = {f['transfer'].pk: f['left'] for f in response.context['funding']}
        self.assertEqual(left, {part.pk: D('1500.00')})

    def test_buy_more_with_a_transfer(self):
        t = self.transfer('2000')
        response = self.client.post(reverse('holding-buy', args=[self.h.pk]), {
            'date': self.today.isoformat(), 'units': '10', 'amount': '1000', 'transfer': str(t.uuid)})
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.h.refresh_from_db()
        self.assertEqual(self.h.units, D('20.000000'))
        self.assertEqual(t.holding_allocations.get().amount, D('1000.00'))

    def test_buy_more_bad_input_changes_nothing(self):
        for data in ({'units': '5'}, {'units': '0', 'price': '5'}, {'units': 'x', 'price': '5'},
                     {'units': '5', 'price': '5', 'date': (self.today + timedelta(days=3)).isoformat()}):
            self.client.post(reverse('holding-buy', args=[self.h.pk]), data)
        self.assertEqual(self.h.transactions.count(), 1)

    def test_buy_with_another_users_transfer_is_404(self):
        other = User.objects.create_user(username='nosy', password='password')
        o_bank = Account.objects.create(user=other, name='B', account_type='SAVINGS_ACCOUNT', balance=D('10'))
        o_funds = Account.objects.create(user=other, name='F', account_type='MUTUAL_FUND', balance=D('0'))
        theirs = Transfer.objects.create(user=other, from_account=o_bank, to_account=o_funds, amount=D('5'), date=self.today)
        response = self.client.post(reverse('holding-buy', args=[self.h.pk]), {'units': '1', 'price': '1', 'transfer': str(theirs.uuid)})
        self.assertEqual(response.status_code, 404)

    def test_edit_and_delete_a_purchase(self):
        tx = hs.add_purchase(self.h, units='10', price='120', date=self.today)
        self.client.post(reverse('holding-purchase-edit', args=[tx.pk]), {
            'date': self.today.isoformat(), 'units': '10', 'price': '200', 'amount': '2000', 'note': 'fixed'})
        tx.refresh_from_db()
        self.assertEqual((tx.amount, tx.note), (D('2000.00'), 'fixed'))
        self.h.refresh_from_db()
        self.assertEqual(self.h.avg_cost, D('150.000000'))
        self.client.post(reverse('holding-purchase-delete', args=[tx.pk]))
        self.assertFalse(HoldingTransaction.objects.filter(pk=tx.pk).exists())
        # the last one stays
        last = self.h.transactions.get()
        self.client.post(reverse('holding-purchase-delete', args=[last.pk]))
        self.assertTrue(HoldingTransaction.objects.filter(pk=last.pk).exists())

    def test_purchases_of_other_users_are_404_and_untouched(self):
        other = User.objects.create_user(username='nosy', password='password')
        self.client.force_login(other)
        tx = self.h.transactions.get()
        self.assertEqual(self.client.post(reverse('holding-purchase-delete', args=[tx.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('holding-purchase-edit', args=[tx.pk]), {'units': '1', 'price': '1'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('holding-buy', args=[self.h.pk]), {'units': '1', 'price': '1'}).status_code, 404)
        self.assertEqual(self.client.post(reverse('holding-rename', args=[self.h.pk]), {'instrument_name': 'x'}).status_code, 404)

    def test_rename(self):
        self.client.post(reverse('holding-rename', args=[self.h.pk]), {'instrument_name': 'Renamed'})
        self.h.refresh_from_db()
        self.assertEqual(self.h.instrument_name, 'Renamed')
        self.client.post(reverse('holding-rename', args=[self.h.pk]), {'instrument_name': '  '})
        self.h.refresh_from_db()
        self.assertEqual(self.h.instrument_name, 'Renamed')

    def test_writes_need_post(self):
        for name, args in (('holding-buy', [self.h.pk]), ('holding-rename', [self.h.pk]),
                           ('holding-purchase-edit', [self.h.transactions.get().pk]),
                           ('holding-purchase-delete', [self.h.transactions.get().pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 405, name)

    def test_list_links_to_the_page(self):
        response = self.client.get(reverse('holding-list'))
        self.assertContains(response, self.url)

    def test_holding_page_query_count_is_flat(self):
        self.client.get(self.url)
        with CaptureQueriesContext(connection) as few:
            self.client.get(self.url)
        for i in range(6):
            hs.add_purchase(self.h, units='1', price='100', date=self.today, note=f'n{i}')
        with CaptureQueriesContext(connection) as many:
            self.client.get(self.url)
        self.assertEqual(len(many), len(few))
