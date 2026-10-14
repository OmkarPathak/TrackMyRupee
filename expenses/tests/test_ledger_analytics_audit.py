"""Audit of the ledger and of the analytics built on it: net worth on a past date really is the net worth on
that date, the monthly numbers follow from it, and every posting is balanced, repeatable and consistent."""
import datetime
import json
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.management.commands.send_monthly_report import Command as MonthlyReport
from expenses.models import (
    Account,
    AssetValuation,
    CapitalEvent,
    Expense,
    FXRate,
    GoalContribution,
    Holding,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerReconciliationReport,
    Loan,
    LoanRepayment,
    PhysicalAsset,
    SavingsGoal,
    Transfer,
    UserProfile,
    Valuation,
)

D = Decimal


def today():
    return timezone.localdate()


def ago(days):
    return today() - datetime.timedelta(days=days)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='nwa', email='nwa@example.com', password='pw')
        profile = self.user.profile      # this very object: logging in saves it back
        profile.tier, profile.currency, profile.has_seen_tutorial = 'PRO', '₹', True
        profile.save()
        Account.objects.filter(user=self.user).delete()
        self.client.force_login(self.user)

    def account(self, name='Bank', kind='SAVINGS_ACCOUNT', balance='10000', currency='₹', **kw):
        account = Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance),
                                         currency=currency, **kw)
        # In production the account's creation posts its opening balance (the signal skips it under tests).
        LedgerPostingService.post_opening_balance(account=account)
        return account

    def nw(self, as_of=None):
        return LedgerReadService.get_net_worth(self.user, as_of=as_of)[0]

    def expense(self, amount, when, account, **kw):
        return Expense.objects.create(user=self.user, account=account, amount=D(str(amount)), date=when,
                                      category='Food', currency=kw.pop('currency', '₹'), **kw)

    def income(self, amount, when, account):
        return Income.objects.create(user=self.user, account=account, amount=D(str(amount)), date=when,
                                     source='Salary', currency='₹')

    def fx(self, rate, frm='USD', to='INR', days_ago=0):
        FXRate.objects.update_or_create(from_currency=frm, to_currency=to, as_of_date=ago(days_ago),
                                        defaults={'rate': D(str(rate)), 'source': 't'})
        cache.clear()


