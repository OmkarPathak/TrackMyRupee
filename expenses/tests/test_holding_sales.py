"""Selling a holding: proceeds become cash, the profit or loss is recorded in the ledger, net worth stays right."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses import holdings_service as hs
from expenses.account_valuation import holdings_cash_summary
from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    CapitalEvent,
    Holding,
    HoldingTransaction,
    Income,
    Transfer,
    UserProfile,
    Valuation,
)

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='seller', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')
        # 10,000 moved into the fund account and spent on 10 units at 1,000; the NAV is now 1,200
        self.transfer = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.funds,
                                                amount=D('10000'), date=self.today - timedelta(days=30))
        self.fund = hs.create_holding(self.funds, name='Alpha Fund', units='10', price='1000', scheme_code='111',
                                      transfer=self.transfer, date=self.today - timedelta(days=30))
        Valuation.objects.create(holding=self.fund, value=D('12000'), as_of_date=self.today, unit_nav=D('1200'))

    def net_worth(self):
        return D(str(LedgerReadService.get_net_worth(self.user)[0]))

    def sell(self, **kw):
        data = dict(date=self.today, units='4', amount='4800')
        data.update(kw)
        return hs.add_sale(self.fund, **data)

    def ledger(self):
        self.funds.refresh_from_db()
        return D(str(LedgerReadService.get_account_balances([self.funds])[self.funds.id]))


class SaleMathTests(Base):
    def test_starting_point(self):
        self.assertEqual(self.net_worth(), D('102000.00'))              # 90,000 bank + 12,000 fund

    def test_a_profit_becomes_investment_returns_income_and_cash(self):
        tx = self.sell()
        self.fund.refresh_from_db()
        self.assertEqual((self.fund.units, self.fund.avg_cost, self.fund.is_active), (D('6.000000'), D('1000.000000'), True))
        tx.refresh_from_db()
        income = tx.realized_income
        self.assertEqual((income.amount, income.source_type, income.account_id, income.date),
                         (D('800.00'), 'Investment Returns', self.funds.id, self.today))
        self.assertIsNone(tx.realized_loss)
        self.assertEqual(self.ledger(), D('10800.00'))                   # 10,000 in + the 800 profit
        self.assertEqual(self.net_worth(), D('102000.00'))               # selling at the market changes nothing

    def test_a_loss_becomes_a_capital_event_and_cash(self):
        tx = self.sell(amount='3200')                                    # 4 units sold at 800 against a cost of 1,000
        tx.refresh_from_db()
        self.assertEqual((tx.realized_loss.amount, tx.realized_loss.account_id), (D('800.00'), self.funds.id))
        self.assertIsNone(tx.realized_income)
        self.assertEqual(self.ledger(), D('9200.00'))
        self.assertEqual(self.net_worth(), D('100400.00'))               # 4 units worth 4,800 fetched 3,200

    def test_a_sale_at_average_cost_records_nothing(self):
        tx = self.sell(amount='4000')
        tx.refresh_from_db()
        self.assertEqual((tx.realized_income_id, tx.realized_loss_id), (None, None))
        self.assertFalse(Income.objects.filter(account=self.funds).exists())
        self.assertFalse(CapitalEvent.objects.filter(account=self.funds).exists())

    def test_selling_everything_closes_the_holding_and_keeps_the_cash(self):
        self.sell(units='10', amount='12000')
        self.fund.refresh_from_db()
        self.assertEqual((self.fund.units, self.fund.is_active), (D('0.000000'), False))
        self.assertEqual(self.ledger(), D('12000.00'))
        self.assertEqual(self.net_worth(), D('102000.00'))

    def test_two_sales_use_the_running_average_cost(self):
        self.sell(units='5', amount='6000')                              # profit 1,000
        self.sell(units='5', amount='5500')                              # profit 500
        self.assertEqual(sorted(Income.objects.filter(account=self.funds).values_list('amount', flat=True)),
                         [D('500.00'), D('1000.00')])

    def test_a_sale_after_another_purchase_uses_the_blended_average(self):
        hs.add_purchase(self.fund, units='10', price='1400', date=self.today - timedelta(days=10))     # avg 1,200
        tx = self.sell(units='5', amount='7000')
        tx.refresh_from_db()
        self.assertEqual(tx.realized_income.amount, D('1000.00'))        # 7,000 - 5 x 1,200

    def test_price_and_amount_may_be_given_instead_of_units(self):
        tx = hs.add_sale(self.fund, date=self.today, price='1200', amount='3600')
        self.assertEqual(tx.units, D('3.000000'))

    def test_more_than_held_is_refused(self):
        with self.assertRaises(hs.HoldingError):
            self.sell(units='11', amount='13000')
        self.assertEqual(self.fund.transactions.filter(kind='SELL').count(), 0)

    def test_selling_before_buying_is_refused(self):
        with self.assertRaises(hs.HoldingError):
            self.sell(date=self.today - timedelta(days=60))

    def test_a_sale_needs_two_of_three_and_no_future_date(self):
        for kw in ({'units': '1', 'amount': None}, {'units': None, 'amount': '5'}):
            with self.assertRaises(hs.HoldingError):
                hs.add_sale(self.fund, date=self.today, **kw)
        with self.assertRaises(hs.HoldingError):
            self.sell(date=self.today + timedelta(days=1))


class EditingSalesTests(Base):
    def test_a_purchase_a_sale_depends_on_cannot_go_or_shrink(self):
        extra = hs.add_purchase(self.fund, units='5', price='1000', date=self.today - timedelta(days=10))
        self.sell(units='14', amount='16800')
        with self.assertRaises(hs.HoldingError):
            hs.delete_purchase(extra)
        with self.assertRaises(hs.HoldingError):
            hs.update_purchase(extra, units='1', price='1000', date=extra.date)
        hs.update_purchase(extra, units='5', price='1100', date=extra.date)          # cost change is fine

    def test_editing_a_sale_updates_or_flips_what_is_recorded(self):
        tx = self.sell()
        income_id = tx.realized_income_id or HoldingTransaction.objects.get(pk=tx.pk).realized_income_id
        hs.update_purchase(tx, units='4', amount='4400', date=self.today)
        tx.refresh_from_db()
        self.assertEqual(tx.realized_income_id, income_id)                         # same row, new amount
        self.assertEqual(tx.realized_income.amount, D('400.00'))
        hs.update_purchase(tx, units='4', amount='3000', date=self.today)
        tx.refresh_from_db()
        self.assertIsNone(tx.realized_income)
        self.assertEqual(tx.realized_loss.amount, D('1000.00'))
        self.assertFalse(Income.objects.filter(account=self.funds).exists())
        self.assertEqual(self.ledger(), D('9000.00'))

    def test_a_sale_cannot_be_edited_into_selling_more_than_held(self):
        tx = self.sell()
        with self.assertRaises(hs.HoldingError):
            hs.update_purchase(tx, units='11', amount='13200', date=self.today)

    def test_editing_a_sale_with_only_an_amount_does_not_estimate_a_nav(self):
        tx = self.sell()
        with self.assertRaises(hs.HoldingError):
            hs.update_purchase(tx, amount='5000', date=self.today)

    def test_deleting_a_sale_takes_its_profit_out_of_the_ledger(self):
        tx = self.sell()
        hs.delete_purchase(tx)
        self.assertFalse(Income.objects.filter(account=self.funds).exists())
        self.fund.refresh_from_db()
        self.assertEqual(self.fund.units, D('10.000000'))
        self.assertEqual(self.ledger(), D('10000.00'))
        self.assertEqual(self.net_worth(), D('102000.00'))

    def test_changing_an_earlier_purchase_changes_a_later_sales_profit(self):
        opening = self.fund.transactions.order_by('id').first()
        tx = self.sell()                                                           # profit 800 on a cost of 1,000
        hs.update_purchase(opening, units='10', price='900', date=opening.date)    # cost now 900 a unit
        tx.refresh_from_db()
        self.assertEqual(tx.realized_income.amount, D('1200.00'))

    def test_deleting_the_holding_removes_its_sales_profits(self):
        self.sell()
        Holding.objects.filter(pk=self.fund.pk).delete()
        self.assertFalse(Income.objects.filter(account=self.funds).exists())

    def test_running_the_recompute_twice_does_not_duplicate_anything(self):
        self.sell()
        hs.recompute(self.fund.pk)
        hs.recompute(self.fund.pk)
        self.assertEqual(Income.objects.filter(account=self.funds).count(), 1)


class SellViewTests(Base):
    def post(self, **kw):
        data = {'date': self.today.isoformat(), 'units': '4', 'amount': '4800'}
        data.update(kw)
        return self.client.post(reverse('holding-sell', args=[self.fund.pk]), data)

    def messages(self, response):
        return [str(m) for m in response.wsgi_request._messages]

    def test_selling_through_the_page(self):
        response = self.post()
        self.assertRedirects(response, reverse('holding-detail', args=[self.fund.pk]), fetch_redirect_response=False)
        self.assertTrue(any('profit of ₹800.00' in m and '₹4,800.00 is now cash in Funds' in m for m in self.messages(response)),
                        self.messages(response))

    def test_a_loss_message(self):
        response = self.post(amount='3200')
        self.assertTrue(any('loss of ₹800.00' in m for m in self.messages(response)))

    def test_selling_everything_goes_back_to_the_list(self):
        response = self.post(units='10', amount='12000')
        self.assertRedirects(response, reverse('holding-list'), fetch_redirect_response=False)

    def test_bad_input_changes_nothing(self):
        for kw in ({'units': '99', 'amount': '5'}, {'units': '', 'amount': ''}, {'units': 'x', 'amount': '5'},
                   {'date': (self.today + timedelta(days=2)).isoformat()}):
            self.post(**kw)
        self.assertEqual(self.fund.transactions.filter(kind='SELL').count(), 0)

    def test_post_only_and_owner_only(self):
        self.assertEqual(self.client.get(reverse('holding-sell', args=[self.fund.pk])).status_code, 405)
        other = User.objects.create_user(username='nosy', password='password')
        self.client.force_login(other)
        self.assertEqual(self.post().status_code, 404)
        self.assertFalse(HoldingTransaction.objects.filter(kind='SELL').exists())

    def test_the_holding_page_shows_sales_and_the_realised_figure(self):
        self.post()
        response = self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        self.assertContains(response, 'Sold')
        self.assertContains(response, 'Profit')
        self.assertContains(response, 'Realised from sales')
        self.assertEqual(response.context['realized'], D('800.00'))
        self.assertContains(response, 'data-mode="sell"')

    def test_editing_a_sale_through_the_page(self):
        tx = self.sell()
        self.client.post(reverse('holding-purchase-edit', args=[tx.pk]), {
            'date': self.today.isoformat(), 'units': '4', 'amount': '4400', 'price': ''})
        tx.refresh_from_db()
        self.assertEqual(tx.amount, D('4400.00'))
        self.assertEqual(tx.realized_income.amount, D('400.00'))

    def test_holding_page_query_count_is_flat_with_many_sales(self):
        self.sell(units='1', amount='1200')
        self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        for i in range(5):
            self.sell(units='1', amount=str(1100 + i))
        self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        self.assertEqual(len(many), len(few))


class CashSummaryTests(Base):
    def test_uninvested_cash_after_a_sale(self):
        self.sell()
        self.funds.refresh_from_db()
        info = holdings_cash_summary([self.funds])[self.funds.id]
        self.assertEqual((info['ledger'], info['cost'], info['uninvested'], info['shortfall']),
                         (D('10800.00'), D('6000.00'), D('4800.00'), D('0.00')))

    def test_money_waiting_in_the_account(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.funds, amount=D('2500'), date=self.today)
        self.funds.refresh_from_db()
        info = holdings_cash_summary([self.funds])[self.funds.id]
        self.assertEqual((info['uninvested'], info['shortfall']), (D('2500.00'), D('0.00')))

    def test_a_missing_transfer_shows_as_a_shortfall(self):
        Holding.objects.create(account=self.funds, instrument_name='Unfunded', units=D('10'), avg_cost=D('100'), currency='₹')
        self.funds.refresh_from_db()
        info = holdings_cash_summary([self.funds])[self.funds.id]
        self.assertEqual((info['uninvested'], info['shortfall']), (D('0.00'), D('1000.00')))

    def test_several_accounts_cost_no_more_queries_than_one(self):
        for i in range(4):
            other = Account.objects.create(user=self.user, name=f'MF{i}', account_type='MUTUAL_FUND', balance=D('0'))
            Holding.objects.create(account=other, instrument_name=f'H{i}', units=D('1'), avg_cost=D('10'), currency='₹')
        accounts = list(Account.objects.filter(account_type='MUTUAL_FUND'))
        holdings_cash_summary(accounts[:1])
        with CaptureQueriesContext(connection) as one:
            holdings_cash_summary(accounts[:1])
        with CaptureQueriesContext(connection) as many:
            holdings_cash_summary(accounts)
        self.assertEqual(len(many), len(one))

    def test_holdings_page_lists_cash_and_the_total(self):
        self.sell()
        response = self.client.get(reverse('holding-list'))
        self.assertEqual(response.context['total_cash'], D('4800.00'))
        self.assertEqual(response.context['total_with_cash'], response.context['total_valuation'] + D('4800.00'))
        self.assertContains(response, 'id="uninvestedCash"')
        self.assertContains(response, 'Funds')

    def test_holdings_page_warns_about_a_missing_transfer(self):
        Holding.objects.create(account=self.funds, instrument_name='Unfunded', units=D('10'), avg_cost=D('100'), currency='₹')
        response = self.client.get(reverse('holding-list'))
        self.assertContains(response, 'may be missing')
        self.assertEqual(response.context['total_cash'], D('0.00'))

    def test_no_cash_block_when_there_is_nothing_to_show(self):
        self.assertNotContains(self.client.get(reverse('holding-list')), 'id="uninvestedCash"')

    def test_the_account_page_shows_invested_and_uninvested(self):
        self.sell()
        response = self.client.get(reverse('account-detail', args=[self.funds.uuid]))
        self.assertContains(response, 'Invested in holdings:')
        self.assertContains(response, 'Uninvested cash:')
        self.assertEqual(response.context['cash_summary']['uninvested'], D('4800.00'))

    def test_the_account_page_warns_about_a_shortfall(self):
        Holding.objects.create(account=self.funds, instrument_name='Unfunded', units=D('10'), avg_cost=D('100'), currency='₹')
        self.assertContains(self.client.get(reverse('account-detail', args=[self.funds.uuid])), 'may be missing')

    def test_other_accounts_have_no_cash_summary(self):
        response = self.client.get(reverse('account-detail', args=[self.bank.uuid]))
        self.assertIsNone(response.context['cash_summary'])


class HistoryPaginationTests(Base):
    def test_history_is_cards_paginated_with_totals_from_the_whole_history(self):
        for i in range(20):
            hs.add_purchase(self.fund, units='1', price='1000', date=self.today - timedelta(days=i + 1), note=f'n{i}')
        self.sell(units='2', amount='2600')
        url = reverse('holding-detail', args=[self.fund.pk])
        first = self.client.get(url)
        self.assertEqual(len(first.context['transactions']), 15)
        self.assertEqual((first.context['tx_total'], first.context['is_paginated']), (22, True))
        self.assertContains(first, 'card expense-card', count=15)
        self.assertContains(first, '?page=2')
        self.assertEqual(first.context['realized'], D('600.00'))        # the sale is on page 1 but counted regardless
        second = self.client.get(url + '?page=2')
        self.assertEqual(len(second.context['transactions']), 7)
        self.assertEqual(second.context['realized'], D('600.00'))
        self.assertTrue(second.context['has_sales'])

    def test_a_bad_page_number_falls_back_to_a_real_page(self):
        url = reverse('holding-detail', args=[self.fund.pk])
        for page in ('abc', '99', '-1'):
            self.assertEqual(self.client.get(url, {'page': page}).status_code, 200)

    def test_one_page_has_no_pager(self):
        self.assertFalse(self.client.get(reverse('holding-detail', args=[self.fund.pk])).context['is_paginated'])

    def test_the_icon_tile_is_styled_on_this_page(self):
        self.assertContains(self.client.get(reverse('holding-detail', args=[self.fund.pk])),
                            'justify-content: center')

    def test_pending_banner_counts_all_pages(self):
        for i in range(17):
            HoldingTransaction.objects.create(holding=self.fund, kind='BUY', date=self.today - timedelta(days=i + 1),
                                              units=None, price=None, amount=D('100'), status='PENDING')
        response = self.client.get(reverse('holding-detail', args=[self.fund.pk]) + '?page=2')
        self.assertEqual((response.context['pending_count'], response.context['pending_amount']), (17, D('1700.00')))

    def test_page_queries_do_not_grow_with_history(self):
        url = reverse('holding-detail', args=[self.fund.pk])
        self.client.get(url)
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(30):
            hs.add_purchase(self.fund, units='1', price='1000', date=self.today - timedelta(days=i + 1))
        self.client.get(url)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(many), len(few))
