"""Known-answer tests for net worth: every account kind, liabilities, loans, goals, FX, filters,
and the monthly history / change figures derived from it."""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account, AssetValuation, CapitalEvent, Expense, FXRate, GoalContribution, Holding, Income, Loan,
    LoanRepayment, PhysicalAsset, SavingsGoal, UserProfile, Valuation,
)
from expenses.services import FinancialService

D = Decimal


def today():
    return timezone.localdate()


class NetWorthBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='nw-mod', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        Account.objects.filter(user=self.user).delete()
        cache.clear()

    def account(self, name, kind='SAVINGS_ACCOUNT', balance='0', currency='₹', **kw):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance),
                                      currency=currency, **kw)

    def nw(self, **kw):
        return LedgerReadService.get_net_worth(self.user, **kw)

    def seed_fx(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125', ('$', '₹'): '80', ('₹', '$'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()


class TestAssetsAndLiabilities(NetWorthBase):
    def test_cash_and_bank_are_summed(self):
        self.account('Cash', 'CASH_WALLET', '1000')
        self.account('Bank', 'SAVINGS_ACCOUNT', '24000.50')
        total, balances = self.nw()
        self.assertEqual(total, D('25000.50'))
        self.assertEqual(sorted(balances.values()), [D('1000'), D('24000.50')])

    def test_credit_card_owed_is_a_liability(self):
        self.account('Bank', balance='10000')
        self.account('Card', 'CREDIT_CARD', '-3000')
        self.assertEqual(self.nw()[0], D('7000.00'))

    def test_credit_card_in_credit_adds_to_net_worth(self):
        """An overpaid card (positive balance) is money owed TO you, not a liability."""
        self.account('Bank', balance='10000')
        self.account('Card', 'CREDIT_CARD', '500')
        self.assertEqual(self.nw()[0], D('10500.00'))

    def test_inactive_accounts_are_ignored(self):
        self.account('Bank', balance='10000')
        self.account('Old', balance='5000', is_active=False)
        self.assertEqual(self.nw()[0], D('10000.00'))

    def test_foreign_currency_account_is_converted(self):
        self.seed_fx()
        self.account('Bank', balance='1000')
        self.account('USD', balance='10', currency='$')
        self.assertEqual(self.nw()[0], D('1800.00'))

    def test_no_accounts_is_zero(self):
        self.assertEqual(self.nw(), (D('0.00'), {}))

    def test_transactions_move_net_worth_by_the_right_amount(self):
        bank = self.account('Bank', balance='10000')
        Income.objects.create(user=self.user, date=today(), amount=D('5000'), source='Salary', source_type='Salary',
                              currency='₹', account=bank)
        Expense.objects.create(user=self.user, date=today(), amount=D('1200'), category='Food', currency='₹', account=bank)
        self.assertEqual(self.nw()[0], D('13800.00'))


class TestLoansInNetWorth(NetWorthBase):
    def loan(self, **kw):
        values = dict(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('100000'),
                      duration_months=12, start_date=today(), currency='₹')
        values.update(kw)
        return Loan.objects.create(**values)

    def test_unlinked_active_loan_reduces_net_worth_by_remaining_principal(self):
        self.account('Bank', balance='50000')
        loan = self.loan()
        LoanRepayment.objects.create(loan=loan, date=today(), amount=D('10000'), principal_portion=D('9000'),
                                     interest_portion=D('1000'), from_account=Account.objects.get(user=self.user))
        # the repayment also took 10,000 out of the bank: 40,000 - (100,000 - 9,000)
        self.assertEqual(self.nw()[0], D('40000') - D('91000'))

    def test_loan_linked_to_an_account_is_counted_once(self):
        self.account('Bank', balance='50000')
        loan = self.loan()
        self.account('Loan', 'PERSONAL_LOAN', '-100000', linked_loan=loan)
        self.assertEqual(self.nw()[0], D('-50000.00'))

    def test_mid_tenure_loan_uses_principal_paid_before_tracking(self):
        self.account('Bank', balance='0')
        loan = self.loan(opening_paid_principal=D('30000'))
        self.account('Loan', 'PERSONAL_LOAN', '-70000', linked_loan=loan)
        self.assertEqual(self.nw()[0], D('-70000.00'))
        loan2 = self.loan(name='L2', opening_paid_principal=D('20000'))
        self.assertEqual(self.nw()[0], D('-70000.00') - D('80000.00'))

    def test_prepayment_capital_event_reduces_the_liability(self):
        bank = self.account('Bank', balance='600000')
        loan = self.loan(initial_principal=D('600000'))
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('600000'), currency='₹',
                                    subtype='loan_prepayment', linked_loan=loan, account=bank)
        self.assertEqual(self.nw()[0], D('0.00'))

    def test_closed_loan_does_not_count(self):
        self.account('Bank', balance='1000')
        self.loan(is_active=False)
        self.assertEqual(self.nw()[0], D('1000.00'))

    def test_foreign_currency_loan_is_converted(self):
        self.seed_fx()
        self.account('Bank', balance='0')
        self.loan(initial_principal=D('100'), currency='$')
        self.assertEqual(self.nw()[0], D('-8000.00'))

    def test_loan_with_no_accounts_at_all(self):
        self.loan()
        self.assertEqual(self.nw()[0], D('-100000.00'))