class NetWorthOnAPastDateTests(Base):
    def test_spending_after_the_date_is_added_back(self):
        bank = self.account()
        self.expense(500, ago(5), bank)
        self.assertEqual(self.nw(), D('9500.00'))
        self.assertEqual(self.nw(ago(10)), D('10000.00'))
        self.assertEqual(self.nw(ago(5)), D('9500.00'))   # the end of that day includes it

    def test_income_after_the_date_is_taken_back_out(self):
        bank = self.account()
        self.income(3000, ago(3), bank)
        self.assertEqual(self.nw(ago(4)), D('10000.00'))
        self.assertEqual(self.nw(), D('13000.00'))

    def test_today_and_later_dates_mean_now(self):
        bank = self.account()
        self.expense(500, ago(1), bank)
        self.assertEqual(self.nw(today()), self.nw())
        self.assertEqual(self.nw(today() + datetime.timedelta(days=30)), self.nw())

    def test_a_transfer_between_my_accounts_never_changes_net_worth_but_moves_each_balance(self):
        a, b = self.account('A', balance='1000'), self.account('B', balance='0')
        Transfer.objects.create(user=self.user, from_account=a, to_account=b, amount=D('400'), date=ago(2))
        before_total, before = LedgerReadService.get_net_worth(self.user, as_of=ago(5))
        now_total, now = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(before_total, now_total)
        self.assertEqual((before[a.pk], before[b.pk]), (D('1000.00'), D('0.00')))
        self.assertEqual((now[a.pk], now[b.pk]), (D('600.00'), D('400.00')))

    def test_a_goal_contribution_never_changes_net_worth(self):
        bank = self.account()
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('5000'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('700'), date=ago(2))
        self.assertEqual(self.nw(), D('10000.00'))
        self.assertEqual(self.nw(ago(5)), D('10000.00'))

    def test_a_loan_repayment_only_costs_the_interest(self):
        bank = self.account()
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'),
                                   duration_months=12, start_date=ago(100), is_active=True)
        LoanRepayment.objects.create(loan=loan, from_account=bank, date=ago(2), amount=D('1100'),
                                     principal_portion=D('1000'), interest_portion=D('100'))
        self.assertEqual(self.nw(), D('10000') - D('1100') - D('4000'))            # 4,900
        self.assertEqual(self.nw(ago(5)), D('10000') - D('5000'))                  # 5,000: interest not yet paid
        self.assertEqual(self.nw(ago(5)) - self.nw(), D('100.00'))

    def test_a_loan_that_had_not_started_owed_nothing(self):
        self.account()
        Loan.objects.create(user=self.user, name='Later', loan_type='HOME', initial_principal=D('5000'),
                            duration_months=12, start_date=ago(2), is_active=True)
        self.assertEqual(self.nw(ago(10)), D('10000.00'))
        self.assertEqual(self.nw(), D('5000.00'))

    def test_a_loan_that_has_since_closed_still_counts_on_the_dates_it_was_open(self):
        self.account()
        loan = Loan.objects.create(user=self.user, name='Done', loan_type='HOME', initial_principal=D('2000'),
                                   duration_months=12, start_date=ago(100), is_active=True)
        bank = Account.objects.get(user=self.user)
        LoanRepayment.objects.create(loan=loan, from_account=bank, date=ago(2), amount=D('2000'),
                                     principal_portion=D('2000'), interest_portion=D('0'))
        Loan.objects.filter(pk=loan.pk).update(is_active=False)
        self.assertEqual(self.nw(ago(10)), D('10000') - D('2000'))
        self.assertEqual(self.nw(), D('8000.00'))

    def test_a_deposit_is_worth_less_the_further_back_you_look(self):
        fd = self.account('FD', 'FD', '100000', deposit_principal=D('100000'), deposit_rate=D('10'),
                          deposit_start_date=ago(400), deposit_compounding='SIMPLE')
        now, then = self.nw(), self.nw(ago(200))
        self.assertGreater(now, then)
        self.assertEqual(then, (D('100000') * (1 + D('0.10') * D(200) / D(365))).quantize(D('0.01')))

    def test_holdings_are_valued_from_the_latest_valuation_on_or_before_the_date(self):
        acc = self.account('Demat', 'DEMAT', '0')
        h = Holding.objects.create(account=acc, instrument_name='F', instrument_type='MF', currency='₹',
                                   units=D('10'), avg_cost=D('600'), is_active=True)
        Valuation.objects.create(holding=h, value=D('7000'), as_of_date=ago(40))
        Valuation.objects.create(holding=h, value=D('8000'), as_of_date=ago(2))
        self.assertEqual(self.nw(), D('8000.00'))
        self.assertEqual(self.nw(ago(10)), D('7000.00'))

    def test_a_property_is_valued_from_the_latest_appraisal_on_or_before_the_date(self):
        asset = PhysicalAsset.objects.create(user=self.user, name='Flat', asset_class='REAL_ESTATE',
                                             acquisition_cost=D('100'), currency='₹', is_active=True)
        self.account('Flat', 'REAL_ESTATE', '0', linked_physical_asset=asset)
        AssetValuation.objects.create(asset=asset, value=D('4000000'), as_of_date=ago(60))
        AssetValuation.objects.create(asset=asset, value=D('4500000'), as_of_date=ago(1))
        self.assertEqual(self.nw(), D('4500000.00'))
        self.assertEqual(self.nw(ago(30)), D('4000000.00'))

    def test_a_dollar_account_is_rolled_back_in_dollars_and_converted_at_that_days_rate(self):
        self.fx(80, days_ago=30)
        self.fx(90, days_ago=0)
        usd = self.account('USD', balance='1000', currency='$')
        self.expense(100, ago(2), usd, currency='$')
        self.assertEqual(self.nw(), D('900') * D('90'))
        # at ago(10) the stored rate in force was the one from 30 days ago
        self.assertEqual(self.nw(ago(10)), D('1000') * D('80'))

    def test_a_past_net_worth_never_writes_anything(self):
        bank = self.account()
        self.expense(5, ago(5), bank)
        entries = JournalEntry.objects.count()
        balance = Account.objects.get(pk=bank.pk).balance
        self.nw(ago(20))
        self.assertEqual((JournalEntry.objects.count(), Account.objects.get(pk=bank.pk).balance), (entries, balance))

    def test_the_query_count_does_not_grow_with_the_number_of_transactions(self):
        bank = self.account()
        for i in range(3):
            self.expense(1, ago(2), bank)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as few:
            self.nw(ago(10))
        for i in range(40):
            self.expense(1, ago(2), bank)
            self.income(1, ago(2), bank)
        with CaptureQueriesContext(connection) as many:
            self.nw(ago(10))
        self.assertEqual(len(few), len(many))


