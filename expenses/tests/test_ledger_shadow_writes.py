from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.models import (
    Account,
    Expense,
    GoalContribution,
    Income,
    JournalEntry,
    LedgerPostingFailure,
    Loan,
    LoanRepayment,
    SavingsGoal,
    Transfer,
)


class LedgerShadowWriteTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="shadow_user", password="password")
        self.user.profile.currency = "₹"
        self.user.profile.save(update_fields=["currency"])

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
            balance=Decimal("4000.00"),
            currency="₹",
        )

    @override_settings(LEDGER_WRITE_ENABLED=False)
    def test_expense_create_has_no_shadow_write_when_flag_off(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Coffee",
            category="Food",
            account=self.cash,
            currency="₹",
        )

        self.assertEqual(JournalEntry.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_expense_update_creates_reversal_and_new_post(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Coffee",
            category="Food",
            account=self.cash,
            currency="₹",
        )

        self.assertEqual(JournalEntry.objects.filter(source_type="EXPENSE", source_id=expense.id).count(), 1)

        expense.amount = Decimal("120.00")
        expense.description = "Coffee + snack"
        expense.save()

        entries = JournalEntry.objects.filter(source_type="EXPENSE", source_id=expense.id).order_by("created_at")
        self.assertEqual(entries.count(), 3)
        self.assertEqual(entries.filter(status="POSTED").count(), 3)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_expense_without_account_skips_shadow_failure(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Cash note",
            category="Food",
            account=None,
            currency="₹",
        )

        self.assertEqual(JournalEntry.objects.filter(source_type="EXPENSE", source_id=expense.id).count(), 0)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_expense_update_removing_account_only_reverses_shadow_entry(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Coffee",
            category="Food",
            account=self.cash,
            currency="₹",
        )

        expense.account = None
        expense.description = "Offline cash note"
        expense.save()

        entries = JournalEntry.objects.filter(source_type="EXPENSE", source_id=expense.id)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.filter(status="POSTED").count(), 2)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_expense_delete_creates_reversal_entry(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("80.00"),
            description="Snacks",
            category="Food",
            account=self.cash,
            currency="₹",
        )

        expense_id = expense.id
        expense.delete()

        entries = JournalEntry.objects.filter(source_type="EXPENSE", source_id=expense_id)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.filter(status="POSTED").count(), 2)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_income_create_writes_shadow_entry(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("2500.00"),
            source="Salary",
            account=self.bank,
            currency="₹",
        )
        self.assertEqual(
            JournalEntry.objects.filter(source_type="INCOME", source_id=income.id, status="POSTED").count(),
            1,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_income_without_account_skips_shadow_failure(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("2500.00"),
            source="Salary",
            account=None,
            currency="₹",
        )

        self.assertEqual(JournalEntry.objects.filter(source_type="INCOME", source_id=income.id).count(), 0)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_income_update_removing_account_only_reverses_shadow_entry(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("2500.00"),
            source="Salary",
            account=self.bank,
            currency="₹",
        )

        income.account = None
        income.description = "Manual adjustment"
        income.save()

        entries = JournalEntry.objects.filter(source_type="INCOME", source_id=income.id)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.filter(status="POSTED").count(), 2)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_transfer_create_writes_shadow_entry(self):
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("200.00"),
            date=date.today(),
            description="Move funds",
        )
        self.assertEqual(
            JournalEntry.objects.filter(source_type="TRANSFER", source_id=transfer.id, status="POSTED").count(),
            1,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_loan_repayment_create_writes_shadow_entry(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Car Loan",
            loan_type="CAR",
            initial_principal=Decimal("50000.00"),
            duration_months=60,
            start_date=date.today(),
            currency="₹",
        )
        repayment = LoanRepayment.objects.create(
            loan=loan,
            from_account=self.bank,
            amount=Decimal("1200.00"),
            principal_portion=Decimal("900.00"),
            interest_portion=Decimal("300.00"),
            date=date.today(),
        )
        self.assertEqual(
            JournalEntry.objects.filter(
                source_type="LOAN_REPAYMENT",
                source_id=repayment.id,
                status="POSTED",
            ).count(),
            1,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_loan_repayment_without_account_skips_shadow_failure(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Offline Loan",
            loan_type="PERSONAL",
            initial_principal=Decimal("50000.00"),
            duration_months=60,
            start_date=date.today(),
            currency="₹",
        )
        repayment = LoanRepayment.objects.create(
            loan=loan,
            from_account=None,
            amount=Decimal("1200.00"),
            principal_portion=Decimal("900.00"),
            interest_portion=Decimal("300.00"),
            date=date.today(),
        )

        self.assertEqual(
            JournalEntry.objects.filter(source_type="LOAN_REPAYMENT", source_id=repayment.id).count(),
            0,
        )
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_loan_repayment_update_removing_account_only_reverses_shadow_entry(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Car Loan",
            loan_type="CAR",
            initial_principal=Decimal("50000.00"),
            duration_months=60,
            start_date=date.today(),
            currency="₹",
        )
        repayment = LoanRepayment.objects.create(
            loan=loan,
            from_account=self.bank,
            amount=Decimal("1200.00"),
            principal_portion=Decimal("900.00"),
            interest_portion=Decimal("300.00"),
            date=date.today(),
        )

        repayment.from_account = None
        repayment.save()

        entries = JournalEntry.objects.filter(source_type="LOAN_REPAYMENT", source_id=repayment.id)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.filter(status="POSTED").count(), 2)
        self.assertEqual(LedgerPostingFailure.objects.count(), 0)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_goal_contribution_create_writes_shadow_entry(self):
        goal = SavingsGoal.objects.create(
            user=self.user,
            name="Emergency Fund",
            target_amount=Decimal("10000.00"),
            target_date=date.today(),
            currency="₹",
        )

        contribution = GoalContribution.objects.create(
            goal=goal,
            account=self.bank,
            amount=Decimal("500.00"),
            date=date.today(),
        )

        self.assertEqual(
            JournalEntry.objects.filter(
                source_type="GOAL_CONTRIBUTION",
                source_id=contribution.id,
                status="POSTED",
            ).count(),
            1,
        )

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_goal_contribution_update_creates_reversal_and_new_post(self):
        goal = SavingsGoal.objects.create(
            user=self.user,
            name="Emergency Fund",
            target_amount=Decimal("10000.00"),
            target_date=date.today(),
            currency="₹",
        )

        contribution = GoalContribution.objects.create(
            goal=goal,
            account=self.bank,
            amount=Decimal("500.00"),
            date=date.today(),
        )

        contribution.amount = Decimal("700.00")
        contribution.save()

        entries = JournalEntry.objects.filter(
            source_type="GOAL_CONTRIBUTION",
            source_id=contribution.id,
        ).order_by("created_at")

        self.assertEqual(entries.count(), 3)
        self.assertEqual(entries.filter(status="POSTED").count(), 3)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_goal_contribution_delete_creates_reversal_entry(self):
        goal = SavingsGoal.objects.create(
            user=self.user,
            name="Emergency Fund",
            target_amount=Decimal("10000.00"),
            target_date=date.today(),
            currency="₹",
        )

        contribution = GoalContribution.objects.create(
            goal=goal,
            account=self.bank,
            amount=Decimal("500.00"),
            date=date.today(),
        )
        contribution_id = contribution.id
        contribution.delete()

        entries = JournalEntry.objects.filter(source_type="GOAL_CONTRIBUTION", source_id=contribution_id)
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.filter(status="POSTED").count(), 2)