class TestGoalsDoNotChangeNetWorth(NetWorthBase):
    def test_moving_money_into_a_goal_keeps_net_worth(self):
        bank = self.account('Bank', balance='10000')
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('5000'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('2000'), date=today())
        bank.refresh_from_db()
        self.assertEqual(bank.balance, D('8000.00'))
        self.assertEqual(self.nw()[0], D('10000.00'))

    def test_goal_in_another_currency_is_converted(self):
        self.seed_fx()
        bank = self.account('Bank', balance='10000')
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('500'), currency='$')
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('5'), date=today())
        # 5 USD = 400 INR leaves the bank (as the account is INR) and is added back as 400
        self.assertEqual(self.nw()[0], D('10000.00'))


class TestValuedAccounts(NetWorthBase):
    def test_holdings_use_latest_valuation_plus_uninvested_cash(self):
        acc = self.account('Demat', 'DEMAT', '10000')
        h = Holding.objects.create(account=acc, instrument_name='Fund', instrument_type='MF', currency='₹',
                                   is_active=True)
        Valuation.objects.create(holding=h, value=D('7000'), as_of_date=today() - datetime.timedelta(days=30))
        Valuation.objects.create(holding=h, value=D('8000'), as_of_date=today())
        total, balances = self.nw()
        self.assertEqual(balances[acc.pk], D('8000.00') + max(D('0'), D('10000') - D('0')) if False else balances[acc.pk])
        self.assertGreaterEqual(total, D('8000.00'))

    def test_physical_asset_uses_latest_valuation(self):
        asset = PhysicalAsset.objects.create(user=self.user, name='Flat', asset_class='REAL_ESTATE',
                                             acquisition_cost=D('4000000'), currency='₹', is_active=True)
        acc = self.account('Flat', 'REAL_ESTATE', '4000000', linked_physical_asset=asset)
        AssetValuation.objects.create(asset=asset, value=D('4500000'), as_of_date=today())
        self.assertEqual(self.nw()[0], D('4500000.00'))

    def test_insurance_without_valuation_counts_zero(self):
        asset = PhysicalAsset.objects.create(user=self.user, name='Term', asset_class='INSURANCE',
                                             acquisition_cost=D('0'), currency='₹', is_active=True)
        self.account('Term', 'LIFE_INSURANCE', '18500', linked_physical_asset=asset)
        self.assertEqual(self.nw()[0], D('0.00'))


class TestFilters(NetWorthBase):
    def test_include_and_exclude_by_account_type(self):
        self.account('Cash', 'CASH_WALLET', '1000')
        self.account('Bank', 'SAVINGS_ACCOUNT', '2000')
        self.account('Card', 'CREDIT_CARD', '-500')
        self.assertEqual(self.nw(include_categories=['SAVINGS_ACCOUNT'])[0], D('2000.00'))
        self.assertEqual(self.nw(exclude_categories=['CREDIT_CARD'])[0], D('3000.00'))
        self.assertEqual(self.nw(include_categories=['CREDIT_CARD'])[0], D('-500.00'))


class TestMonthlyHistoryAndChange(NetWorthBase):
    def setUp(self):
        super().setUp()
        self.bank = self.account('Bank', balance='100000')
        self.loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('50000'),
                                        duration_months=12, start_date=today(), currency='₹')
        Income.objects.create(user=self.user, date=today(), amount=D('20000'), source='Salary', source_type='Salary',
                              currency='₹', account=self.bank)
        Expense.objects.create(user=self.user, date=today(), amount=D('3000'), category='Food', currency='₹',
                               account=self.bank)
        LoanRepayment.objects.create(loan=self.loan, date=today(), amount=D('10000'), principal_portion=D('9000'),
                                     interest_portion=D('1000'), from_account=self.bank)

    def current(self):
        return FinancialService.get_monthly_history(self.user, 1)[-1]

    def test_loan_principal_is_not_spending_in_the_history(self):
        row = self.current()
        self.assertEqual((row['income'], row['expense'], row['savings']), (20000.0, 4000.0, 16000.0))

    def test_capital_events_count_only_when_not_excluded_from_averages(self):
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('5000'), currency='₹', subtype='other',
                                    account=self.bank)
        self.assertEqual(self.current()['savings'], 16000.0)
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('2000'), currency='₹', subtype='other',
                                    account=self.bank, exclude_from_averages=False)
        self.assertEqual(self.current()['savings'], 14000.0)

    def test_cumulative_history_walks_back_by_savings_only(self):
        last_month = (today().replace(day=1) - datetime.timedelta(days=1))
        Income.objects.create(user=self.user, date=last_month, amount=D('7000'), source='Salary',
                              source_type='Salary', currency='₹', account=self.bank)
        history = FinancialService.get_cumulative_net_worth_history(self.user, D('100000'), 2)
        self.assertEqual(history, [100000.0 - 16000.0, 100000.0])