class MonthlyReportNetWorthTests(Base):
    def test_net_worth_growth_is_what_the_month_actually_did(self):
        bank = self.account(balance='10000')
        first = today().replace(day=1)
        last_month_end = first - datetime.timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        self.income(5000, last_month_start + datetime.timedelta(days=2), bank)
        self.expense(1000, last_month_start + datetime.timedelta(days=5), bank)
        self.expense(300, first, bank)                     # this month: must not be in last month's growth
        data = MonthlyReport().get_report_data(self.user, last_month_start, last_month_end)
        self.assertEqual(data['nw_at_end'], D('14000.00'))
        self.assertEqual(data['nw_change'], D('4000.00'))
        self.assertEqual(data['nw_change_pct'], D('40.0'))

    def test_investments_and_card_dues_are_the_month_end_figures(self):
        bank = self.account(balance='10000')
        card = self.account('Card', 'CREDIT_CARD', '0')
        first = today().replace(day=1)
        end = first - datetime.timedelta(days=1)
        start = end.replace(day=1)
        self.expense(800, start + datetime.timedelta(days=3), card)
        self.expense(200, start + datetime.timedelta(days=4), bank)
        self.expense(500, first, card)                    # owed later: not part of last month's card dues
        data = MonthlyReport().get_report_data(self.user, start, end)
        self.assertEqual(data['credit_card_pending'], D('800.00'))


class MonthOnMonthPageTests(Base):
    def series(self):
        r = self.client.get(reverse('analytics-mom'))
        self.assertEqual(r.status_code, 200)
        return r, json.loads(r.context['nw_data'])

    def test_each_months_net_worth_follows_the_ledger_not_a_cashflow_guess(self):
        bank = self.account(balance='10000')
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'),
                                   duration_months=12, start_date=ago(200), is_active=True)
        first = today().replace(day=1)
        prev_mid = (first - datetime.timedelta(days=1)).replace(day=15)
        # Repaying principal moves money from cash to debt: net worth must not move by the principal
        LoanRepayment.objects.create(loan=loan, from_account=bank, date=prev_mid, amount=D('1000'),
                                     principal_portion=D('1000'), interest_portion=D('0'))
        _r, nw = self.series()
        self.assertEqual(len(nw), 12)
        self.assertEqual(nw[-1], nw[-2])           # unchanged by the principal repayment
        self.assertEqual(nw[-1], 10000 - 1000 - 4000)

    def test_the_series_ends_with_the_current_net_worth(self):
        bank = self.account(balance='10000')
        self.expense(250, today(), bank)
        _r, nw = self.series()
        self.assertEqual(nw[-1], float(self.nw()))

    def test_a_free_account_sees_two_months(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        self.account()
        _r, nw = self.series()
        self.assertEqual(len(nw), 2)

    def test_a_new_user_with_nothing_sees_an_empty_page_not_invented_numbers(self):
        r, _nw = self.series()
        alloc = r.context['asset_allocation']
        self.assertFalse(alloc['has_data'])
        self.assertEqual((alloc['bank_pct'], alloc['inv_pct']), (0, 0))
        self.assertNotContains(r, '88.4')
        self.assertNotContains(r, '13.8')

    def test_allocation_is_worked_out_from_what_you_hold(self):
        self.account('Bank', 'SAVINGS_ACCOUNT', '80000')
        self.account('MF', 'MUTUAL_FUND', '20000')
        r, _ = self.series()
        alloc = r.context['asset_allocation']
        self.assertTrue(alloc['has_data'])
        self.assertEqual((alloc['bank_pct'], alloc['inv_pct']), (80.0, 20.0))

    def test_cards_loans_and_overdrafts_are_not_part_of_what_you_hold(self):
        self.account('Bank', 'SAVINGS_ACCOUNT', '50000')
        self.account('Card', 'CREDIT_CARD', '-30000')
        self.account('Overdrawn', 'SAVINGS_ACCOUNT', '-9000')
        r, _ = self.series()
        alloc = r.context['asset_allocation']
        self.assertEqual((alloc['bank_pct'], alloc['inv_pct']), (100.0, 0.0))

    def test_a_dollar_account_counts_at_its_value_in_your_currency(self):
        self.fx(80)
        self.account('Bank', 'SAVINGS_ACCOUNT', '80000')
        self.account('US', 'MUTUAL_FUND', '250', currency='$')     # 250 * 80 = 20,000
        r, _ = self.series()
        alloc = r.context['asset_allocation']
        self.assertEqual((alloc['bank_pct'], alloc['inv_pct']), (80.0, 20.0))

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('analytics-mom')).status_code, 302)


