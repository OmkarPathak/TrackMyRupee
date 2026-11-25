"""Bugs the loan browser tests found on the Loans pages. Each test names what went wrong for the person."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import LoanRepaymentForm
from expenses.models import Account, Loan, LoanInterestRate, LoanRepayment, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('bugfinder', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('500000'), currency='₹')
        self.today = timezone.localdate()

    def loan(self, principal='1200000', months=120, days_ago=100, name='Home Loan'):
        start = self.today - timedelta(days=days_ago)
        loan = Loan.objects.create(user=self.user, name=name, loan_type='HOME', initial_principal=D(principal), duration_months=months,
                                   start_date=start, currency='₹')
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('9'), effective_date=start)
        return loan

    def pay(self, loan, principal, interest='0', days_ago=1):
        return LoanRepayment.objects.create(loan=loan, from_account=self.bank, amount=D(principal) + D(interest),
                                            principal_portion=D(principal), interest_portion=D(interest),
                                            date=self.today - timedelta(days=days_ago))

    def detail(self, loan):
        return self.client.get(reverse('loan-detail', kwargs={'pk': loan.uuid}))


class RepaymentWindowTests(Base):
    def test_the_amount_field_calls_no_function_that_does_not_exist(self):
        """It called recalculatePortions() from an inline onchange, a function that lives inside a closure: every edit
        of the amount threw a script error. The page already recalculates on input."""
        form = LoanRepaymentForm(user=self.user, loan=self.loan())
        self.assertNotIn('onchange', str(form['amount']))
        self.assertNotIn('recalculatePortions', str(form['amount']))


class BalanceTrendTests(Base):
    def test_the_trend_text_groups_digits_the_way_the_rest_of_the_page_does(self):
        """A rupee loan of 12 lakh showed ₹1,192,000 in the trend and ₹11,92,000 everywhere else."""
        loan = self.loan()
        self.pay(loan, 4000, 100, days_ago=70)
        self.pay(loan, 4000, 100, days_ago=40)
        text = self.detail(loan).context['trend_summary_text']
        self.assertIn('11,', text)
        self.assertNotIn('1,19', text)

    def test_a_loan_with_no_repayments_has_no_trend_yet(self):
        """It showed the next six months of the plan, worded as "over the last 6 months"."""
        loan = self.loan(days_ago=2)
        context = self.detail(loan).context
        self.assertEqual(context['trend_summary_text'], '')
        self.assertEqual(context['trend_chart_data']['labels'], [])
        self.assertContains(self.detail(loan), 'No Balance Trend Yet')


class RateInForceTests(Base):
    """A rate that starts next month was shown as "the" rate and marked Current, while the schedule (correctly) kept
    using today's rate."""

    def setUp(self):
        super().setUp()
        self.l = self.loan()
        self.later = LoanInterestRate.objects.create(loan=self.l, interest_rate=D('11'), effective_date=self.today + timedelta(days=45))

    def test_the_rate_card_shows_the_rate_in_force_today(self):
        self.assertEqual(self.detail(self.l).context['current_rate'], D('9.00'))

    def test_the_history_marks_the_rate_in_force_as_current_and_the_later_one_as_upcoming(self):
        rows = self.detail(self.l).context['rate_rows']
        self.assertEqual([(str(r['rate']), r['status']) for r in rows], [('11.00', 'upcoming'), ('9.00', 'current')])

    def test_once_the_later_rate_has_started_it_is_the_current_one(self):
        self.later.effective_date = self.today - timedelta(days=1)
        self.later.save()
        context = self.detail(self.l).context
        self.assertEqual(context['current_rate'], D('11.00'))
        self.assertEqual([r['status'] for r in context['rate_rows']], ['current', 'past'])

    def test_a_loan_whose_every_rate_is_in_the_future_uses_the_earliest(self):
        loan = self.loan(days_ago=-10, name='Not started')
        LoanInterestRate.objects.filter(loan=loan).update(effective_date=self.today + timedelta(days=10))
        context = self.detail(loan).context
        self.assertEqual(context['current_rate'], D('9.00'))
        self.assertEqual([r['status'] for r in context['rate_rows']], ['current'])