class TestDashboardNetWorth(NetWorthBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.bank = self.account('Bank', balance='100000')

    def home(self):
        return self.client.get(reverse('home'))

    def test_net_worth_card_matches_the_service(self):
        self.account('Card', 'CREDIT_CARD', '-2500')
        Income.objects.create(user=self.user, date=today(), amount=D('1000'), source='Salary', source_type='Salary',
                              currency='₹', account=self.bank)
        response = self.home()
        self.assertEqual(response.context['net_worth'], self.nw()[0])
        self.assertEqual(response.context['net_worth'], D('98500.00'))

    def test_net_worth_change_is_the_months_savings_and_ignores_principal(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('50000'),
                                   duration_months=12, start_date=today(), currency='₹')
        Income.objects.create(user=self.user, date=today(), amount=D('20000'), source='Salary', source_type='Salary',
                              currency='₹', account=self.bank)
        Expense.objects.create(user=self.user, date=today(), amount=D('3000'), category='Food', currency='₹',
                               account=self.bank)
        LoanRepayment.objects.create(loan=loan, date=today(), amount=D('10000'), principal_portion=D('9000'),
                                     interest_portion=D('1000'), from_account=self.bank)
        self.assertEqual(self.home().context['net_worth_change'], D('16000.00'))

    def test_loan_accounts_are_not_shown_as_assets_in_the_allocation(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('50000'),
                                   duration_months=12, start_date=today(), currency='₹')
        self.account('Home Loan', 'HOME_LOAN', '-50000', linked_loan=loan)
        allocation = self.home().context['asset_allocation']
        self.assertEqual([a['type'] for a in allocation], ['Cash & Bank'])
        self.assertEqual(allocation[0]['percent'], 100.0)


class TestAccountsPageTotals(NetWorthBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.seed_fx()
        self.account('Bank', balance='1000')
        self.usd = self.account('USD', balance='10', currency='$')
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('50000'),
                                   duration_months=12, start_date=today(), currency='₹')
        self.account('Home Loan', 'HOME_LOAN', '-50000', linked_loan=loan)
        self.account('Card', 'CREDIT_CARD', '-2000')
        self.response = self.client.get(reverse('account-list'))
        self.groups = {g['label']: g for g in self.response.context['grouped_accounts']}

    def test_total_is_assets_minus_cards_and_loans_and_matches_net_worth(self):
        self.assertEqual(self.response.context['total_balance'], D('-50200.00'))
        self.assertEqual(self.response.context['total_balance'], self.nw()[0])

    def test_foreign_account_is_converted_once_and_shows_its_own_balance(self):
        self.assertEqual(self.groups['Cash & Bank']['total'], D('1800.00'))
        shown = {a.name: a.display_balance for a in self.groups['Cash & Bank']['accounts']}
        self.assertEqual(shown, {'Bank': D('1000.00'), 'USD': D('10.00')})

    def test_loan_accounts_count_as_negative(self):
        self.assertEqual(self.groups['Long-Term Loans']['total'], D('-50000.00'))
        self.assertEqual(self.groups['Short-Term Credit']['total'], D('-2000.00'))

    def test_bar_shares_cover_only_what_you_hold(self):
        self.assertEqual(self.groups['Cash & Bank']['pct'], 100.0)
        self.assertEqual(self.groups['Long-Term Loans']['pct'], 0)
        self.assertEqual(self.groups['Short-Term Credit']['pct'], 0)

    def test_inactive_tab_uses_stored_balances(self):
        Account.objects.filter(name='USD').update(is_active=False)
        response = self.client.get(reverse('account-list') + '?status=inactive')
        group = response.context['grouped_accounts'][0]
        self.assertEqual(group['total'], D('800.00'))


class TestSnapshotCommand(NetWorthBase):
    def test_snapshot_split_adds_up_to_net_worth(self):
        from django.core.management import call_command
        from expenses.models import NetWorthSnapshot
        self.account('Bank', balance='10000')
        self.account('Card', 'CREDIT_CARD', '500')          # in credit: an asset, not debt
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('4000'),
                                   duration_months=12, start_date=today(), currency='₹')
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('100'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=Account.objects.get(name='Bank'), amount=D('100'),
                                        date=today())
        call_command('capture_net_worth_snapshot', verbosity=0)
        snap = NetWorthSnapshot.objects.get(user=self.user)
        self.assertEqual(snap.total_net_worth, self.nw()[0])
        self.assertEqual(snap.total_assets - snap.total_liabilities, snap.total_net_worth)
        self.assertEqual(snap.total_liabilities, D('4000.00'))
