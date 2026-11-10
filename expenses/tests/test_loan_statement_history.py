from datetime import date
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.urls import reverse

from expenses.ledger_read_service import LedgerReadService
from expenses.loan_history import build_history, due_date
from expenses.models import JournalEntry, Loan, LoanHistoryEntry, LoanRepayment, RecurringTransaction
from expenses.services import LoanService

from .test_loans_module import LoanBase

D = Decimal
Kind = LoanHistoryEntry.Kind


class TestStatementCalculator(LoanBase):
    def setUp(self):
        super().setUp()
        self.clock = patch('expenses.loan_history.timezone.localdate', return_value=date(2025, 3, 31))
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def statement(self, rate='0', **terms):
        defaults = dict(started=date(2025, 1, 31), months=4, statement_history=True)
        return self.loan(rate=rate, **(defaults | terms))

    def entry(self, loan, kind, day, **fields):
        return LoanHistoryEntry.objects.create(loan=loan, kind=kind, date=day, **fields)

    def test_month_end_today_cutoff_and_as_of_balances(self):
        loan = self.statement()
        self.entry(loan, Kind.DISBURSEMENT, date(2025, 1, 31), amount=D('1000'))
        result = build_history(loan, project=True)
        installments = [row for row in result.rows if row.due_date]
        self.assertEqual([(row.date.day, row.status, row.balance) for row in installments], [
            (28, 'assumed', D('750')), (31, 'assumed', D('500')),
            (30, 'projected', D('250')), (31, 'projected', D('0')),
        ])
        self.assertEqual(loan.remaining_principal, D('500'))
        self.assertEqual(LoanService.get_total_liabilities(self.user), D('500'))
        self.assertEqual(LedgerReadService._loans_outstanding_base(
            self.user, '₹', as_of=date(2025, 2, 27),
        ), D('1000'))
        self.assertEqual(LedgerReadService._fetch_loan_outstanding(
            [loan.id], as_of=date(2025, 2, 28),
        ), {loan.id: D('750')})
        for day, expected in ((date(2025, 2, 27), D('1000')), (None, D('500'))):
            detail = {}
            LedgerReadService.get_net_worth(self.user, as_of=day, detail=detail)
            self.assertEqual(detail['liabilities'], expected)
        self.assertEqual(LoanHistoryEntry.objects.filter(loan=loan).count(), 1)

    def test_daily_interest_splits_at_disbursement_and_rate_change(self):
        loan = self.statement(
            rate='10', started=date(2025, 1, 1), first_emi_date=date(2025, 2, 1),
            interest_method=Loan.InterestMethod.DAILY,
        )
        self.entry(loan, Kind.DISBURSEMENT, date(2025, 1, 1), amount=D('1000'))
        self.entry(loan, Kind.DISBURSEMENT, date(2025, 1, 11), amount=D('500'))
        self.entry(loan, Kind.RATE, date(2025, 1, 21), interest_rate=D('12'))
        first = next(row for row in build_history(loan).rows if row.status == 'assumed')
        # 10 days at 1,000 × 10%, 10 days at 1,500 × 10%, 11 days at 1,500 × 12%.
        self.assertEqual(first.interest, D('12.27'))
        self.assertEqual(first.rate, D('12'))
        self.assertEqual(LoanService.current_rate(loan, date(2025, 1, 20)), D('10'))
        self.assertEqual(LoanService.current_rate(loan, date(2025, 1, 21)), D('12'))

    def test_rate_reset_changes_payment_or_payoff_date(self):
        recalculate = self.statement(months=3)
        keep = self.statement(months=3, rate_reset=Loan.RateReset.KEEP_EMI)
        for loan in (recalculate, keep):
            self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('3000'))
            self.entry(loan, Kind.RATE, date(2025, 3, 1), interest_rate=D('12'))
        recalc_rows = [r for r in build_history(recalculate, project=True).rows if r.due_date]
        keep_rows = [r for r in build_history(keep, project=True).rows if r.due_date]
        self.assertEqual(recalc_rows[0].amount, D('1000'))
        self.assertGreater(recalc_rows[1].amount, D('1000'))
        self.assertEqual(keep_rows[1].amount, D('1000'))
        self.assertEqual(recalc_rows[-1].date, date(2025, 4, 30))
        self.assertEqual(keep_rows[-1].date, date(2025, 5, 31))
        self.assertEqual(keep_rows[-1].balance, D('0'))

    def test_missed_late_partial_and_extra_payments(self):
        loan = self.statement(rate='12')
        self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('1000'))
        self.entry(loan, Kind.EMI, loan.start_date, amount=D('250'))
        first, second = due_date(loan, 1), due_date(loan, 2)
        self.entry(loan, Kind.MISSED, first, installment_date=first)
        self.entry(loan, Kind.PAYMENT, date(2025, 3, 5), amount=D('100'), principal=D('90'), interest=D('10'))
        self.entry(
            loan, Kind.PAYMENT, date(2025, 4, 2), installment_date=second,
            amount=D('150'), principal=D('140.90'), interest=D('9.10'),
        )
        self.assertEqual(build_history(loan).balance, D('910'))
        self.clock.stop()
        with patch('expenses.loan_history.timezone.localdate', return_value=date(2025, 4, 2)):
            result = build_history(loan)
        self.assertEqual([row.status for row in result.rows if row.due_date], [
            'missed', 'pending_recorded', 'partial',
        ])
        self.assertEqual(result.balance, D('769.10'))
        self.assertEqual(result.unpaid_interest, D('0'))

    def test_early_linked_payment_covers_interest_and_replaces_assumption(self):
        loan = self.statement(rate='12')
        self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('1000'))
        self.entry(loan, Kind.EMI, loan.start_date, amount=D('250'))
        self.entry(
            loan, Kind.PAYMENT, date(2025, 2, 26), installment_date=date(2025, 2, 28),
            amount=D('250'), principal=D('240'), interest=D('10'),
        )
        result = build_history(loan, as_of=date(2025, 2, 28))
        self.assertEqual(result.balance, D('760'))
        self.assertEqual(result.unpaid_interest, D('0'))
        self.assertEqual([row.status for row in result.rows if row.due_date], ['recorded'])

    def test_future_rate_and_emi_changes_affect_only_projected_due_dates(self):
        loan = self.statement(rate='12')
        self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('1000'))
        self.entry(loan, Kind.RATE, date(2025, 4, 15), interest_rate=D('6'))
        self.entry(loan, Kind.EMI, date(2025, 4, 15), amount=D('200'))
        result = build_history(loan, project=True)
        due = [row for row in result.rows if row.due_date]
        self.assertEqual([row.rate for row in due], [D('12'), D('12'), D('6'), D('6')])
        self.assertEqual(due[2].amount, D('200'))
        self.assertGreater(result.projected_balance, D('0'))
        self.assertEqual(result.balance, build_history(loan).balance)

    def test_insufficient_emi_does_not_capitalize_interest(self):
        loan = self.statement(rate='12')
        self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('1000'))
        self.entry(loan, Kind.EMI, loan.start_date, amount=D('5'))
        result = build_history(loan)
        self.assertEqual(result.balance, D('1000'))
        self.assertEqual(result.unpaid_interest, D('10'))
        self.assertEqual(result.principal_paid, D('0'))

    def test_early_payoff_has_no_future_installments(self):
        loan = self.statement()
        self.entry(loan, Kind.DISBURSEMENT, loan.start_date, amount=D('1000'))
        self.entry(loan, Kind.PAYMENT, date(2025, 2, 1), amount=D('1000'), principal=D('1000'), interest=D('0'))
        result = build_history(loan, project=True)
        self.assertFalse(result.is_active)
        self.assertFalse(any(row.due_date for row in result.rows))