class PaidOffLoanScheduleTests(Base):
    """A loan paid off kept an active EMI subscription (listed as renewing soon, on the calendar) until its next due date."""

    def schedule(self, loan, start_days_ago=5, amount='20000'):
        from expenses.models import RecurringTransaction
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D(amount), base_amount=D(amount), currency='₹', account=self.bank, loan=loan,
            frequency='MONTHLY', start_date=self.today - timedelta(days=start_days_ago), description='Loan EMI')

    def test_paying_a_loan_off_by_hand_stops_its_schedule_at_once(self):
        loan = self.loan('20000', 12, 100)
        schedule = self.schedule(loan, start_days_ago=-20)
        self.pay(loan, '20000', '0')
        schedule.refresh_from_db()
        self.assertFalse(Loan.objects.get(pk=loan.pk).is_active)
        self.assertFalse(schedule.is_active)

    def test_the_instalment_that_pays_a_loan_off_stops_its_schedule(self):
        from expenses.views.mixins import process_user_recurring_transactions
        loan = self.loan('15000', 12, 100)
        schedule = self.schedule(loan)
        process_user_recurring_transactions(self.user, force=True)
        schedule.refresh_from_db()
        self.assertEqual(LoanRepayment.objects.filter(loan=loan).count(), 1)
        self.assertFalse(schedule.is_active)

    def test_a_loan_still_owing_keeps_its_schedule(self):
        loan = self.loan('1200000', 120, 100)
        schedule = self.schedule(loan, start_days_ago=-20)
        self.pay(loan, '8000', '9000')
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)


class AccountsPageNetWorthTests(Base):
    """The Dashboard counted a loan with no loan account against net worth (5 lakh in the bank, 12 lakh loan: -7 lakh); the
    Accounts page, whose own text says "minus what you owe on loans", showed +5 lakh for the same person."""

    def net_worth_on_dashboard(self):
        from expenses.ledger_read_service import LedgerReadService
        return LedgerReadService.get_net_worth(self.user)[0]

    def accounts_page(self):
        return self.client.get(reverse('account-list')).context

    def test_a_loan_with_no_loan_account_is_taken_off_the_accounts_headline(self):
        self.loan()
        context = self.accounts_page()
        self.assertEqual(context['overall_total'], D('-700000.00'))
        self.assertEqual(context['overall_total'], self.net_worth_on_dashboard())

    def test_the_owed_part_of_the_headline_sentence_includes_it(self):
        self.loan()
        context = self.accounts_page()
        self.assertEqual(context['assets_total'], D('500000.00'))
        self.assertEqual(context['owed_total'], D('1200000.00'))
        self.assertIn('loans', str(context['owed_label']))

    def test_a_loan_that_has_its_own_account_is_counted_once(self):
        loan = self.loan()
        Account.objects.create(user=self.user, name='Home loan account', account_type='HOME_LOAN', balance=D('0'), currency='₹', linked_loan=loan)
        context = self.accounts_page()
        self.assertEqual(context['overall_total'], D('-700000.00'))
        self.assertEqual(context['overall_total'], self.net_worth_on_dashboard())

    def test_a_loan_that_is_paid_off_takes_nothing_off(self):
        loan = self.loan('20000', 12, 100)
        self.pay(loan, '20000', '0')
        self.assertEqual(self.accounts_page()['overall_total'], D('480000.00'))

    def test_the_total_follows_repayments(self):
        loan = self.loan()
        self.pay(loan, '100000', '9000')
        self.assertEqual(self.accounts_page()['overall_total'], D('-600000.00') - D('109000'))


class AmountGroupingTests(Base):
    """"Principal Paid" read ₹150000 while every other figure on the page was grouped the Indian way (₹1,50,000)."""

    def test_principal_paid_and_the_grand_total_are_grouped_like_the_other_figures(self):
        from expenses.models import CapitalEvent
        loan = self.loan('5000000', 240)
        self.pay(loan, '150000', '45000', days_ago=40)
        CapitalEvent.objects.create(user=self.user, amount=D('200000'), date=self.today - timedelta(days=30), subtype='loan_down_payment',
                                    linked_loan=loan, account=self.bank, currency='₹')
        html = self.detail(loan).content.decode()
        self.assertIn('₹3,50,000', html)             # principal paid: 1,50,000 repaid and 2,00,000 down payment
        self.assertNotIn('₹350000', html)
        self.assertNotRegex(html, r'₹\d{6,}</h4>')
        self.assertNotRegex(html, r'Grand Total Paid to Bank</span>\s*<span class="fw-bold">₹\d{6,}')
