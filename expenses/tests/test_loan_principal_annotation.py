import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.models import (
    CapitalEvent,
    Loan,
    LoanRepayment,
    annotate_loan_principal_totals,
)
from expenses.services import LoanService


class LoanPrincipalAnnotationTest(TestCase):
    """annotate_loan_principal_totals must give exactly what Loan.remaining_principal computes
    on its own, while costing one query regardless of how many loans there are."""

    def setUp(self):
        self.user = User.objects.create_user(username='loan_annot', password='pw')
        day = datetime.date(2024, 1, 1)
        self.loans = []
        for i, principal in enumerate(['50000.00', '120000.50', '9000.00', '30000.00']):
            loan = Loan.objects.create(
                user=self.user, name=f'Loan {i}', loan_type='HOME',
                initial_principal=Decimal(principal), duration_months=12, start_date=day, currency='₹',
            )
            self.loans.append(loan)
        # loan 0: repayments only; loan 1: repayments + prepayment; loan 2: nothing; loan 3: prepayment only
        for n in range(3):
            LoanRepayment.objects.create(
                loan=self.loans[0], amount=Decimal('4442.44'), principal_portion=Decimal('3942.44'),
                interest_portion=Decimal('500.00'), date=day + datetime.timedelta(days=30 * n),
            )
        LoanRepayment.objects.create(
            loan=self.loans[1], amount=Decimal('10000.00'), principal_portion=Decimal('7000.25'),
            interest_portion=Decimal('2999.75'), date=day,
        )
        for loan, subtype, amount in [
            (self.loans[1], 'loan_prepayment', '15000.10'),
            (self.loans[3], 'loan_down_payment', '5000.00'),
            (self.loans[3], 'loan_prepayment', '1000.00'),
            (self.loans[0], 'other', '777.00'),  # not a prepayment subtype: must be ignored
        ]:
            CapitalEvent.objects.create(
                user=self.user, amount=Decimal(amount), date=day, subtype=subtype, linked_loan=loan,
            )

    def test_annotated_matches_unannotated(self):
        expected = {loan.pk: loan.remaining_principal for loan in Loan.objects.filter(user=self.user)}
        annotated = {
            loan.pk: loan.remaining_principal
            for loan in annotate_loan_principal_totals(Loan.objects.filter(user=self.user))
        }
        self.assertEqual(annotated, expected)
        self.assertEqual(len(annotated), 4)

    def test_loan_without_rows_gets_zero_not_none(self):
        loan = annotate_loan_principal_totals(Loan.objects.filter(pk=self.loans[2].pk)).get()
        self.assertEqual(loan.paid_principal, Decimal('0.00'))
        self.assertEqual(loan.capital_prepaid, Decimal('0.00'))

    def test_total_liabilities_is_one_query_for_any_number_of_loans(self):
        expected = float(sum(loan.remaining_principal for loan in Loan.objects.filter(user=self.user)))
        with self.assertNumQueries(1):
            total = LoanService.get_total_liabilities(self.user)
        self.assertEqual(total, expected)