class TestStatementHistoryViews(LoanBase):
    def setUp(self):
        super().setUp()
        self.loan = self.loan(started=date(2025, 1, 31), statement_history=True, rate='0')
        self.entry_url = reverse('loan-history-entry', args=[self.loan.uuid])
        self.detail_url = reverse('loan-detail', args=[self.loan.uuid])

    def submit(self, kind, day='2025-01-31', **fields):
        data = {'entry_key': uuid4(), 'kind': kind, 'date': day, **fields}
        return self.client.post(self.entry_url, data)

    def test_add_loan_with_statement_options_is_safe_to_retry(self):
        response = self.client.get(reverse('loan-create'))
        key = response.context['form']['statement_key'].value()
        data = {
            'name': 'Synthetic statement', 'loan_type': 'HOME', 'currency': '₹',
            'initial_principal': '2000', 'duration_months': '24',
            'start_date': '2025-02-01', 'interest_rate': '10',
            'statement_history': 'on', 'statement_key': key,
            'first_emi_date': '2025-03-15', 'scheduled_emi': '110',
            'interest_method': Loan.InterestMethod.DAILY, 'rate_reset': Loan.RateReset.KEEP_EMI,
        }
        for _ in range(2):
            response = self.client.post(reverse('loan-create'), data)
            self.assertEqual(response.status_code, 302)
        loan = Loan.objects.get(uuid=key, user=self.user)
        self.assertEqual(loan.first_emi_date, date(2025, 3, 15))
        self.assertEqual(loan.history_entries.get().amount, D('110'))
        self.assertEqual(loan.interest_rates.count(), 1)

    def test_history_writes_do_not_touch_cash_or_create_other_transactions(self):
        starting_balance = self.cash.balance
        journal_count = JournalEntry.objects.count()
        key = uuid4()
        for _ in range(2):
            self.assertEqual(self.submit(Kind.DISBURSEMENT, entry_key=key, amount='12000').status_code, 302)
        self.assertEqual(self.submit(Kind.PAYMENT, '2025-02-28', amount='900',
                                     principal='900', interest='0', installment_date='2025-02-28').status_code, 302)
        response = self.client.get(self.detail_url)
        self.assertContains(response, 'Partial · recorded')
        self.assertContains(response, 'Paid · assumed')
        self.assertEqual(self.loan.history_entries.count(), 2)
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, starting_balance)
        self.assertEqual(JournalEntry.objects.count(), journal_count)
        self.assertFalse(LoanRepayment.objects.exists())
        self.assertFalse(RecurringTransaction.objects.exists())
        self.assertEqual(self.client.post(reverse('loan-repayment-create', args=[self.loan.uuid])).status_code, 404)

    def test_edit_and_delete_validate_entire_history(self):
        self.submit(Kind.DISBURSEMENT, amount='1000')
        self.submit(Kind.PAYMENT, '2025-02-01', amount='1000', principal='1000', interest='0')
        draw = self.loan.history_entries.get(kind=Kind.DISBURSEMENT)
        url = reverse('loan-history-entry-edit', args=[self.loan.uuid, draw.uuid])
        response = self.client.post(url, {'kind': Kind.DISBURSEMENT, 'entry_key': draw.uuid,
                                         'date': '2025-01-31', 'amount': '500'})
        self.assertContains(response, 'more principal than was owed')
        self.client.post(url, {'action': 'delete'})
        draw.refresh_from_db()
        self.assertEqual(draw.amount, D('1000'))

    def test_rejects_duplicate_corrections_and_bad_splits(self):
        self.submit(Kind.DISBURSEMENT, amount='1000')
        self.assertEqual(self.submit(Kind.MISSED, '2025-02-28').status_code, 302)
        response = self.submit(Kind.PAYMENT, '2025-02-28', amount='100',
                               principal='100', interest='0', installment_date='2025-02-28')
        self.assertContains(response, 'existing correction')
        response = self.submit(Kind.PAYMENT, '2025-02-28', amount='100', principal='100', interest='1')
        self.assertContains(response, 'must add up to the payment')

    def test_history_is_scoped_to_owner_and_paid_plans(self):
        other = self.make_user('statement-other')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.detail_url).status_code, 404)
        self.assertEqual(self.submit(Kind.DISBURSEMENT, amount='1000').status_code, 404)
        free = self.make_user('statement-free', tier='FREE')
        self.client.force_login(free)
        self.assertRedirects(self.client.get(self.entry_url), reverse('pricing'))
        self.assertFalse(self.loan.history_entries.exists())
