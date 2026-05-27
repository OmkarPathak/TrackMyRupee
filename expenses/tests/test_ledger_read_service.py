from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, Expense, Loan, LoanRepayment, SavingsGoal, GoalContribution


class LedgerReadServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pass")
        self.user.profile.currency = "₹"
        self.user.profile.save(update_fields=["currency"])

        self.cash = Account.objects.create(
            user=self.user,
            name="Cash",
            account_type="CASH",
            balance=Decimal("1000.00"),
            currency="₹",
        )

    @override_settings(LEDGER_READ_ENABLED=False, LEDGER_WRITE_ENABLED=True)
    def test_account_balance_falls_back_to_account_model_when_read_flag_off(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Lunch",
            category="Food",
            account=self.cash,
            currency="₹",
        )
        self.cash.refresh_from_db()
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), self.cash.balance)

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_account_balance_reads_from_ledger_when_enabled(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Lunch",
            category="Food",
            account=self.cash,
            currency="₹",
        )
        self.cash.refresh_from_db()
        # Without an opening balance adjustment journal, read adapter safely falls back.
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("900.00"))
        self.assertEqual(LedgerReadService.get_account_ledger_delta(self.cash), Decimal("-100.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_net_worth_uses_ledger_balances(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("150.00"),
            description="Groceries",
            category="Food",
            account=self.cash,
            currency="₹",
        )
        net_worth, base_balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(net_worth, Decimal("850.00"))
        self.assertEqual(base_balances[self.cash.id], Decimal("850.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_net_worth_subtracts_outstanding_loan_principal(self):
        loan = Loan.objects.create(
            user=self.user,
            name="Test Loan",
            loan_type="PERSONAL",
            initial_principal=Decimal("500.00"),
            duration_months=12,
            start_date=date.today(),
            currency="₹",
        )
        LoanRepayment.objects.create(
            loan=loan,
            from_account=self.cash,
            amount=Decimal("200.00"),
            principal_portion=Decimal("150.00"),
            interest_portion=Decimal("50.00"),
            date=date.today(),
        )

        # Assets: 1000 - 200 = 800
        # Remaining liability principal: 500 - 150 = 350
        # Net worth: 800 - 350 = 450
        net_worth, _ = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(net_worth, Decimal("450.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_goal_contribution_does_not_reduce_net_worth(self):
        goal = SavingsGoal.objects.create(
            user=self.user,
            name="Emergency Fund",
            target_amount=Decimal("10000.00"),
            target_date=date.today(),
            currency="₹",
        )

        net_worth_before, _ = LedgerReadService.get_net_worth(self.user)
        GoalContribution.objects.create(
            goal=goal,
            account=self.cash,
            amount=Decimal("200.00"),
            date=date.today(),
        )
        net_worth_after, _ = LedgerReadService.get_net_worth(self.user)

        self.assertEqual(net_worth_before, Decimal("1000.00"))
        self.assertEqual(net_worth_after, Decimal("1000.00"))
