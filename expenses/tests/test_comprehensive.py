"""
Comprehensive unit tests for:
- Income model logic & balance updates
- Expense model logic & balance updates
- Recurring transactions processing
- Transfer model logic & balance updates
- Net worth calculations on the dashboard
- Account aggregations displayed on the dashboard
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import (
    Account,
    Category,
    Expense,
    Income,
    RecurringTransaction,
    Transfer,
)
from finance_tracker.plans import PLAN_DETAILS
from expenses.views.mixins import process_user_recurring_transactions

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _BaseTestCase(TestCase):
    """Common setUp that creates a user + profile + default accounts."""

    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        # Profile is auto-created by signal; ensure tutorial is marked done
        self.profile = self.user.profile
        self.profile.has_seen_tutorial = True
        self.profile.tier = "PRO"
        self.profile.save()

        # Canonical accounts
        self.cash = Account.objects.create(
            user=self.user, name="Cash", account_type="CASH", balance=Decimal("1000.00")
        )
        self.bank = Account.objects.create(
            user=self.user, name="Bank", account_type="BANK", balance=Decimal("5000.00")
        )
        self.investment = Account.objects.create(
            user=self.user, name="Investment", account_type="INVESTMENT", balance=Decimal("2000.00")
        )
        self.cc = Account.objects.create(
            user=self.user, name="Credit Card", account_type="CREDIT_CARD", balance=Decimal("-500.00")
        )

        # Default category
        Category.objects.get_or_create(user=self.user, name="Food", defaults={"limit": 5000})
        Category.objects.get_or_create(user=self.user, name="Rent", defaults={"limit": 15000})


# ===========================================================================
# 1. INCOME TESTS
# ===========================================================================

class IncomeCreationTest(_BaseTestCase):
    """Income model: creation, field defaults, whitespace stripping."""

    def test_income_creation_basic(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("5000.00"),
            source="Salary",
        )
        self.assertEqual(income.amount, Decimal("5000.00"))
        self.assertEqual(income.source, "Salary")
        self.assertEqual(income.currency, "₹")  # default

    def test_income_source_whitespace_stripped(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("1000.00"),
            source="  Freelance  ",
        )
        self.assertEqual(income.source, "Freelance")

    def test_income_base_amount_same_currency(self):
        """If income currency == profile currency, base_amount == amount."""
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("3000.00"),
            source="Dividend",
            currency="₹",
        )
        self.assertEqual(income.base_amount, Decimal("3000.00"))
        self.assertEqual(income.exchange_rate, Decimal("1.0"))


class IncomeAccountBalanceTest(_BaseTestCase):
    """Income ↔ Account balance interactions."""

    def test_income_adds_to_account_balance(self):
        Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("500.00"),
            source="Freelance",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5500.00"))

    def test_income_update_reverts_then_applies(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("500.00"),
            source="Freelance",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5500.00"))

        # Update amount
        income.amount = Decimal("800.00")
        income.save()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5800.00"))

    def test_income_update_switch_account(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("500.00"),
            source="Freelance",
            account=self.bank,
        )
        # Switch account to Cash
        income.account = self.cash
        income.amount = Decimal("300.00")
        income.save()

        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))  # reverted
        self.assertEqual(self.cash.balance, Decimal("1300.00"))

    def test_income_delete_reverts_balance(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("500.00"),
            source="Freelance",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5500.00"))

        income.delete()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))

    def test_income_without_account_no_balance_change(self):
        """Income with no linked account should not change any balance."""
        Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("1000.00"),
            source="Gift",
        )
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))
        self.assertEqual(self.cash.balance, Decimal("1000.00"))


class IncomeUniqueConstraintTest(_BaseTestCase):
    """Income unique constraint: user + date + amount + currency + source."""

    def test_duplicate_income_raises(self):
        Income.objects.create(
            user=self.user,
            date=date(2025, 6, 1),
            amount=Decimal("5000.00"),
            source="Salary",
            currency="₹",
        )
        with self.assertRaises(IntegrityError):
            Income.objects.create(
                user=self.user,
                date=date(2025, 6, 1),
                amount=Decimal("5000.00"),
                source="Salary",
                currency="₹",
            )

    def test_different_source_allowed(self):
        Income.objects.create(
            user=self.user, date=date(2025, 6, 1), amount=Decimal("5000.00"),
            source="Salary", currency="₹",
        )
        income2 = Income.objects.create(
            user=self.user, date=date(2025, 6, 1), amount=Decimal("5000.00"),
            source="Freelance", currency="₹",
        )
        self.assertIsNotNone(income2.pk)


# ===========================================================================
# 2. EXPENSE TESTS
# ===========================================================================

class ExpenseCreationTest(_BaseTestCase):
    """Expense model: creation, field defaults, whitespace stripping."""

    def test_expense_creation_basic(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            description="Groceries",
            category="Food",
        )
        self.assertEqual(expense.amount, Decimal("200.00"))
        self.assertEqual(expense.category, "Food")
        self.assertEqual(expense.currency, "₹")

    def test_expense_category_whitespace_stripped(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Test",
            category="  Food  ",
        )
        self.assertEqual(expense.category, "Food")

    def test_expense_base_amount_same_currency(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("150.00"),
            description="Bus",
            category="Transport",
            currency="₹",
        )
        self.assertEqual(expense.base_amount, Decimal("150.00"))
        self.assertEqual(expense.exchange_rate, Decimal("1.0"))

    def test_expense_str(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date(2025, 3, 15),
            amount=Decimal("100.50"),
            description="Test Expense",
            category="Food",
        )
        self.assertEqual(str(expense), "2025-03-15 - Test Expense - 100.50")


class ExpenseAccountBalanceTest(_BaseTestCase):
    """Expense ↔ Account balance interactions."""

    def test_expense_deducts_from_account(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            category="Food",
            description="Dinner",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4800.00"))

    def test_expense_update_reverts_then_applies(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            category="Food",
            description="Dinner",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4800.00"))

        expense.amount = Decimal("300.00")
        expense.save()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4700.00"))

    def test_expense_update_switch_account(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            category="Food",
            description="Dinner",
            account=self.bank,
        )
        expense.account = self.cash
        expense.amount = Decimal("100.00")
        expense.save()

        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))  # reverted
        self.assertEqual(self.cash.balance, Decimal("900.00"))

    def test_expense_delete_reverts_balance(self):
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            category="Food",
            description="Dinner",
            account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4800.00"))

        expense.delete()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))

    def test_expense_without_account_no_balance_change(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("200.00"),
            category="Food",
            description="No account",
        )
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))
        self.assertEqual(self.cash.balance, Decimal("1000.00"))

    def test_expense_on_credit_card_increases_liability(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            category="Food",
            description="CC Spend",
            account=self.cc,
        )
        self.cc.refresh_from_db()
        self.assertEqual(self.cc.balance, Decimal("-600.00"))


class ExpenseUniqueConstraintTest(_BaseTestCase):
    """Expense unique constraint: user + date + amount + currency + description + category."""

    def test_duplicate_expense_raises(self):
        Expense.objects.create(
            user=self.user, date=date(2025, 6, 1), amount=Decimal("100.00"),
            description="Lunch", category="Food", currency="₹",
        )
        with self.assertRaises(IntegrityError):
            Expense.objects.create(
                user=self.user, date=date(2025, 6, 1), amount=Decimal("100.00"),
                description="Lunch", category="Food", currency="₹",
            )

    def test_different_category_allowed(self):
        Expense.objects.create(
            user=self.user, date=date(2025, 6, 1), amount=Decimal("100.00"),
            description="Lunch", category="Food", currency="₹",
        )
        e2 = Expense.objects.create(
            user=self.user, date=date(2025, 6, 1), amount=Decimal("100.00"),
            description="Lunch", category="Entertainment", currency="₹",
        )
        self.assertIsNotNone(e2.pk)


# ===========================================================================
# 3. TRANSFER TESTS
# ===========================================================================

class TransferCreationTest(_BaseTestCase):
    """Transfer model: balance adjustments on create, update, delete."""

    def test_transfer_basic_balances(self):
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("500.00"),
            date=date.today(),
        )
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4500.00"))
        self.assertEqual(self.cash.balance, Decimal("1500.00"))

    def test_transfer_update_reverts_and_reapplies(self):
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("500.00"),
            date=date.today(),
        )
        transfer.amount = Decimal("200.00")
        transfer.save()

        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4800.00"))
        self.assertEqual(self.cash.balance, Decimal("1200.00"))

    def test_transfer_delete_reverts_balances(self):
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("500.00"),
            date=date.today(),
        )
        transfer.delete()

        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("5000.00"))
        self.assertEqual(self.cash.balance, Decimal("1000.00"))

    def test_transfer_to_investment_account(self):
        """The most common 'investment' flow: bank → investment account."""
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.investment,
            amount=Decimal("1000.00"),
            date=date.today(),
        )
        self.bank.refresh_from_db()
        self.investment.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4000.00"))
        self.assertEqual(self.investment.balance, Decimal("3000.00"))

    def test_transfer_cc_payment_zeroes_liability(self):
        """Pay off a $600 CC balance from bank."""
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cc,
            amount=Decimal("500.00"),
            date=date.today(),
        )
        self.cc.refresh_from_db()
        self.assertEqual(self.cc.balance, Decimal("0.00"))

    def test_multiple_transfers_cumulative(self):
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.cash,
            amount=Decimal("100.00"), date=date.today(),
        )
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.investment,
            amount=Decimal("500.00"), date=date.today(),
        )
        Transfer.objects.create(
            user=self.user, from_account=self.cash, to_account=self.investment,
            amount=Decimal("200.00"), date=date.today(),
        )
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.investment.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("4400.00"))   # 5000 - 100 - 500
        self.assertEqual(self.cash.balance, Decimal("900.00"))    # 1000 + 100 - 200
        self.assertEqual(self.investment.balance, Decimal("2700.00"))  # 2000 + 500 + 200

    def test_transfer_net_worth_unchanged(self):
        """Transfers between accounts must not change total net worth."""
        initial_net_worth = (
            self.bank.balance + self.cash.balance +
            self.investment.balance + self.cc.balance
        )
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.investment,
            amount=Decimal("1000.00"), date=date.today(),
        )
        self.bank.refresh_from_db()
        self.investment.refresh_from_db()
        new_net_worth = (
            self.bank.balance + self.cash.balance +
            self.investment.balance + self.cc.balance
        )
        self.assertEqual(initial_net_worth, new_net_worth)


# ===========================================================================
# 4. RECURRING TRANSACTION TESTS
# ===========================================================================

class RecurringTransactionDateTest(TestCase):
    """Static get_next_date logic for all frequencies."""

    def test_daily(self):
        self.assertEqual(
            RecurringTransaction.get_next_date(date(2025, 3, 1), "DAILY"),
            date(2025, 3, 2),
        )

    def test_weekly(self):
        self.assertEqual(
            RecurringTransaction.get_next_date(date(2025, 3, 1), "WEEKLY"),
            date(2025, 3, 8),
        )

    def test_monthly_normal(self):
        self.assertEqual(
            RecurringTransaction.get_next_date(date(2025, 3, 15), "MONTHLY"),
            date(2025, 4, 15),
        )

    def test_monthly_end_of_month_overflow(self):
        # Jan 31 → Feb 28/29
        result = RecurringTransaction.get_next_date(date(2025, 1, 31), "MONTHLY")
        self.assertEqual(result, date(2025, 2, 28))

    def test_monthly_leap_year(self):
        result = RecurringTransaction.get_next_date(date(2024, 1, 31), "MONTHLY")
        self.assertEqual(result, date(2024, 2, 29))

    def test_monthly_december_to_january(self):
        result = RecurringTransaction.get_next_date(date(2025, 12, 15), "MONTHLY")
        self.assertEqual(result, date(2026, 1, 15))

    def test_yearly_normal(self):
        self.assertEqual(
            RecurringTransaction.get_next_date(date(2025, 6, 1), "YEARLY"),
            date(2026, 6, 1),
        )

    def test_yearly_leap_day(self):
        # Feb 29 2024 → Feb 28 2025
        self.assertEqual(
            RecurringTransaction.get_next_date(date(2024, 2, 29), "YEARLY"),
            date(2025, 2, 28),
        )


class RecurringTransactionNextDueDateTest(_BaseTestCase):
    """next_due_date property logic."""

    def test_next_due_is_start_date_when_never_processed(self):
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
            description="Rent", frequency="MONTHLY", start_date=date(2025, 6, 1),
            category="Rent",
        )
        self.assertEqual(rt.next_due_date, date(2025, 6, 1))

    def test_next_due_after_processing(self):
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
            description="Rent", frequency="MONTHLY", start_date=date(2025, 1, 1),
            category="Rent", last_processed_date=date(2025, 3, 1),
        )
        self.assertEqual(rt.next_due_date, date(2025, 4, 1))


class RecurringTransactionProcessingTest(_BaseTestCase):
    """Integration: process_user_recurring_transactions creates expected records."""

    def test_monthly_expense_created(self):
        """A monthly recurring expense with start_date in the past should spawn Expense rows."""
        start = date.today() - timedelta(days=60)  # ~2 months ago
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("500.00"),
            description="Internet", frequency="MONTHLY", start_date=start,
            category="Bills",
        )
        process_user_recurring_transactions(self.user)

        # At least 2 expenses should have been created (months elapsed)
        expenses = Expense.objects.filter(user=self.user, description__contains="Internet")
        self.assertGreaterEqual(expenses.count(), 2)

        # RT should be updated
        rt.refresh_from_db()
        self.assertIsNotNone(rt.last_processed_date)

    def test_monthly_income_created(self):
        start = date.today() - timedelta(days=35)  # ~1 month ago
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type="INCOME", amount=Decimal("50000.00"),
            description="Salary", frequency="MONTHLY", start_date=start,
            source="Salary",
        )
        process_user_recurring_transactions(self.user)

        incomes = Income.objects.filter(user=self.user, description__contains="Salary")
        self.assertGreaterEqual(incomes.count(), 1)

    def test_daily_expense_creates_multiple(self):
        start = date.today() - timedelta(days=5)
        RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("50.00"),
            description="Coffee", frequency="DAILY", start_date=start,
            category="Food",
        )
        process_user_recurring_transactions(self.user)

        expenses = Expense.objects.filter(user=self.user, description__contains="Coffee")
        # start + 5 days → at least 5 entries (start through today-1 or today)
        self.assertGreaterEqual(expenses.count(), 5)

    def test_future_start_date_creates_nothing(self):
        RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
            description="Future sub", frequency="MONTHLY",
            start_date=date.today() + timedelta(days=30),
            category="Bills",
        )
        process_user_recurring_transactions(self.user)
        self.assertEqual(
            Expense.objects.filter(user=self.user, description__contains="Future sub").count(), 0
        )

    def test_inactive_recurring_skipped(self):
        RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
            description="Cancelled gym", frequency="MONTHLY",
            start_date=date.today() - timedelta(days=60),
            category="Bills", is_active=False,
        )
        process_user_recurring_transactions(self.user)
        self.assertEqual(
            Expense.objects.filter(user=self.user, description__contains="Cancelled gym").count(), 0
        )

    def test_idempotent_processing(self):
        """Running processing twice should not duplicate records."""
        RecurringTransaction.objects.create(
            user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
            description="Netflix", frequency="MONTHLY",
            start_date=date.today() - timedelta(days=35),
            category="Entertainment",
        )
        process_user_recurring_transactions(self.user)
        count_after_first = Expense.objects.filter(
            user=self.user, description__contains="Netflix"
        ).count()

        process_user_recurring_transactions(self.user)
        count_after_second = Expense.objects.filter(
            user=self.user, description__contains="Netflix"
        ).count()

        self.assertEqual(count_after_first, count_after_second)

    def test_free_tier_respects_recurring_limit(self):
        """FREE tier should process up to its recurring transaction limit."""
        self.profile.tier = "FREE"
        self.profile.save()
        limit = PLAN_DETAILS['FREE']['limits']['recurring_transactions']
        
        # Create (limit + 1) recurring transactions
        for i in range(limit + 1):
            RecurringTransaction.objects.create(
                user=self.user, transaction_type="EXPENSE", amount=Decimal("100.00"),
                description=f"FREERT_{i}", frequency="MONTHLY",
                start_date=date.today() - timedelta(days=1),
                category="Bills",
            )
            
        process_user_recurring_transactions(self.user)
        # It should only have processed the first 'limit' transactions
        expenses_count = Expense.objects.filter(user=self.user, description__contains="FREERT_").count()
        self.assertEqual(expenses_count, limit)


# ===========================================================================
# 5. NET WORTH & ACCOUNTS DASHBOARD TESTS
# ===========================================================================

class DashboardNetWorthTest(_BaseTestCase):
    """Verify net_worth and investment_accounts_balance in dashboard context."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

    def test_net_worth_equals_sum_of_all_accounts(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

        expected = self.bank.balance + self.cash.balance + self.investment.balance + self.cc.balance
        self.assertEqual(response.context["net_worth"], expected)

    def test_investment_accounts_balance(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["investment_accounts_balance"], self.investment.balance)

    def test_net_worth_after_income(self):
        Income.objects.create(
            user=self.user, date=date.today(), amount=Decimal("1000.00"),
            source="Bonus", account=self.bank,
        )
        response = self.client.get(reverse("home"))
        expected = (
            Decimal("6000.00") +  # bank: 5000 + 1000
            Decimal("1000.00") +  # cash
            Decimal("2000.00") +  # investment
            Decimal("-500.00")    # cc
        )
        self.assertEqual(response.context["net_worth"], expected)

    def test_net_worth_after_expense(self):
        Expense.objects.create(
            user=self.user, date=date.today(), amount=Decimal("500.00"),
            description="Shopping", category="Shopping", account=self.bank,
        )
        response = self.client.get(reverse("home"))
        expected = (
            Decimal("4500.00") +  # bank
            Decimal("1000.00") +  # cash
            Decimal("2000.00") +  # investment
            Decimal("-500.00")    # cc
        )
        self.assertEqual(response.context["net_worth"], expected)

    def test_net_worth_unchanged_after_transfer(self):
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.investment,
            amount=Decimal("1000.00"), date=date.today(),
        )
        response = self.client.get(reverse("home"))
        expected = (
            Decimal("4000.00") +  # bank
            Decimal("1000.00") +  # cash
            Decimal("3000.00") +  # investment
            Decimal("-500.00")    # cc
        )
        self.assertEqual(response.context["net_worth"], expected)
        # Investment balance should have increased
        self.assertEqual(response.context["investment_accounts_balance"], Decimal("3000.00"))

    def test_net_worth_no_accounts(self):
        """User with no accounts → net_worth = 0."""
        other_user = User.objects.create_user(username="noaccounts", password="password")
        other_user.profile.has_seen_tutorial = True
        other_user.profile.tier = "PRO"
        other_user.profile.save()

        client = Client()
        client.login(username="noaccounts", password="password")
        # Need at least some data or tutorial seen to avoid onboarding redirect
        Income.objects.create(user=other_user, date=date.today(), amount=Decimal("100"), source="Test")

        response = client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["net_worth"], Decimal("0.00"))


