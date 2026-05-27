from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    Expense,
    Income,
    JournalEntry,
    JournalLine,
    Loan,
    LoanRepayment,
    Transfer,
)


class LedgerPostingServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ledger_user", password="password")
        self.profile = self.user.profile
        self.profile.currency = "₹"
        self.profile.save(update_fields=["currency"])

        self.cash = Account.objects.create(
            user=self.user,
            name="Cash",
            account_type="CASH",
            balance=Decimal("1000.00"),
            currency="₹",
        )
        self.bank = Account.objects.create(
            user=self.user,
            name="Bank",
            account_type="BANK",
            balance=Decimal("5000.00"),
            currency="₹",
        )

    def _assert_entry_balanced(self, entry):
        debit = sum(
            (line.base_amount for line in entry.lines.filter(direction="DEBIT")),
            Decimal("0.00"),
        )
        credit = sum(
            (line.base_amount for line in entry.lines.filter(direction="CREDIT")),
            Decimal("0.00"),
        )
        self.assertEqual(debit, credit)

    def test_post_expense_balanced_and_idempotent(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("125.50"),
            description="Groceries",
            category="Food",
            account=self.cash,
            currency="₹",
        )

        key = f"EXPENSE:{expense.id}:1"
        entry, created = LedgerPostingService.post_expense(expense=expense, idempotency_key=key)

        self.assertTrue(created)
        self.assertEqual(entry.source_type, "EXPENSE")
        self.assertEqual(entry.lines.count(), 2)
        self._assert_entry_balanced(entry)

        entry_2, created_2 = LedgerPostingService.post_expense(expense=expense, idempotency_key=key)

        self.assertFalse(created_2)
        self.assertEqual(entry.id, entry_2.id)
        self.assertEqual(JournalEntry.objects.filter(idempotency_key=key).count(), 1)
        self.assertEqual(JournalLine.objects.filter(journal_entry=entry).count(), 2)

    def test_post_income_balanced(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("5000.00"),
            source="Salary",
            account=self.bank,
            currency="₹",
        )

        key = f"INCOME:{income.id}:1"
        entry, created = LedgerPostingService.post_income(income=income, idempotency_key=key)

        self.assertTrue(created)
        self.assertEqual(entry.source_type, "INCOME")
        self.assertEqual(entry.lines.count(), 2)
        self._assert_entry_balanced(entry)

    def test_post_transfer_balanced(self):
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("250.00"),
            date=date.today(),
            description="Top up wallet",
        )

        key = f"TRANSFER:{transfer.id}:1"
        entry, created = LedgerPostingService.post_transfer(transfer=transfer, idempotency_key=key)

        self.assertTrue(created)
        self.assertEqual(entry.source_type, "TRANSFER")
        self.assertEqual(entry.lines.count(), 2)
        self._assert_entry_balanced(entry)

    def test_post_loan_repayment_balanced(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Home Loan",
            loan_type="HOME",
            initial_principal=Decimal("100000.00"),
            duration_months=120,
            start_date=date.today(),
            currency="₹",
        )

        repayment = LoanRepayment.objects.create(
            loan=loan,
            from_account=self.bank,
            amount=Decimal("1500.00"),
            principal_portion=Decimal("1100.00"),
            interest_portion=Decimal("400.00"),
            date=date.today(),
        )

        key = f"LOAN_REPAYMENT:{repayment.id}:1"
        entry, created = LedgerPostingService.post_loan_repayment(
            repayment=repayment,
            idempotency_key=key,
        )

        self.assertTrue(created)
        self.assertEqual(entry.source_type, "LOAN_REPAYMENT")
        self.assertEqual(entry.lines.count(), 3)
        self._assert_entry_balanced(entry)

    def test_post_loan_repayment_balanced_with_zero_interest(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Zero Interest Loan",
            loan_type="PERSONAL",
            initial_principal=Decimal("10000.00"),
            duration_months=10,
            start_date=date.today(),
            currency="₹",
        )

        repayment = LoanRepayment.objects.create(
            loan=loan,
            from_account=self.bank,
            amount=Decimal("1000.00"),
            principal_portion=Decimal("1000.00"),
            interest_portion=Decimal("0.00"),
            date=date.today(),
        )

        key = f"LOAN_REPAYMENT:{repayment.id}:ZERO_INTEREST"
        entry, created = LedgerPostingService.post_loan_repayment(
            repayment=repayment,
            idempotency_key=key,
        )

        self.assertTrue(created)
        self.assertEqual(entry.source_type, "LOAN_REPAYMENT")
        self.assertEqual(entry.lines.count(), 2)
        self._assert_entry_balanced(entry)