class YearInReviewGoalsTests(Base):
    def test_a_goal_counts_in_the_year_it_was_finished_not_the_year_it_was_created(self):
        from expenses.utils import generate_year_in_review_data
        bank = self.account()
        year = today().year
        goal = SavingsGoal.objects.create(user=self.user, name='Long', target_amount=D('100'), currency='₹')
        SavingsGoal.objects.filter(pk=goal.pk).update(created_at=timezone.now().replace(year=year - 1))
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('100'), date=today())
        goal.refresh_from_db()
        self.assertTrue(goal.is_completed)
        self.expense(10, today(), bank)
        self.assertEqual(generate_year_in_review_data(self.user, year)['goals_completed'], 1)
        self.expense(10, datetime.date(year - 1, 6, 1), bank)
        self.assertEqual(generate_year_in_review_data(self.user, year - 1)['goals_completed'], 0)

    def test_an_unfinished_goal_does_not_count(self):
        from expenses.utils import generate_year_in_review_data
        bank = self.account()
        goal = SavingsGoal.objects.create(user=self.user, name='Half', target_amount=D('100'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('50'), date=today())
        self.expense(10, today(), bank)
        self.assertEqual(generate_year_in_review_data(self.user, today().year)['goals_completed'], 0)


class LedgerPostingTests(Base):
    def balanced(self, entry):
        debit = sum((l.base_amount for l in entry.lines.all() if l.direction == 'DEBIT'), D('0'))
        credit = sum((l.base_amount for l in entry.lines.all() if l.direction == 'CREDIT'), D('0'))
        return debit.quantize(D('0.01')) == credit.quantize(D('0.01'))

    def test_a_transaction_in_a_third_currency_posts_and_balances(self):
        for frm, to, rate in (('EUR', 'INR', '90'), ('EUR', 'USD', '1.1'), ('USD', 'INR', '80'), ('INR', 'USD', '0.0125')):
            self.fx(rate, frm, to)
        usd = self.account('USD', balance='1000', currency='$')
        self.expense(100, today(), usd, currency='€')
        entry = JournalEntry.objects.get(source_type='EXPENSE')
        self.assertTrue(self.balanced(entry))
        self.assertEqual(Account.objects.get(pk=usd.pk).balance, D('890.00'))

    def test_an_amount_in_your_own_currency_on_a_foreign_account_survives_rounding(self):
        self.fx(80)
        usd = self.account('USD', balance='1000', currency='$')
        for amount in ('99.99', '33.33', '0.01', '1234.57'):
            self.expense(amount, today(), usd)            # rupees on a dollar account
        for entry in JournalEntry.objects.filter(source_type='EXPENSE'):
            self.assertTrue(self.balanced(entry))

    def test_every_entry_the_app_posts_is_balanced(self):
        self.fx(80)
        bank, usd = self.account(), self.account('USD', balance='500', currency='$')
        self.income(900, today(), bank)
        self.expense(40, today(), usd, currency='$')
        Transfer.objects.create(user=self.user, from_account=usd, to_account=bank, amount=D('10'), date=today())
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('99'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=bank, amount=D('50'), date=today())
        CapitalEvent.objects.create(user=self.user, account=bank, date=today(), amount=D('70'), include_in_net_worth=True)
        for entry in JournalEntry.objects.all():
            self.assertTrue(self.balanced(entry), entry.description)

    def test_posting_the_same_thing_twice_changes_nothing(self):
        bank = self.account()
        e = self.expense(10, today(), bank)
        entries = JournalEntry.objects.count()
        key = JournalEntry.objects.get(source_type='EXPENSE').idempotency_key
        LedgerPostingService.post_expense(expense=e, idempotency_key=key)
        self.assertEqual(JournalEntry.objects.count(), entries)

    def test_the_cached_balances_always_equal_what_the_lines_add_up_to(self):
        self.fx(80)
        bank, usd = self.account(), self.account('USD', balance='500', currency='$')
        e = self.expense(40, today(), bank)
        f = self.expense(5, today(), usd, currency='$')
        e.amount = D('55')
        e.save()
        e.description = 'renamed'
        e.save()
        Transfer.objects.create(user=self.user, from_account=bank, to_account=usd, amount=D('100'), date=today())
        f.delete()
        for ledger in LedgerAccount.objects.filter(user=self.user, cached_balance__isnull=False):
            lines = JournalLine.objects.filter(ledger_account=ledger, journal_entry__status='POSTED')
            debit = lines.filter(direction='DEBIT').aggregate(t=Sum('amount'))['t'] or D('0')
            credit = lines.filter(direction='CREDIT').aggregate(t=Sum('amount'))['t'] or D('0')
            self.assertEqual(ledger.cached_balance, debit - credit, ledger.code)

    def test_deleting_a_transaction_leaves_the_ledger_balance_where_it_started(self):
        bank = self.account()
        before = LedgerReadService.get_account_balance(Account.objects.get(pk=bank.pk))
        e = self.expense(123.45, today(), bank)
        e.delete()
        self.assertEqual(LedgerReadService.get_account_balance(Account.objects.get(pk=bank.pk)), before)

    def test_the_account_field_and_the_ledger_agree_after_a_mixed_history(self):
        self.fx(80)
        bank, usd = self.account(), self.account('USD', balance='500', currency='$')
        e = self.expense(40, today(), bank)
        e.amount = D('60')
        e.save()
        self.expense(7, today(), usd, currency='$')
        self.income(25, today(), bank)
        out = StringIO()
        call_command('reconcile_ledgers', stdout=out)
        reports = LedgerReconciliationReport.objects.filter(user=self.user)
        self.assertTrue(all(r.status == 'MATCH' for r in reports), [(r.account.name, r.drift_amount) for r in reports])


class ReconcileCommandTests(Base):
    def test_a_balance_that_no_longer_matches_the_ledger_is_reported_as_drift(self):
        bank = self.account()
        self.expense(10, today(), bank)
        Account.objects.filter(pk=bank.pk).update(balance=D('1.00'))
        call_command('reconcile_ledgers', stdout=StringIO())
        report = LedgerReconciliationReport.objects.get(account=bank)
        self.assertEqual(report.status, 'DRIFT')
        self.assertEqual(report.drift_amount, D('1.00') - D('9990.00'))

    def test_the_gate_flags_fail_the_run_when_asked(self):
        from django.core.management.base import CommandError
        bank = self.account()
        Account.objects.filter(pk=bank.pk).update(balance=D('5.00'))
        with self.assertRaises(CommandError):
            call_command('reconcile_ledgers', '--fail-on-drift', stdout=StringIO())

    def test_running_it_twice_in_a_day_keeps_one_report_per_account(self):
        self.account()
        call_command('reconcile_ledgers', stdout=StringIO())
        call_command('reconcile_ledgers', stdout=StringIO())
        self.assertEqual(LedgerReconciliationReport.objects.filter(user=self.user).count(), 1)

    def test_one_user_can_be_reconciled_alone(self):
        self.account()
        other = User.objects.create_user(username='o', password='pw')
        Account.objects.create(user=other, name='X', balance=D('3'), currency='₹')
        call_command('reconcile_ledgers', '--user-id', str(self.user.id), stdout=StringIO())
        self.assertEqual(set(LedgerReconciliationReport.objects.values_list('user_id', flat=True)), {self.user.id})