class DashboardAssetAllocationTest(_BaseTestCase):
    """Verify asset_allocation breakdown in dashboard context."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

    def test_asset_allocation_types_present(self):
        # Ensure CC has a positive balance so it shows up in asset allocation context
        cc = Account.objects.get(user=self.user, account_type="CREDIT_CARD")
        cc.balance = Decimal("500.00")
        cc.save()

        response = self.client.get(reverse("home"))
        allocation = response.context["asset_allocation"]
        type_names = [a["type"] for a in allocation]
        # All four account types should appear now
        self.assertIn("Bank Account", type_names)
        self.assertIn("Cash", type_names)
        self.assertIn("Investment Account", type_names)
        self.assertIn("Credit Card", type_names)

    def test_asset_allocation_percentages_sum_roughly_100(self):
        response = self.client.get(reverse("home"))
        allocation = response.context["asset_allocation"]
        total_pct = sum(a["percent"] for a in allocation)
        # Rounding may cause small deviations; should be close to 100
        self.assertAlmostEqual(total_pct, 100.0, delta=1.0)

    def test_asset_allocation_totals_match_balances(self):
        # Temporarily give CC a positive balance to test presence in asset allocation
        # (Since liabilities are skipped in the donut chart)
        cc = Account.objects.get(user=self.user, account_type="CREDIT_CARD")
        cc.balance = Decimal("500.00")
        cc.save()

        response = self.client.get(reverse("home"))
        allocation = response.context["asset_allocation"]
        alloc_map = {a["type"]: a["total"] for a in allocation}
        self.assertAlmostEqual(alloc_map["Bank Account"], 5000.00, places=2)
        self.assertAlmostEqual(alloc_map["Cash"], 1000.00, places=2)
        self.assertAlmostEqual(alloc_map["Investment Account"], 2000.00, places=2)
        self.assertAlmostEqual(alloc_map["Credit Card"], 500.00, places=2)


class DashboardIncomeExpenseSavingsTest(_BaseTestCase):
    """Verify total_income, total_expenses, savings calculated for the filtered period."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

        today = date.today()
        # Seed data for current month
        Income.objects.create(
            user=self.user, date=today, amount=Decimal("10000.00"),
            source="Salary", currency="₹",
        )
        Expense.objects.create(
            user=self.user, date=today, amount=Decimal("3000.00"),
            description="Rent", category="Rent", currency="₹",
        )
        Expense.objects.create(
            user=self.user, date=today, amount=Decimal("1000.00"),
            description="Groceries", category="Food", currency="₹",
        )

    def test_total_income_current_month(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_income"]), 10000.00)

    def test_total_expenses_current_month(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_expenses"]), 4000.00)

    def test_savings_current_month(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["savings"]), 6000.00)

    def test_hero_metrics_savings_rate(self):
        response = self.client.get(reverse("home"))
        hero = response.context["hero_metrics"]
        # savings_rate = 6000/10000 * 100 = 60%
        self.assertEqual(hero["savings_rate"], 60.0)
        self.assertEqual(hero["status"], "excellent")  # >= 20%


class DashboardTransferAggregationTest(_BaseTestCase):
    """Verify total_transfers and transfer_count in dashboard context."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

        today = date.today()
        # Need at least one income/expense so onboarding redirect doesn't fire
        Income.objects.create(user=self.user, date=today, amount=Decimal("100"), source="Seed")

        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.investment,
            amount=Decimal("1000.00"), date=today,
        )
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.cash,
            amount=Decimal("500.00"), date=today,
        )

    def test_total_transfers_for_current_month(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_transfers"]), 1500.00)

    def test_transfer_count_for_current_month(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.context["transfer_count"], 2)


class DashboardDataIsolationTest(_BaseTestCase):
    """Ensure one user cannot see another user's data on the dashboard."""

    def setUp(self):
        super().setUp()
        self.other_user = User.objects.create_user(username="other", password="password")
        self.other_user.profile.has_seen_tutorial = True
        self.other_user.profile.save()

        today = date.today()
        # Other user data
        other_bank = Account.objects.create(
            user=self.other_user, name="Bank", account_type="BANK", balance=Decimal("99999.00")
        )
        Income.objects.create(user=self.other_user, date=today, amount=Decimal("99999"), source="Big salary")
        Expense.objects.create(user=self.other_user, date=today, amount=Decimal("9999"), description="Big spend", category="Food")

        # Own data
        Income.objects.create(user=self.user, date=today, amount=Decimal("100"), source="My salary")

        self.client = Client()
        self.client.login(username="testuser", password="password")

    def test_net_worth_excludes_other_user(self):
        response = self.client.get(reverse("home"))
        # Should only reflect self.user's accounts
        expected = self.bank.balance + self.cash.balance + self.investment.balance + self.cc.balance
        self.assertEqual(response.context["net_worth"], expected)

    def test_income_excludes_other_user(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_income"]), 100.00)


class DashboardFilterTest(_BaseTestCase):
    """Test year/month/date-range filters affect totals correctly."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

        today = date.today()
        last_month = today.replace(day=1) - timedelta(days=1)

        Income.objects.create(user=self.user, date=today, amount=Decimal("5000"), source="Now", currency="₹")
        Income.objects.create(user=self.user, date=last_month, amount=Decimal("3000"), source="Before", currency="₹")
        Expense.objects.create(user=self.user, date=today, amount=Decimal("1000"), description="Now", category="Food", currency="₹")
        Expense.objects.create(user=self.user, date=last_month, amount=Decimal("2000"), description="Before", category="Food", currency="₹")

    def test_current_month_filter_default(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_income"]), 5000.00)
        self.assertEqual(float(response.context["total_expenses"]), 1000.00)

    def test_date_range_filter(self):
        today = date.today()
        last_month = today.replace(day=1) - timedelta(days=1)
        response = self.client.get(reverse("home"), {
            "start_date": last_month.strftime("%Y-%m-%d"),
            "end_date": today.strftime("%Y-%m-%d"),
        })
        # Should include both months
        self.assertEqual(float(response.context["total_income"]), 8000.00)
        self.assertEqual(float(response.context["total_expenses"]), 3000.00)

    def test_specific_year_month_filter(self):
        today = date.today()
        last_month = today.replace(day=1) - timedelta(days=1)
        response = self.client.get(reverse("home"), {
            "year": str(last_month.year),
            "month": str(last_month.month),
        })
        self.assertEqual(float(response.context["total_income"]), 3000.00)
        self.assertEqual(float(response.context["total_expenses"]), 2000.00)


class DashboardMoMChangeTest(_BaseTestCase):
    """Verify month-over-month comparison data (prev_month_data)."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

        today = date.today()
        last_month = today.replace(day=1) - timedelta(days=1)

        # Last month data
        Income.objects.create(user=self.user, date=last_month, amount=Decimal("8000"), source="Salary", currency="₹")
        Expense.objects.create(user=self.user, date=last_month, amount=Decimal("5000"), description="Rent", category="Rent", currency="₹")

        # This month data
        Income.objects.create(user=self.user, date=today, amount=Decimal("10000"), source="Salary", currency="₹")
        Expense.objects.create(user=self.user, date=today, amount=Decimal("4000"), description="Rent", category="Rent", currency="₹")

    def test_prev_month_data_present(self):
        response = self.client.get(reverse("home"))
        self.assertIsNotNone(response.context["prev_month_data"])

    def test_income_pct_change(self):
        response = self.client.get(reverse("home"))
        pmd = response.context["prev_month_data"]
        # 10000 vs 8000 → +25%
        self.assertAlmostEqual(pmd["income_pct"], 25.0, places=1)

    def test_expense_pct_change(self):
        response = self.client.get(reverse("home"))
        pmd = response.context["prev_month_data"]
        # 4000 vs 5000 → -20%
        self.assertAlmostEqual(pmd["expense_pct"], -20.0, places=1)

    def test_savings_diff(self):
        response = self.client.get(reverse("home"))
        pmd = response.context["prev_month_data"]
        # Last month savings: 8000-5000=3000, This month: 10000-4000=6000
        self.assertAlmostEqual(float(pmd["income_diff_amount"]), 2000.0, places=2)


# ===========================================================================
# 6. COMBINED SCENARIO TESTS (End-to-End-like)
# ===========================================================================

class CombinedScenarioTest(_BaseTestCase):
    """
    Simulates a realistic month: salary in, rent out, investment transfer,
    credit card spend + payment. Verifies final balances and dashboard totals.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username="testuser", password="password")

    def test_full_month_scenario(self):
        today = date.today()

        # 1. Salary credited into Bank
        Income.objects.create(
            user=self.user, date=today, amount=Decimal("50000.00"),
            source="Salary", account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("55000.00"))

        # 2. Rent paid from Bank
        Expense.objects.create(
            user=self.user, date=today, amount=Decimal("15000.00"),
            description="Rent", category="Rent", account=self.bank,
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("40000.00"))

        # 3. Transfer to investment
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.investment,
            amount=Decimal("10000.00"), date=today,
        )
        self.bank.refresh_from_db()
        self.investment.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("30000.00"))
        self.assertEqual(self.investment.balance, Decimal("12000.00"))

        # 4. CC spend
        Expense.objects.create(
            user=self.user, date=today, amount=Decimal("2000.00"),
            description="Shopping", category="Shopping", account=self.cc,
        )
        self.cc.refresh_from_db()
        self.assertEqual(self.cc.balance, Decimal("-2500.00"))

        # 5. Pay CC bill from Bank
        Transfer.objects.create(
            user=self.user, from_account=self.bank, to_account=self.cc,
            amount=Decimal("2500.00"), date=today,
        )
        self.bank.refresh_from_db()
        self.cc.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal("27500.00"))
        self.assertEqual(self.cc.balance, Decimal("0.00"))

        # 6. Verify dashboard
        response = self.client.get(reverse("home"))

        # Net worth = 27500 + 1000 + 12000 + 0 = 40500
        self.assertEqual(response.context["net_worth"], Decimal("40500.00"))

        # Total income = 50000
        self.assertEqual(float(response.context["total_income"]), 50000.00)

        # Total expenses = 15000 + 2000 = 17000
        self.assertEqual(float(response.context["total_expenses"]), 17000.00)

        # Savings = 50000 - 17000 = 33000
        self.assertEqual(float(response.context["savings"]), 33000.00)

        # Investment balance
        self.assertEqual(response.context["investment_accounts_balance"], Decimal("12000.00"))

        # Transfer total = 10000 + 2500 = 12500  (both in current month)
        self.assertEqual(float(response.context["total_transfers"]), 12500.00)
        self.assertEqual(response.context["transfer_count"], 2)

    def test_expense_delete_restores_dashboard_totals(self):
        today = date.today()
        Income.objects.create(
            user=self.user, date=today, amount=Decimal("5000.00"),
            source="Salary", account=self.bank,
        )
        expense = Expense.objects.create(
            user=self.user, date=today, amount=Decimal("1000.00"),
            description="Will delete", category="Food", account=self.bank,
        )

        # Before delete
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_expenses"]), 1000.00)

        # Delete and re-check
        expense.delete()
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_expenses"]), 0.00)
        self.assertEqual(float(response.context["savings"]), 5000.00)

    def test_income_delete_affects_dashboard(self):
        today = date.today()
        income = Income.objects.create(
            user=self.user, date=today, amount=Decimal("5000.00"),
            source="Freelance",
        )
        # Need a second seed so dashboard doesn't redirect
        Expense.objects.create(
            user=self.user, date=today, amount=Decimal("1000.00"),
            description="Dinner", category="Food",
        )

        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_income"]), 5000.00)

        income.delete()
        response = self.client.get(reverse("home"))
        self.assertEqual(float(response.context["total_income"]), 0.00)
