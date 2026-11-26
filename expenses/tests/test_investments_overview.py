"""build_investments_overview is the one place the investment screens get their figures from: the printable summary report
is built on it, so the two cannot disagree, and both agree with the net worth."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from expenses.investments_overview import (
    active_accounts,
    allocation_by_group,
    build_investments_overview,
    split_accounts,
)
from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, Holding, RecurringTransaction, Valuation
from expenses.portfolio_summary import build_portfolio_summary

D = Decimal


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('overview', 'o@example.com', 'pw')
        self.user.profile.tier = 'PRO'
        self.user.profile.save()
        self.today = timezone.localdate()
        self.bank = self.account('HDFC Savings', balance='100000')
        self.funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.demat = self.account('Demat', 'DEMAT')
        self.fd = self.account('Bank FD', 'FD', balance='100000', deposit_principal=D('100000'), deposit_rate=D('7.1'),
                               deposit_start_date=self.today - timedelta(days=365),
                               deposit_maturity_date=self.today + timedelta(days=365))
        self.ppf = self.account('PPF', 'PPF', balance='50000')
        self.holding(self.funds, 'Winner Fund', '10', '100', nav='120')
        self.holding(self.funds, 'Loser Fund', '10', '100', nav='80')
        self.holding(self.demat, 'HDFC Bank', '5', '1000', nav='1100', kind='STOCK')

    def account(self, name, kind='SAVINGS_ACCOUNT', balance='0', **extra):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency='₹', **extra)

    def holding(self, account, name, units, cost, nav=None, kind='MF'):
        holding = Holding.objects.create(account=account, instrument_name=name, units=D(units), avg_cost=D(cost),
                                         instrument_type=kind, currency='₹')
        if nav is not None:
            Valuation.objects.create(holding=holding, value=D(units) * D(nav), unit_nav=D(nav), as_of_date=self.today)
        return holding

    def sip(self, amount, frequency='MONTHLY', to=None):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='TRANSFER', amount=D(amount), currency='₹', base_amount=D(amount),
            description='SIP', frequency=frequency, start_date=self.today, from_account=self.bank, to_account=to or self.funds)


class FiguresTests(Base):
    def test_holding_blocks_hold_one_block_per_account_with_their_values(self):
        titles = {b['title']: b for b in build_investments_overview(self.user)['holding_blocks']}
        self.assertEqual(set(titles), {'Mutual Funds', 'Demat'})
        self.assertEqual(titles['Mutual Funds']['total'], D('2000.00'))
        self.assertEqual(titles['Demat']['total'], D('5500.00'))

    def test_deposits_and_schemes_are_listed_with_their_rate(self):
        rows = {r['name']: r for r in build_investments_overview(self.user)['deposit_rows']}
        self.assertEqual(set(rows), {'Bank FD', 'PPF'})
        self.assertIn('7.1%', rows['Bank FD']['subtitle'])

    def test_invested_is_what_was_put_in_and_the_value_is_the_sum_of_the_rows(self):
        overview = build_investments_overview(self.user)
        self.assertIsNotNone(overview['returns'])
        self.assertEqual(overview['investments_value'],
                         sum((b['total'] for b in overview['holding_blocks']), D('0')) + sum((r['value'] for r in overview['deposit_rows']), D('0')))
        self.assertGreater(overview['invested'], D('100000'))        # at least the FD's principal

    def test_the_allocation_adds_up_and_is_biggest_first(self):
        allocation = build_investments_overview(self.user)['allocation']
        self.assertEqual([a['amount'] for a in allocation], sorted((a['amount'] for a in allocation), reverse=True))
        self.assertAlmostEqual(float(sum(a['percent'] for a in allocation)), 100, delta=1.0)
        self.assertEqual(allocation[0]['color'], 0)

    def test_sips_are_monthly_amounts(self):
        self.sip('5000')
        self.sip('1000', 'WEEKLY', to=self.demat)
        overview = build_investments_overview(self.user)
        self.assertEqual(len(overview['sip_rows']), 2)
        self.assertEqual(overview['monthly_sip'], D('5000.00') + (D('1000') * D(52) / D(12)).quantize(D('0.01')))

    def test_a_person_with_nothing_has_an_empty_overview(self):
        overview = build_investments_overview(User.objects.create_user('nobody', 'n@example.com', 'pw'))
        self.assertEqual((overview['holding_blocks'], overview['deposit_rows'], overview['sip_rows'], overview['allocation']), ([], [], [], []))
        self.assertIsNone(overview['returns'])
        self.assertEqual(overview['monthly_sip'], D('0.00'))


class AgreementTests(Base):
    def test_the_summary_report_is_built_from_it_and_agrees(self):
        self.sip('5000')
        overview = build_investments_overview(self.user)
        summary = build_portfolio_summary(self.user)
        for key in ('investments_value', 'invested', 'returns', 'returns_pct', 'monthly_sip', 'allocation', 'sip_rows'):
            self.assertEqual(summary[key], overview[key], key)

    def test_it_never_exceeds_the_net_worths_assets(self):
        detail = {}
        LedgerReadService.get_net_worth(self.user, detail=detail)
        overview = build_investments_overview(self.user)
        self.assertLessEqual(overview['investments_value'], D(str(detail['assets'])) + D('0.5'))

    def test_given_the_balances_it_gives_the_same_figures(self):
        accounts = active_accounts(self.user)
        _nw, balances = LedgerReadService.get_net_worth(self.user)
        given = build_investments_overview(self.user, accounts=accounts, balances=balances)
        alone = build_investments_overview(self.user)
        self.assertEqual(given['investments_value'], alone['investments_value'])
        self.assertEqual(given['allocation'], alone['allocation'])


class HelperTests(Base):
    def test_accounts_are_split_by_how_they_are_valued(self):
        holdings, deposits, other = split_accounts(active_accounts(self.user))
        self.assertEqual({a.name for a in holdings}, {'Mutual Funds', 'Demat'})
        self.assertEqual({a.name for a in deposits}, {'Bank FD', 'PPF'})
        self.assertEqual({a.name for a in other}, {'HDFC Savings'})

    def test_a_credit_card_is_not_in_the_allocation(self):
        card = self.account('Visa', 'CREDIT_CARD', balance='-20000')
        _nw, balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(allocation_by_group([card], balances), [])


class DepositsAndSchemesTests(Base):
    def setUp(self):
        super().setUp()
        self.rd = self.account('Post Office RD', 'RD', balance='20000', deposit_rate=D('6.7'),
                               deposit_maturity_date=self.today + timedelta(days=30))
        self.ssy = self.account('SSY', 'SSY', balance='110000', deposit_rate=D('8.2'),
                                deposit_maturity_date=self.today + timedelta(days=4000))

    def test_deposits_are_the_fixed_and_recurring_ones_and_schemes_the_rest(self):
        overview = build_investments_overview(self.user)
        self.assertEqual({r['name'] for r in overview['deposits']}, {'Bank FD', 'Post Office RD'})
        self.assertEqual({r['name'] for r in overview['schemes']}, {'PPF', 'SSY'})
        self.assertEqual(len(overview['deposits']) + len(overview['schemes']), len(overview['deposit_rows']))

    def test_a_row_carries_what_the_tabs_show(self):
        fd = next(r for r in build_investments_overview(self.user)['deposits'] if r['name'] == 'Bank FD')
        self.assertEqual(fd['type'], 'FD')
        self.assertEqual(fd['rate'], D('7.1'))
        self.assertEqual(fd['maturity'], self.today + timedelta(days=365))
        self.assertEqual(fd['invested'], D('100000.00'))
        self.assertEqual(fd['gain'], fd['value'] - D('100000.00'))
        self.assertEqual(fd['account_uuid'], self.fd.uuid)

    def test_a_scheme_without_a_recorded_cost_has_no_invested_or_gain(self):
        ppf = next(r for r in build_investments_overview(self.user)['schemes'] if r['name'] == 'PPF')
        self.assertIsNone(ppf['rate'])
        self.assertIsNone(ppf['maturity'])

    def test_a_closed_deposit_has_no_maturity(self):
        self.fd.deposit_closed_date = self.today
        self.fd.save()
        fd = next((r for r in build_investments_overview(self.user)['deposit_rows'] if r['name'] == 'Bank FD'), None)
        if fd:
            self.assertIsNone(fd['maturity'])

    def test_upcoming_lists_the_soonest_maturities_first(self):
        upcoming = build_investments_overview(self.user)['upcoming']
        self.assertEqual([u['name'] for u in upcoming], ['Post Office RD', 'Bank FD', 'SSY'])
        self.assertTrue(all(u['kind'] == 'maturity' for u in upcoming))

    def test_upcoming_leaves_out_past_maturities_and_is_limited(self):
        self.rd.deposit_maturity_date = self.today - timedelta(days=1)
        self.rd.save()
        names = [u['name'] for u in build_investments_overview(self.user)['upcoming']]
        self.assertNotIn('Post Office RD', names)
        for n in range(8):
            self.account(f'FD {n}', 'FD', balance='1000', deposit_maturity_date=self.today + timedelta(days=10 + n))
        self.assertEqual(len(build_investments_overview(self.user)['upcoming']), 5)

    def test_the_report_rows_keep_their_old_fields(self):
        row = build_portfolio_summary(self.user)['blocks']
        titles = [b['title'] for b in row]
        self.assertIn('Deposits and schemes', titles)
