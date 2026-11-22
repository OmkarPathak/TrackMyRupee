"""Accounts > Summary report: the investments and liabilities summary agrees with the net worth, shows holdings with their
gains, deposits, loans and cards as liabilities and the active SIPs per fund, and is a page made to be printed."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, FundNAVCache, Holding, Loan, RecurringFundSplit, RecurringTransaction, Valuation
from expenses.portfolio_summary import MONTHLY_FACTOR, build_portfolio_summary

D = Decimal


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('summary', 's@example.com', 'pw')
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def account(self, name, kind='SAVINGS_ACCOUNT', balance='0', **extra):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency='₹', **extra)

    def holding(self, account, name, units, cost, nav=None, kind='MF'):
        holding = Holding.objects.create(account=account, instrument_name=name, units=D(units), avg_cost=D(cost),
                                         instrument_type=kind, currency='₹')
        if nav is not None:
            Valuation.objects.create(holding=holding, value=D(units) * D(nav), unit_nav=D(nav), as_of_date=self.today)
        return holding

    def rows(self, summary, title):
        block = next(b for b in summary['blocks'] if b['title'] == title)
        return {row['name']: row for row in block['rows']}


class HeadlineTests(Base):
    def test_it_agrees_with_the_net_worth_everywhere_else(self):
        self.account('HDFC Savings', balance='100000')
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.holding(funds, 'Winner Fund', '10', '100', nav='120')
        card = self.account('Visa', 'CREDIT_CARD', balance='-20000')
        summary = build_portfolio_summary(self.user)
        net_worth, _balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(summary['net_worth'], net_worth)
        self.assertEqual(summary['assets'] - summary['liabilities'], net_worth)
        self.assertEqual(card.balance, D('-20000'))

    def test_a_person_with_nothing_has_an_empty_summary(self):
        summary = build_portfolio_summary(self.user)
        self.assertTrue(summary['is_empty'])
        self.assertEqual((summary['blocks'], summary['liability_rows'], summary['sip_rows']), ([], [], []))
        self.assertIsNone(summary['returns'])


class InvestmentTests(Base):
    def setUp(self):
        super().setUp()
        self.funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.winner = self.holding(self.funds, 'Winner Fund', '10', '100', nav='120')
        self.loser = self.holding(self.funds, 'Loser Fund', '10', '100', nav='80')

    def test_each_holding_shows_its_value_and_gain(self):
        rows = self.rows(build_portfolio_summary(self.user), 'Mutual Funds')
        self.assertEqual((rows['Winner Fund']['value'], rows['Winner Fund']['gain_pct']), (D('1200.00'), D('20.0')))
        self.assertEqual((rows['Loser Fund']['value'], rows['Loser Fund']['gain_pct']), (D('800.00'), D('-20.0')))

    def test_the_account_total_and_its_gain_are_worked_out_from_its_holdings(self):
        summary = build_portfolio_summary(self.user)
        block = next(b for b in summary['blocks'] if b['title'] == 'Mutual Funds')
        self.assertEqual(block['total'], D('2000.00'))
        self.assertEqual(block['gain_pct'], D('0.0'))             # +200 and -200 on 2,000 cost

    def test_invested_and_returns_use_the_cost_where_it_is_recorded(self):
        summary = build_portfolio_summary(self.user)
        self.assertEqual(summary['invested'], D('2000.00'))
        self.assertEqual(summary['returns'], D('0.00'))
        self.assertEqual(summary['returns_pct'], D('0.0'))
        self.holding(self.funds, 'Third Fund', '10', '100', nav='150')
        later = build_portfolio_summary(self.user)
        self.assertEqual((later['invested'], later['returns'], later['returns_pct']), (D('3000.00'), D('500.00'), D('16.7')))

    def test_a_holding_with_no_valuation_counts_at_cost_and_has_no_gain(self):
        self.holding(self.funds, 'New Fund', '10', '100')
        summary = build_portfolio_summary(self.user)
        row = self.rows(summary, 'Mutual Funds')['New Fund']
        self.assertEqual((row['value'], row['gain_pct']), (D('1000.00'), None))

    def test_cash_left_in_the_account_is_listed_so_the_rows_add_up(self):
        self.funds.balance = D('500')
        self.funds.save()
        self.account('Wallet', 'CASH_WALLET', balance='0')
        summary = build_portfolio_summary(self.user)
        block = next(b for b in summary['blocks'] if b['title'] == 'Mutual Funds')
        self.assertEqual(sum(r['value'] for r in block['rows']), block['total'])

    def test_a_sold_out_holding_is_not_listed(self):
        self.loser.is_active = False
        self.loser.save()
        self.assertNotIn('Loser Fund', self.rows(build_portfolio_summary(self.user), 'Mutual Funds'))

    def test_the_blocks_come_biggest_first_with_their_share_of_assets(self):
        self.account('HDFC Savings', balance='8000')
        summary = build_portfolio_summary(self.user)
        self.assertEqual([b['title'] for b in summary['blocks']], ['Cash and other assets', 'Mutual Funds'])
        self.assertEqual([b['share_pct'] for b in summary['blocks']], [D('80'), D('20')])

    def test_the_allocation_bar_is_assets_by_type_and_adds_up(self):
        self.account('HDFC Savings', balance='8000')
        allocation = build_portfolio_summary(self.user)['allocation']
        self.assertEqual(sum(item['percent'] for item in allocation), D('100.0'))
        self.assertEqual([item['color'] for item in allocation], [0, 1])


class HoldingValueAgreesWithNetWorthTests(Base):
    """A holding is worth the same in the report as in the net worth the rows are meant to add up to."""

    def block(self, summary, title='Mutual Funds'):
        return next(b for b in summary['blocks'] if b['title'] == title)

    def test_a_holding_with_no_valuation_but_a_cached_nav_is_worth_units_times_that_nav(self):
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        holding = Holding.objects.create(account=funds, instrument_name='Cached Fund', units=D('10'), avg_cost=D('100'),
                                         instrument_type='MF', currency='₹', scheme_code='999001')
        FundNAVCache.objects.create(scheme_code='999001', latest_nav=D('120.0000'))
        summary = build_portfolio_summary(self.user)
        row = {r['name']: r for r in self.block(summary)['rows']}['Cached Fund']
        self.assertEqual((row['value'], row['gain_pct']), (D('1200.00'), D('20.0')))
        names = [r['name'] for r in self.block(summary)['rows']]
        self.assertNotIn('Cash in this account', names)
        self.assertEqual(sum(r['value'] for r in self.block(summary)['rows']), self.block(summary)['total'])
        self.assertTrue(holding.pk)

    def test_a_recorded_valuation_beats_the_cached_nav(self):
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        holding = self.holding(funds, 'Valued Fund', '10', '100', nav='130')
        holding.scheme_code = '999002'
        holding.save()
        FundNAVCache.objects.create(scheme_code='999002', latest_nav=D('120.0000'))
        row = {r['name']: r for r in self.block(build_portfolio_summary(self.user))['rows']}['Valued Fund']
        self.assertEqual(row['value'], D('1300.00'))

    def test_an_account_shown_at_cost_lists_its_holdings_at_cost(self):
        """(an account with 'show accrued balance' off is worth its cost in net worth, so its rows are too)"""
        funds = self.account('Mutual Funds', 'MUTUAL_FUND', show_accrued_balance=False)
        self.holding(funds, 'Winner Fund', '10', '100', nav='120')
        summary = build_portfolio_summary(self.user)
        block = self.block(summary)
        row = {r['name']: r for r in block['rows']}['Winner Fund']
        self.assertEqual((row['value'], row['gain_pct']), (D('1000.00'), None))
        self.assertEqual(block['total'], D('1000.00'))
        self.assertEqual(sum(r['value'] for r in block['rows']), block['total'])
        self.assertIsNone(summary['returns'])          # (no valuation is counted, so there is no gain to report)


class PercentagesTests(Base):
    """The allocation bar and the blocks' shares are shares of what is listed, so they add up to 100%."""

    def overdrawn(self):
        self.account('HDFC Savings', balance='120000')
        self.account('Old Overdrawn', balance='-20000')

    def test_the_bar_adds_up_to_100_when_one_account_is_overdrawn(self):
        self.overdrawn()
        summary = build_portfolio_summary(self.user)
        self.assertEqual(sum(item['percent'] for item in summary['allocation']), D('100.0'))

    def test_the_blocks_shares_add_up_to_100_and_none_is_negative(self):
        self.overdrawn()
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.holding(funds, 'Fund', '10', '100', nav='100')
        summary = build_portfolio_summary(self.user)
        shares = [b['share_pct'] for b in summary['blocks'] if b['share_pct'] is not None]
        self.assertTrue(all(share >= 0 for share in shares), shares)
        self.assertTrue(D('99.5') <= sum(shares) <= D('100.5'), shares)

    def test_a_block_that_takes_away_from_the_total_has_no_share(self):
        self.overdrawn()
        summary = build_portfolio_summary(self.user)
        negative = [b for b in summary['blocks'] if b['total'] < 0]
        self.assertTrue(all(b['share_pct'] is None for b in negative))

    def test_ordinary_assets_are_unchanged(self):
        self.account('HDFC Savings', balance='8000')
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.holding(funds, 'Fund', '10', '100', nav='100')
        shares = {b['title']: b['share_pct'] for b in build_portfolio_summary(self.user)['blocks']}
        self.assertEqual(shares, {'Cash and other assets': D('89'), 'Mutual Funds': D('11')})


