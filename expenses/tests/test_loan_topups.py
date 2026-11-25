"""A top-up adds to a loan's principal from its date, and every figure that depends on the principal follows: what is
still owed, the schedule, the repayments an automatic EMI posts, the Loans pages, net worth on a past day, insights."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account, CapitalEvent, Loan, LoanDisbursement, LoanInterestRate, LoanRepayment, LoanTopUp, RecurringTransaction, UserProfile,
    annotate_loan_principal_totals,
)
from expenses.services import LoanService
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('topper', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('1000000'), currency='₹')
        self.today = timezone.localdate()
        self.start = self.today - timedelta(days=200)
        self.loan = Loan.objects.create(user=self.user, name='Home Loan', loan_type='HOME', initial_principal=D('1000000'),
                                        duration_months=120, start_date=self.start, currency='₹')
        LoanInterestRate.objects.create(loan=self.loan, interest_rate=D('12'), effective_date=self.start)

    def topup(self, amount='200000', when=None, months=0, **extra):
        return LoanTopUp.objects.create(loan=self.loan, date=when or self.today - timedelta(days=30), amount=D(amount), extra_months=months, **extra)

    def fresh(self):
        return Loan.objects.get(pk=self.loan.pk)


class PrincipalTests(Base):
    def test_a_top_up_adds_to_what_is_owed(self):
        self.topup('200000')
        self.assertEqual(self.fresh().remaining_principal, D('1200000'))
        self.assertEqual(self.fresh().total_principal, D('1200000'))

    def test_the_original_principal_is_not_touched(self):
        self.topup('200000')
        self.assertEqual(self.fresh().initial_principal, D('1000000'))

    def test_repayments_and_prepayments_still_reduce_it(self):
        self.topup('200000')
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('20000'), principal_portion=D('10000'),
                                     interest_portion=D('10000'), date=self.today - timedelta(days=10))
        CapitalEvent.objects.create(user=self.user, amount=D('50000'), date=self.today - timedelta(days=5), subtype='loan_prepayment',
                                    linked_loan=self.loan, account=self.bank, currency='₹')
        self.assertEqual(self.fresh().remaining_principal, D('1140000'))

    def test_several_top_ups_add_up(self):
        self.topup('100000')
        self.topup('50000', when=self.today - timedelta(days=10))
        self.assertEqual(self.fresh().total_principal, D('1150000'))

    def test_the_term_grows_by_the_months_a_top_up_adds(self):
        self.assertEqual(self.fresh().total_duration_months, 120)
        self.topup('100000', months=24)
        self.assertEqual(self.fresh().total_duration_months, 144)

    def test_the_bulk_totals_for_lists_agree_with_the_loan(self):
        self.topup('200000', months=12)
        annotated = annotate_loan_principal_totals(Loan.objects.filter(pk=self.loan.pk)).get()
        self.assertEqual(annotated.remaining_principal, D('1200000'))
        self.assertEqual(annotated.total_duration_months, 132)

    def test_the_bulk_totals_as_of_a_day_leave_out_later_top_ups(self):
        self.topup('200000', when=self.today - timedelta(days=30))
        before = annotate_loan_principal_totals(Loan.objects.filter(pk=self.loan.pk), as_of=self.today - timedelta(days=60)).get()
        after = annotate_loan_principal_totals(Loan.objects.filter(pk=self.loan.pk), as_of=self.today).get()
        self.assertEqual((before.remaining_principal, after.remaining_principal), (D('1000000'), D('1200000')))

    def test_the_summary_reports_the_top_ups(self):
        self.topup('200000')
        summary = LoanService.get_loan_summary(self.fresh())
        self.assertEqual((summary['topped_up'], summary['remaining_principal']), (200000.0, 1200000.0))


class RulesTests(Base):
    def test_the_amount_must_be_more_than_zero(self):
        with self.assertRaises(ValidationError):
            self.topup('0')

    def test_it_cannot_be_dated_before_the_loan_started(self):
        with self.assertRaises(ValidationError):
            self.topup(when=self.start - timedelta(days=1))

    def test_a_repayment_cannot_repay_more_principal_than_is_owed_including_top_ups(self):
        self.loan.opening_paid_principal = D('0')
        self.topup('500000', when=self.today - timedelta(days=30))
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1400000'), principal_portion=D('1400000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=1))
        with self.assertRaises(ValidationError):
            LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('200000'), principal_portion=D('200000'),
                                         interest_portion=D('0'), date=self.today)


class ScheduleTests(Base):
    def test_the_schedule_is_worked_out_from_the_larger_balance(self):
        before = LoanService.generate_amortization_schedule(self.fresh())
        self.topup('1000000')
        after = LoanService.generate_amortization_schedule(self.fresh())
        self.assertGreater(after[0]['emi'], before[0]['emi'] * 1.9)
        self.assertAlmostEqual(after[0]['balance'] + after[0]['principal'], 2 * (before[0]['balance'] + before[0]['principal']), delta=2)

    def test_added_months_lengthen_the_schedule_and_lower_the_emi(self):
        self.topup('200000')
        plain = LoanService.generate_amortization_schedule(self.fresh())
        LoanTopUp.objects.all().delete()
        self.topup('200000', months=36)
        longer = LoanService.generate_amortization_schedule(self.fresh())
        self.assertEqual(len(longer) - len(plain), 36)
        self.assertLess(longer[0]['emi'], plain[0]['emi'])


class StatusTests(Base):
    def test_a_top_up_reopens_a_paid_off_loan(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1000000'), principal_portion=D('1000000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=1))
        self.assertFalse(self.fresh().is_active)
        self.topup('300000', when=self.today)
        self.assertTrue(self.fresh().is_active)

    def test_deleting_the_top_up_closes_it_again(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('1000000'), principal_portion=D('1000000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=1))
        top = self.topup('300000', when=self.today)
        top.delete()
        self.assertFalse(self.fresh().is_active)


class MoneyReceivedTests(Base):
    def test_deleting_a_top_up_takes_the_money_it_put_in_an_account_out_again(self):
        received = LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D('200000'), date=self.today)
        top = self.topup('200000', when=self.today, disbursement=received)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('1200000'))
        top.delete()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('1000000'))
        self.assertFalse(LoanDisbursement.objects.exists())


class AutomaticEmiTests(Base):
    def emi_schedule(self, start):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D('20000'), base_amount=D('20000'), currency='₹', account=self.bank,
            loan=self.loan, frequency='MONTHLY', start_date=start, description='Loan EMI: Home Loan')

    def interest_of(self, month_date):
        return LoanRepayment.objects.get(loan=self.loan, date=month_date).interest_portion

    def test_an_emi_is_split_using_the_balance_on_its_own_day(self):
        first = self.today - timedelta(days=65)
        self.emi_schedule(first)
        self.topup('1000000', when=self.today - timedelta(days=30))     # (after the first instalment, before a later one)
        process_user_recurring_transactions(self.user, force=True)
        rows = list(LoanRepayment.objects.filter(loan=self.loan).order_by('date'))
        self.assertGreaterEqual(len(rows), 2)
        # 12% a year on the balance at the time: 1,000,000 before the top-up, then more than that after it
        self.assertEqual(rows[0].interest_portion, D('10000.00'))
        self.assertGreater(rows[-1].interest_portion, D('15000.00'))

    def test_a_prepayment_dated_later_does_not_lower_an_earlier_instalment(self):
        first = self.today - timedelta(days=65)
        self.emi_schedule(first)
        CapitalEvent.objects.create(user=self.user, amount=D('500000'), date=self.today - timedelta(days=5), subtype='loan_prepayment',
                                    linked_loan=self.loan, account=self.bank, currency='₹')
        process_user_recurring_transactions(self.user, force=True)
        earliest = LoanRepayment.objects.filter(loan=self.loan).order_by('date').first()
        self.assertEqual(earliest.interest_portion, D('10000.00'))


class PagesTests(Base):
    def test_the_loans_list_measures_progress_against_everything_borrowed(self):
        self.topup('1000000')
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('200000'), principal_portion=D('200000'),
                                     interest_portion=D('0'), date=self.today - timedelta(days=1))
        context = self.client.get(reverse('loan-list')).context
        summary = context['loan_summaries'][0]
        self.assertEqual(summary['remaining_principal'], 1800000.0)
        self.assertEqual(round(summary['progress'], 1), 10.0)

    def test_the_loan_page_shows_the_larger_balance(self):
        self.topup('200000')
        context = self.client.get(reverse('loan-detail', kwargs={'pk': self.loan.uuid})).context
        self.assertEqual(context['summary']['remaining_principal'], 1200000.0)

    def test_the_balance_chart_rises_in_the_month_of_a_top_up(self):
        LoanRepayment.objects.create(loan=self.loan, from_account=self.bank, amount=D('20000'), principal_portion=D('10000'),
                                     interest_portion=D('10000'), date=self.today - timedelta(days=100))
        self.topup('300000', when=self.today - timedelta(days=40))
        context = self.client.get(reverse('loan-detail', kwargs={'pk': self.loan.uuid})).context
        chart = context['amortization_chart_data']
        history = chart['balance'][:chart['history_count']]
        self.assertEqual(history[0], 990000.0)
        self.assertEqual(history[-1], 1290000.0)


class NetWorthTests(Base):
    def test_net_worth_on_a_day_before_the_top_up_does_not_count_it(self):
        from expenses.ledger_read_service import LedgerReadService
        self.topup('200000', when=self.today - timedelta(days=30))
        earlier = LedgerReadService._fetch_loan_outstanding([self.loan.id], as_of=self.today - timedelta(days=60))
        now = LedgerReadService._fetch_loan_outstanding([self.loan.id])
        self.assertEqual((earlier[self.loan.id], now[self.loan.id]), (D('1000000.00'), D('1200000.00')))


class InsightsTests(Base):
    def test_the_insights_context_sees_the_larger_balance(self):
        from expenses.insights.context import InsightContext
        self.topup('200000')
        ctx = InsightContext(self.user)
        loans = ctx.loans
        self.assertEqual(loans[0]._cached_summary['remaining_principal'], 1200000.0)
        self.assertEqual(loans[0].total_duration_months, 120)
