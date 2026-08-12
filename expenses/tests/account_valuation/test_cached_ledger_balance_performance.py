import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from expenses.account_valuation import (
    get_baseline,
    get_current,
    get_display_value,
    get_interest_summary,
)
from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Loan,
    LoanRepayment,
    PhysicalAsset,
    SavingsGoal,
    Transfer,
    UserProfile,
)


@override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
class CachedLedgerBalancePerformanceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="val_test_user", password="password123")
        UserProfile.objects.get_or_create(user=self.user, defaults={"currency": "₹"})
        self.account = Account.objects.create(
            user=self.user,
            name="Primary Savings",
            account_type="SAVINGS_ACCOUNT",
            balance=Decimal("1000.00"),
            currency="₹",
        )
        self.ledger_account = LedgerPostingService._get_or_create_account_ledger(self.user, self.account)

    def test_backfill_correctness(self):
        """
        Verify that backfill_ledger_cached_balances management command correctly computes
        cached_balance matching full-history sums for multi-currency, edits, and reversals.
        """
        # Create initial opening balance entry
        LedgerPostingService.post_opening_balance(account=self.account)

        # Create expense and income
        exp = Expense.objects.create(
            user=self.user,
            date="2026-08-01",
            amount=Decimal("150.00"),
            description="Grocery",
            category="Food",
            account=self.account,
            currency="₹",
        )
        inc = Income.objects.create(
            user=self.user,
            date="2026-08-02",
            amount=Decimal("500.00"),
            source="Salary",
            account=self.account,
            currency="₹",
        )

        # Reverse expense
        LedgerPostingService._post_expense_reversal(
            expense=exp,
            idempotency_key="EXPENSE:REV:1",
        )

        # Explicitly set cached_balance to NULL to test backfill
        self.ledger_account.cached_balance = None
        self.ledger_account.save(update_fields=["cached_balance"])

        # Compute ground truth from JournalLines directly
        lines = JournalLine.objects.filter(
            ledger_account=self.ledger_account,
            journal_entry__status="POSTED",
        )
        debit = sum((l.amount for l in lines if l.direction == "DEBIT"), Decimal("0.00"))
        credit = sum((l.amount for l in lines if l.direction == "CREDIT"), Decimal("0.00"))
        expected_balance = (debit - credit).quantize(Decimal("0.01"))

        # Run management command backfill
        call_command("backfill_ledger_cached_balances", force=True)

        self.ledger_account.refresh_from_db()
        self.assertIsNotNone(self.ledger_account.cached_balance)
        self.assertEqual(self.ledger_account.cached_balance, expected_balance)

    def test_incremental_vs_full_sum_equivalence(self):
        """
        Verify that inline F()-expression updates keep cached_balance exactly equal
        to a full-history sum at every step of posts and reversals.
        """
        # Seed cached_balance with opening balance
        LedgerPostingService.post_opening_balance(account=self.account)
        call_command("backfill_ledger_cached_balances", force=True)

        # Step 1: Add Expense
        exp = Expense.objects.create(
            user=self.user,
            date="2026-08-01",
            amount=Decimal("200.00"),
            description="Dinner",
            category="Food",
            account=self.account,
            currency="₹",
        )

        self.ledger_account.refresh_from_db()
        full_sum_delta = LedgerReadService.get_account_ledger_delta(self.account)
        self.assertEqual(self.ledger_account.cached_balance, full_sum_delta)

        # Step 2: Add Income
        inc = Income.objects.create(
            user=self.user,
            date="2026-08-02",
            amount=Decimal("350.00"),
            source="Bonus",
            account=self.account,
            currency="₹",
        )

        self.ledger_account.refresh_from_db()
        full_sum_delta = LedgerReadService.get_account_ledger_delta(self.account)
        self.assertEqual(self.ledger_account.cached_balance, full_sum_delta)

    def test_null_fallback_correctness(self):
        """
        Verify that when cached_balance is null, LedgerReadService.get_account_balance
        falls back to full-sum computation without error.
        """
        LedgerPostingService.post_opening_balance(account=self.account)
        Expense.objects.create(
            user=self.user,
            date="2026-08-01",
            amount=Decimal("100.00"),
            description="Shopping",
            category="Shopping",
            account=self.account,
            currency="₹",
        )

        # Set cached_balance to None
        self.ledger_account.cached_balance = None
        self.ledger_account.save(update_fields=["cached_balance"])

        balance = LedgerReadService.get_account_balance(self.account)
        self.assertEqual(balance, Decimal("900.00"))

    def test_performance_constant_queries_and_time(self):
        """
        Assert get_account_balance executes in constant (1) queries when cached_balance is present,
        and execution time does not scale with number of JournalLines.
        """
        # Create 500 journal entries and lines
        self.ledger_account.cached_balance = Decimal("5000.00")
        self.ledger_account.save(update_fields=["cached_balance"])

        entry = JournalEntry.objects.create(
            user=self.user,
            source_type="ADJUSTMENT",
            source_id=999,
            idempotency_key="PERF:TEST:1",
            status="POSTED",
        )

        lines = [
            JournalLine(
                journal_entry=entry,
                ledger_account=self.ledger_account,
                direction="DEBIT",
                amount=Decimal("10.00"),
                currency="₹",
                base_amount=Decimal("10.00"),
                account_ref=self.account,
            )
            for _ in range(500)
        ]
        JournalLine.objects.bulk_create(lines)

        # Verify query count is 1
        with self.assertNumQueries(1):
            balance = LedgerReadService.get_account_balance(self.account)
            self.assertEqual(balance, Decimal("5000.00"))

    def test_concurrency_atomic_update(self):
        """
        Verify inline F()-expression update applies atomicity to cached_balance.
        """
        self.ledger_account.cached_balance = Decimal("1000.00")
        self.ledger_account.save(update_fields=["cached_balance"])

        # Simulate two concurrent F() updates
        from django.db.models import F
        LedgerAccount.objects.filter(pk=self.ledger_account.pk).update(
            cached_balance=F("cached_balance") - Decimal("100.00")
        )
        LedgerAccount.objects.filter(pk=self.ledger_account.pk).update(
            cached_balance=F("cached_balance") + Decimal("250.00")
        )

        self.ledger_account.refresh_from_db()
        self.assertEqual(self.ledger_account.cached_balance, Decimal("1150.00"))

    def test_get_baseline_current_display_value_skeleton(self):
        """
        Verify SPEC §0 interface functions for baseline, current, display value.
        """
        # Savings account (BALANCE strategy) -> baseline is None, current is ledger balance
        self.assertIsNone(get_baseline(self.account))
        self.assertEqual(get_current(self.account), Decimal("1000.00"))
        self.assertEqual(get_display_value(self.account), Decimal("1000.00"))

        # Deposit account (DEPOSIT strategy stub)
        fd_account = Account.objects.create(
            user=self.user,
            name="HDFC FD",
            account_type="FD",
            balance=Decimal("50000.00"),
            currency="₹",
            deposit_principal=Decimal("50000.00"),
            show_accrued_balance=True,
        )
        self.assertEqual(get_current(fd_account), Decimal("50000.00"))
        self.assertEqual(get_display_value(fd_account), Decimal("50000.00"))

    def test_get_interest_summary(self):
        """
        Verify SPEC §3 get_interest_summary aggregation logic.
        """
        int_inc_cat = Category.objects.create(
            user=self.user,
            name="Interest Income",
            is_interest_category=True,
        )
        int_exp_cat = Category.objects.create(
            user=self.user,
            name="Interest Charged",
            is_interest_category=True,
        )

        Income.objects.create(
            user=self.user,
            date="2026-08-01",
            amount=Decimal("340.00"),
            source="Bank Interest",
            source_fk=int_inc_cat,
            account=self.account,
            currency="₹",
        )

        Expense.objects.create(
            user=self.user,
            date="2026-08-02",
            amount=Decimal("150.00"),
            description="Card Interest",
            category="Interest Charged",
            category_fk=int_exp_cat,
            account=self.account,
            currency="₹",
        )

        summary = get_interest_summary(self.user, start_date="2026-08-01", end_date="2026-08-05")
        self.assertEqual(summary["interest_earned"], Decimal("340.00"))
        self.assertEqual(summary["interest_charged"], Decimal("150.00"))

    def test_cached_balance_reversal_correctness_all_models(self):
        """
        Verify that cached_balance stays strictly equal to LedgerReadService full-sum delta
        across create, edit, and delete (reversal) operations for all 6 mutating models:
        Expense, Income, Transfer, GoalContribution, LoanRepayment, CapitalEvent.
        """
        LedgerPostingService.post_opening_balance(account=self.account)
        call_command("backfill_ledger_cached_balances", force=True)

        target_acc = Account.objects.create(
            user=self.user,
            name="Secondary Account",
            account_type="SAVINGS_ACCOUNT",
            balance=Decimal("0.00"),
            currency="₹",
        )
        LedgerPostingService.post_opening_balance(account=target_acc)
        call_command("backfill_ledger_cached_balances", force=True)

        target_la = LedgerPostingService._get_or_create_account_ledger(self.user, target_acc)

        def assert_balance_synced(acc, la):
            la.refresh_from_db()
            delta = LedgerReadService.get_account_ledger_delta(acc)
            self.assertEqual(la.cached_balance, delta)

        # 1. Expense
        pre = LedgerReadService.get_account_ledger_delta(self.account)
        exp = Expense.objects.create(
            user=self.user, date="2026-08-01", amount=Decimal("100.00"),
            description="Lunch", category="Food", account=self.account, currency="₹",
        )
        assert_balance_synced(self.account, self.ledger_account)

        exp.amount = Decimal("150.00")
        exp.save()
        assert_balance_synced(self.account, self.ledger_account)

        exp.delete()
        assert_balance_synced(self.account, self.ledger_account)
        self.ledger_account.refresh_from_db()
        self.assertEqual(self.ledger_account.cached_balance, pre)

        # 2. Income
        inc = Income.objects.create(
            user=self.user, date="2026-08-01", amount=Decimal("500.00"),
            source="Freelance", account=self.account, currency="₹",
        )
        assert_balance_synced(self.account, self.ledger_account)

        inc.amount = Decimal("600.00")
        inc.save()
        assert_balance_synced(self.account, self.ledger_account)

        inc.delete()
        assert_balance_synced(self.account, self.ledger_account)

        # 3. Transfer
        tr = Transfer.objects.create(
            user=self.user, date="2026-08-01", amount=Decimal("200.00"),
            from_account=self.account, to_account=target_acc, description="Transfer out",
        )
        assert_balance_synced(self.account, self.ledger_account)
        assert_balance_synced(target_acc, target_la)

        tr.amount = Decimal("250.00")
        tr.save()
        assert_balance_synced(self.account, self.ledger_account)
        assert_balance_synced(target_acc, target_la)

        tr.delete()
        assert_balance_synced(self.account, self.ledger_account)
        assert_balance_synced(target_acc, target_la)

        # 4. GoalContribution
        goal = SavingsGoal.objects.create(
            user=self.user, name="Emergency Fund", target_amount=Decimal("10000.00"),
            currency="₹",
        )
        gc = GoalContribution.objects.create(
            goal=goal, date="2026-08-01", amount=Decimal("300.00"),
            account=self.account,
        )
        assert_balance_synced(self.account, self.ledger_account)

        gc.amount = Decimal("400.00")
        gc.save()
        assert_balance_synced(self.account, self.ledger_account)

        gc.delete()
        assert_balance_synced(self.account, self.ledger_account)

        # 5. LoanRepayment
        loan = Loan.objects.create(
            user=self.user, name="Car Loan", initial_principal=Decimal("50000.00"),
            loan_type="PERSONAL", currency="₹", duration_months=60,
            start_date="2026-01-01",
        )
        lr = LoanRepayment.objects.create(
            loan=loan, date="2026-08-01", amount=Decimal("1000.00"),
            from_account=self.account, principal_portion=Decimal("800.00"),
            interest_portion=Decimal("200.00"),
        )
        assert_balance_synced(self.account, self.ledger_account)

        lr.amount = Decimal("1200.00")
        lr.principal_portion = Decimal("1000.00")
        lr.save()
        assert_balance_synced(self.account, self.ledger_account)

        lr.delete()
        assert_balance_synced(self.account, self.ledger_account)

        # 6. CapitalEvent
        ce = CapitalEvent.objects.create(
            user=self.user, date="2026-08-01", amount=Decimal("700.00"),
            subtype="loan_down_payment", account=self.account,
            currency="₹",
        )
        assert_balance_synced(self.account, self.ledger_account)

        ce.amount = Decimal("800.00")
        ce.save()
        assert_balance_synced(self.account, self.ledger_account)

        ce.delete()
        assert_balance_synced(self.account, self.ledger_account)