class DepositTests(Base):
    def test_a_deposit_shows_its_rate_maturity_and_value(self):
        self.account('Bank FD', 'FD', balance='100000', deposit_principal=D('100000'), deposit_rate=D('7.1'),
                     deposit_start_date=self.today - timedelta(days=365),
                     deposit_maturity_date=self.today + timedelta(days=365))
        summary = build_portfolio_summary(self.user)
        row = self.rows(summary, 'Deposits and schemes')['Bank FD']
        self.assertGreaterEqual(row['value'], D('100000'))
        self.assertIn('7.1%', row['subtitle'])
        self.assertIn('matures', row['subtitle'])
        self.assertEqual(summary['invested'], D('100000.00'))


class LiabilityTests(Base):
    def test_a_card_and_a_loan_are_the_liabilities_and_add_up_to_the_total(self):
        self.account('HDFC Savings', balance='100000')
        self.account('Visa', 'CREDIT_CARD', balance='-20000')
        Loan.objects.create(user=self.user, name='Home loan', loan_type='HOME', initial_principal=D('500000'),
                            duration_months=120, start_date=self.today, currency='₹')
        summary = build_portfolio_summary(self.user)
        names = {row['name']: row for row in summary['liability_rows']}
        self.assertEqual(names['Visa']['value'], D('20000.00'))
        self.assertEqual(names['Home loan']['value'], D('500000.00'))
        self.assertEqual(names['Home loan']['subtitle'].split(' · ')[0], 'Home Loan')
        self.assertEqual(sum(row['value'] for row in summary['liability_rows']), summary['liabilities'])

    def test_a_loan_account_linked_to_a_loan_is_listed_with_what_is_owed(self):
        """(net worth keeps a loan account's value as a positive outstanding figure, not a negative balance)"""
        loan = Loan.objects.create(user=self.user, name='Home loan', loan_type='HOME', initial_principal=D('500000'),
                                   duration_months=120, start_date=self.today, currency='₹')
        self.account('Home loan account', 'HOME_LOAN', balance='-500000', linked_loan=loan)
        summary = build_portfolio_summary(self.user)
        rows = {row['name']: row for row in summary['liability_rows']}
        self.assertEqual(rows['Home loan account']['value'], D('500000.00'))
        self.assertEqual(rows['Home loan account']['subtitle'].split(' · ')[0], 'Home Loan')
        self.assertNotIn('Other', rows)
        self.assertEqual(sum(row['value'] for row in summary['liability_rows']), summary['liabilities'])

    def test_a_loan_account_with_no_loan_behind_it_is_listed_too(self):
        self.account('Car loan', 'VEHICLE_LOAN', balance='-300000')
        summary = build_portfolio_summary(self.user)
        rows = {row['name']: row['value'] for row in summary['liability_rows']}
        self.assertEqual(rows, {'Car loan': D('300000.00')})
        self.assertEqual(summary['liabilities'], D('300000.00'))

    def test_a_loan_that_is_paid_off_is_not_listed(self):
        Loan.objects.create(user=self.user, name='Old loan', loan_type='CAR', initial_principal=D('1000'),
                            opening_paid_principal=D('1000'), duration_months=12, start_date=self.today, currency='₹')
        self.assertEqual(build_portfolio_summary(self.user)['liability_rows'], [])


