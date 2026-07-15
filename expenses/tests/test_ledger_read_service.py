from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    SavingsGoal,
    Transfer,
)


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

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_ledger_balance_after_expense_update_and_delete(self):
        from expenses.ledger_service import LedgerPostingService
        
        # Enable ledger by posting opening balance
        # Cash starts with model balance 1000.00
        LedgerPostingService.post_opening_balance(account=self.cash)
        
        # 1. Create expense of 100
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Coffee",
            category="Food",
            account=self.cash,
            currency="₹",
        )
        self.cash.refresh_from_db()
        # model balance: 900, ledger balance: 900
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("900.00"))
        
        # 2. Update expense to 150
        expense.amount = Decimal("150.00")
        expense.save()
        self.cash.refresh_from_db()
        # model balance should be 850
        # ledger balance should be 850
        self.assertEqual(self.cash.balance, Decimal("850.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("850.00"))
        
        # 3. Delete expense
        expense.delete()
        self.cash.refresh_from_db()
        # model balance should be 1000
        # ledger balance should be 1000
        self.assertEqual(self.cash.balance, Decimal("1000.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1000.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_ledger_balance_after_transfer_update_and_delete(self):
        from expenses.ledger_service import LedgerPostingService
        self.bank = Account.objects.create(
            user=self.user,
            name="Bank",
            account_type="BANK",
            balance=Decimal("4000.00"),
            currency="₹",
        )
        # Enable ledger on both
        LedgerPostingService.post_opening_balance(account=self.cash)
        LedgerPostingService.post_opening_balance(account=self.bank)
        
        # Create transfer of 200 from bank to cash
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal("200.00"),
            date=date.today(),
            description="Transfer",
        )
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1200.00"))
        self.assertEqual(self.bank.balance, Decimal("3800.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1200.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), Decimal("3800.00"))
        
        # Update transfer amount to 300
        transfer.amount = Decimal("300.00")
        transfer.save()
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1300.00"))
        self.assertEqual(self.bank.balance, Decimal("3700.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1300.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), Decimal("3700.00"))
        
        # Delete transfer
        transfer.delete()
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1000.00"))
        self.assertEqual(self.bank.balance, Decimal("4000.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), Decimal("4000.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_ledger_balance_after_income_update_and_delete(self):
        from expenses.ledger_service import LedgerPostingService
        LedgerPostingService.post_opening_balance(account=self.cash)
        
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("500.00"),
            source="Salary",
            account=self.cash,
            currency="₹",
        )
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1500.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1500.00"))
        
        income.amount = Decimal("600.00")
        income.save()
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1600.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1600.00"))
        
        income.delete()
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, Decimal("1000.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1000.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_ledger_balance_after_switching_account_on_update(self):
        from expenses.ledger_service import LedgerPostingService
        
        # 1. Create second account
        self.bank = Account.objects.create(
            user=self.user,
            name="Bank",
            account_type="BANK",
            balance=Decimal("4000.00"),
            currency="₹",
        )
        
        # 2. Enable ledger on both
        LedgerPostingService.post_opening_balance(account=self.cash)
        LedgerPostingService.post_opening_balance(account=self.bank)
        
        # 3. Create expense on cash (100)
        expense = Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal("100.00"),
            description="Lunch",
            category="Food",
            account=self.cash,
            currency="₹",
        )
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("900.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), Decimal("4000.00"))
        
        # 4. Update expense to switch account to bank and change amount to 150
        expense.account = self.bank
        expense.amount = Decimal("150.00")
        expense.save()
        
        self.cash.refresh_from_db()
        self.bank.refresh_from_db()
        # cash balance should go back to 1000
        # bank balance should be 4000 - 150 = 3850
        self.assertEqual(self.cash.balance, Decimal("1000.00"))
        self.assertEqual(self.bank.balance, Decimal("3850.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1000.00"))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), Decimal("3850.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_ledger_balance_with_fluctuating_exchange_rates(self):
        from unittest.mock import patch

        from expenses.ledger_service import LedgerPostingService
        
        # Mock exchange rate first returns 80.00 on creation, then 85.00 on deletion.
        with patch('expenses.models.get_exchange_rate') as mock_model_rate, \
             patch('expenses.fx.get_exchange_rate') as mock_fx_rate, \
             patch('expenses.ledger_service.get_exchange_rate') as mock_service_rate, \
             patch('expenses.ledger_read_service.get_exchange_rate') as mock_read_rate_outer:
            
            # Setup mock returns:
            mock_model_rate.side_effect = [Decimal("80.00"), Decimal("80.00"), Decimal("85.00")]
            mock_fx_rate.side_effect = [Decimal("80.00"), Decimal("80.00"), Decimal("80.00"), Decimal("85.00"), Decimal("85.00"), Decimal("85.00")]
            mock_service_rate.side_effect = [Decimal("80.00"), Decimal("80.00"), Decimal("80.00"), Decimal("85.00"), Decimal("85.00"), Decimal("85.00")]
            mock_read_rate_outer.return_value = Decimal("80.00")
            
            LedgerPostingService.post_opening_balance(account=self.cash)
            
            # 1. Create USD expense of $10.00.
            # exchange rate: 80.00.
            expense = Expense.objects.create(
                user=self.user,
                date=date.today(),
                amount=Decimal("10.00"),
                description="Hosting",
                category="Software",
                account=self.cash,
                currency="$",
            )
            self.cash.refresh_from_db()
            self.assertEqual(self.cash.balance, Decimal("200.00"))
            self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("200.00"))
            
            # 2. Delete expense.
            # exchange rate: 85.00.
            expense.delete()
            self.cash.refresh_from_db()
            self.assertEqual(self.cash.balance, Decimal("1050.00"))
            
            # Verification that no exception is raised and ledger read is calculated correctly.
            with patch('expenses.fx.get_exchange_rate') as mock_read_rate:
                mock_read_rate.return_value = Decimal("85.00")
                self.assertEqual(LedgerReadService.get_account_balance(self.cash), Decimal("1000.00"))

    @override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True)
    def test_reconciliation_report_captures_drift(self):
        from django.core.management import call_command

        from expenses.ledger_service import LedgerPostingService
        from expenses.models import LedgerReconciliationReport
        
        # 1. Enable ledger on cash
        LedgerPostingService.post_opening_balance(account=self.cash)
        
        # 2. Manually modify cash model balance to create drift
        self.cash.balance = Decimal("2000.00")
        self.cash.save()
        
        # 3. Call reconcile_ledgers command
        call_command("reconcile_ledgers", user_id=self.user.id, threshold="0.01")
        
        # 4. Verify report has DRIFT status and drift amount is 1000.00
        report = LedgerReconciliationReport.objects.filter(account=self.cash).order_by("-created_at").first()
        self.assertIsNotNone(report)
        self.assertEqual(report.status, "DRIFT")
        self.assertEqual(report.drift_amount, Decimal("1000.00"))
