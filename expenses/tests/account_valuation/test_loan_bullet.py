from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current
from expenses.models import Account, Loan, LoanInterestRate, LoanRepayment, UserProfile


class LoanBulletTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testloanbullet", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})

        self.loan = Loan.objects.create(
            user=self.user,
            name="Gold Loan",
            loan_type="OTHER",
            repayment_type="BULLET",
            initial_principal=Decimal("200000.00"),
            duration_months=6,
            start_date=date(2023, 1, 1),
            currency="₹",
        )
        LoanInterestRate.objects.create(
            loan=self.loan,
            interest_rate=Decimal("12.00"),
            effective_date=date(2023, 1, 1),
        )

        self.account = Account.objects.create(
            user=self.user,
            name="Gold Loan Account",
            account_type="GOLD_LOAN",
            currency="₹",
            balance=Decimal("-200000.00"),
            linked_loan=self.loan,
        )

    def test_bullet_loan_does_not_amortize_early(self):
        """SPEC §2.4 Edge Case 4: Bullet / interest-only loan principal stays flat across interest repayments."""
        # Log 3 monthly interest-only repayments
        for month in range(1, 4):
            LoanRepayment.objects.create(
                loan=self.loan,
                amount=Decimal("2000.00"),
                principal_portion=Decimal("0.00"),
                interest_portion=Decimal("2000.00"),
                date=date(2023, month, 1),
            )

        # Outstanding principal must remain full initial principal (₹2,00,000.00)
        current = get_current(self.account)
        self.assertEqual(current, Decimal("200000.00"))

    def test_bullet_loan_repayment_at_term_end(self):
        """Bullet loan drops to 0 only when full principal repayment lands."""
        LoanRepayment.objects.create(
            loan=self.loan,
            amount=Decimal("202000.00"),
            principal_portion=Decimal("200000.00"),
            interest_portion=Decimal("2000.00"),
            date=date(2023, 7, 1),
        )
        current = get_current(self.account)
        self.assertEqual(current, Decimal("0.00"))