class SipTests(Base):
    def setUp(self):
        super().setUp()
        self.bank = self.account('HDFC Savings', balance='100000')
        self.funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.winner = self.holding(self.funds, 'Winner Fund', '10', '100', nav='120')

    def sip(self, amount, frequency='MONTHLY', **extra):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='TRANSFER', amount=D(amount), currency='₹', base_amount=D(amount),
            description='SIP', frequency=frequency, start_date=self.today, from_account=self.bank, to_account=self.funds,
            **extra)

    def test_a_split_sip_lists_each_fund_and_the_cash_left_over(self):
        schedule = self.sip('5000')
        RecurringFundSplit.objects.create(recurring=schedule, holding=self.winner, amount=D('3000'))
        summary = build_portfolio_summary(self.user)
        rows = {row['name']: row['value'] for row in summary['sip_rows']}
        self.assertEqual(rows, {'Winner Fund': D('3000.00'), 'Cash (not in a fund)': D('2000.00')})
        self.assertEqual(summary['monthly_sip'], D('5000.00'))

    def test_a_sip_without_a_split_is_one_row(self):
        self.sip('5000')
        self.assertEqual([(r['name'], r['value']) for r in build_portfolio_summary(self.user)['sip_rows']], [('SIP', D('5000.00'))])

    def test_other_frequencies_are_shown_as_a_months_worth(self):
        self.sip('3000', 'QUARTERLY')
        self.assertEqual(build_portfolio_summary(self.user)['monthly_sip'], D('1000.00'))
        self.assertEqual(MONTHLY_FACTOR['YEARLY'] * 12, D(1))

    def test_a_schedule_that_has_ended_or_is_off_is_left_out(self):
        self.sip('5000', end_date=self.today - timedelta(days=1))
        self.sip('4000', is_active=False)
        self.assertEqual(build_portfolio_summary(self.user)['sip_rows'], [])

    def test_a_transfer_between_two_bank_accounts_is_not_a_sip(self):
        other = self.account('ICICI Savings', balance='1000')
        RecurringTransaction.objects.create(user=self.user, transaction_type='TRANSFER', amount=D('500'), currency='₹',
                                            base_amount=D('500'), description='Move', frequency='MONTHLY',
                                            start_date=self.today, from_account=self.bank, to_account=other)
        self.assertEqual(build_portfolio_summary(self.user)['sip_rows'], [])


