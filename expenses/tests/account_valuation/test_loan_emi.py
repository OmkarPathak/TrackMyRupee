from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.models import (
    Account,
    CapitalEvent,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    LoanScheduleInstallment,
)


class LoanEMITestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testloanemi", password="password")
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.is_lifetime = True
        profile.save()

        self.loan = Loan.objects.create(
            user=self.user,
            name="Home Loan",
            loan_type="HOME",
            repayment_type="EMI",
            initial_principal=Decimal("3000000.00"),
            duration_months=240,
            start_date=date(2023, 1, 1),
            currency="₹",
        )
        LoanInterestRate.objects.create(
            loan=self.loan,
            interest_rate=Decimal("8.50"),
            effective_date=date(2023, 1, 1),
        )

        self.account = Account.objects.create(
            user=self.user,
            name="Home Loan Account",
            account_type="HOME_LOAN",
            currency="₹",
            balance=Decimal("-3000000.00"),
            linked_loan=self.loan,
        )

    def test_baseline_is_none_for_loan(self):
        """SPEC §2.4: Baseline for LOAN_OUTSTANDING is None (suppresses gain toggle)."""
        self.assertIsNone(get_baseline(self.account))

    def test_display_value_returns_current(self):
        self.assertEqual(get_display_value(self.account), get_current(self.account))

    def test_loan_schedule_installment_valuation(self):
        """When schedule installments are paid and up to date, get_current returns latest paid scheduled_balance."""
        inst1 = LoanScheduleInstallment.objects.create(
            loan=self.loan,
            installment_no=1,
            due_date=date(2023, 2, 1),
            scheduled_principal=Decimal("5000.00"),
            scheduled_interest=Decimal("21250.00"),
            scheduled_balance=Decimal("2995000.00"),
            is_paid=True,
        )

        current = get_current(self.account)
        self.assertEqual(current, Decimal("2995000.00"))

    def test_prepayment_stale_schedule_fallback(self):
        """SPEC §2.4 Edge Case 1: Prepayment posted after schedule triggers fallback formula."""
        LoanScheduleInstallment.objects.create(
            loan=self.loan,
            installment_no=1,
            due_date=date(2023, 2, 1),
            scheduled_principal=Decimal("5000.00"),
            scheduled_interest=Decimal("21250.00"),
            scheduled_balance=Decimal("2995000.00"),
            is_paid=True,
        )

        LoanRepayment.objects.create(
            loan=self.loan,
            amount=Decimal("26250.00"),
            principal_portion=Decimal("5000.00"),
            interest_portion=Decimal("21250.00"),
            date=date(2023, 2, 1),
        )

        # Prepayment posted AFTER latest paid installment
        CapitalEvent.objects.create(
            user=self.user,
            linked_loan=self.loan,
            account=self.account,
            subtype="loan_prepayment",
            amount=Decimal("100000.00"),
            date=date(2023, 2, 15),
        )

        # Outstanding should drop by prepayment (3,000,000 - 5,000 - 100,000 = 2,895,000)
        current = get_current(self.account)
        self.assertEqual(current, Decimal("2895000.00"))

    def test_loan_fully_repaid_clamped_at_zero(self):
        """SPEC §2.4 Edge Case 3: Fully repaid loan outstanding = 0, never negative."""
        LoanRepayment.objects.create(
            loan=self.loan,
            amount=Decimal("3500000.00"),
            principal_portion=Decimal("3000000.00"),
            interest_portion=Decimal("500000.00"),
            date=date(2024, 1, 1),
        )
        current = get_current(self.account)
        self.assertEqual(current, Decimal("0.00"))

    def test_auto_inactivation_on_100_percent_paid(self):
        """When loan is 100% paid off, it automatically becomes inactive (is_active=False)."""
        self.assertTrue(self.loan.is_active)
        LoanRepayment.objects.create(
            loan=self.loan,
            amount=Decimal("3500000.00"),
            principal_portion=Decimal("3000000.00"),
            interest_portion=Decimal("500000.00"),
            date=date(2024, 1, 1),
        )
        self.loan.refresh_from_db()
        self.assertFalse(self.loan.is_active)

    def test_loan_list_htmx_partial(self):
        """HTMX request to loan list returns the partial template without base.html layout."""
        self.client.force_login(self.user)
        response = self.client.get(reverse('loan-list'), {'status': 'inactive'}, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.template_name[0], 'expenses/partials/_loan_list_partial.html')
        self.assertIn('loans-shell', str(response.content))
        self.assertNotIn('<!DOCTYPE html>', str(response.content))