class PageTests(Base):
    def test_it_needs_a_login(self):
        self.client.logout()
        response = self.client.get(reverse('portfolio-summary'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_the_page_shows_the_figures_and_a_print_button(self):
        self.account('HDFC Savings', balance='2500000')
        html = self.client.get(reverse('portfolio-summary')).content.decode()
        self.assertIn('Portfolio summary', html)
        self.assertIn('window.print()', html)
        self.assertIn('Print / Save as PDF', html)
        net_worth = html.split('data-psr="net-worth">', 1)[1].split('</div>', 1)[0]
        self.assertEqual(net_worth, '₹25,00,000')

    def test_the_chrome_is_hidden_when_printing(self):
        html = self.client.get(reverse('portfolio-summary')).content.decode()
        self.assertIn('@media print', html)
        self.assertIn('aside#sidebar', html)
        self.assertIn('class="psr-actions no-print"', html)

    def test_the_page_is_never_cached(self):
        self.assertIn('no-store', self.client.get(reverse('portfolio-summary'))['Cache-Control'])

    def test_a_person_with_nothing_is_invited_to_add_an_account(self):
        html = self.client.get(reverse('portfolio-summary')).content.decode()
        self.assertIn('Add an account, a holding or a loan and your summary will appear here.', html)
        self.assertIn(reverse('account-create'), html)

    def test_only_the_persons_own_accounts_are_in_it(self):
        other = User.objects.create_user('other', 'o@example.com', 'pw')
        Account.objects.create(user=other, name='Secret Savings', account_type='SAVINGS_ACCOUNT', balance=D('999'), currency='₹')
        self.account('Mine', balance='100')
        html = self.client.get(reverse('portfolio-summary')).content.decode()
        self.assertIn('Mine', html)
        self.assertNotIn('Secret Savings', html)

    def test_the_accounts_page_links_to_it(self):
        html = self.client.get(reverse('account-list')).content.decode()
        self.assertIn(reverse('portfolio-summary'), html)
        self.assertIn('Summary report', html)

    def test_it_loads_in_a_fixed_number_of_queries(self):
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        for index in range(6):
            self.holding(funds, f'Fund {index}', '10', '100', nav='110')
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.get(reverse('portfolio-summary'))
        with CaptureQueriesContext(connection) as small:
            self.client.get(reverse('portfolio-summary'))
        for index in range(6, 20):
            self.holding(funds, f'Fund {index}', '10', '100', nav='110')
        with CaptureQueriesContext(connection) as large:
            self.client.get(reverse('portfolio-summary'))
        self.assertEqual(len(small), len(large), 'the number of queries grows with the number of holdings')
