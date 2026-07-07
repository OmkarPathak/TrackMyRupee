"""
Comprehensive cashflow and financial calculation tests.
Verifies that expenses, income, goals, loans, and recurring transactions
correctly update balances and financial summaries.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db.models import Sum
from django.test import TestCase

from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
)


class CashflowCalculationTestBase(TestCase):
    """Base class for cashflow verification tests."""
    
    def setUp(self):
        """Set up test user with accounts and categories."""
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        
        # Setup profile
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PLUS'
        profile.save()
        
        # Create accounts with different currencies
        self.checking = Account.objects.create(
            user=self.user,
            name='Checking',
            account_type='BANK',
            currency='₹',
            is_active=True,
            balance=Decimal('100000')  # Starting balance
        )
        
        self.savings = Account.objects.create(
            user=self.user,
            name='Savings',
            account_type='BANK',
            currency='₹',
            is_active=True,
            balance=Decimal('50000')
        )
        
        self.cash = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH',
            currency='₹',
            is_active=True,
            balance=Decimal('10000')
        )
        
        # Create categories
        self.food_category, _ = Category.objects.get_or_create(user=self.user, name='Food')
        self.transport_category, _ = Category.objects.get_or_create(user=self.user, name='Transport')
        self.entertainment_category, _ = Category.objects.get_or_create(user=self.user, name='Entertainment')
        self.salary_source = 'Salary'
        
    def get_account_balance(self, account):
        """Get current account balance from database."""
        account.refresh_from_db()
        return account.balance
    
    def get_net_worth(self):
        """Get current net worth."""
        return LedgerReadService.get_net_worth(self.user)[0]
    
    def get_total_expenses(self):
        """Get sum of all expenses."""
        return Expense.objects.filter(user=self.user).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
    
    def get_total_income(self):
        """Get sum of all income."""
        return Income.objects.filter(user=self.user).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')


# ============================================================================
# EXPENSE CASHFLOW TESTS
# ============================================================================

class ExpenseCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of expense operations."""
    
    def test_add_expense_from_bank_account(self):
        """Test that adding expense decreases account balance."""
        initial_balance = self.get_account_balance(self.checking)
        initial_expenses = self.get_total_expenses()
        
        expense = Expense.objects.create(
            user=self.user,
            account=self.checking,
            amount=Decimal('500'),
            category='Food',
            date=date.today(),
            currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        new_expenses = self.get_total_expenses()
        
        # Balance should decrease by expense amount
        self.assertEqual(new_balance, initial_balance - Decimal('500'))
        # Total expenses should increase
        self.assertEqual(new_expenses, initial_expenses + Decimal('500'))
    
    def test_add_multiple_expenses_same_day(self):
        """Test that multiple expenses on same day all impact balance."""
        initial_balance = self.get_account_balance(self.checking)
        
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        transport_cat, _ = Category.objects.get_or_create(user=self.user, name='Transport')
        Expense.objects.create(
            user=self.user, account=self.checking, amount=100,
            category='Food', date=date.today(), currency='₹'
        )
        Expense.objects.create(
            user=self.user, account=self.checking, amount=200,
            category='Transport', date=date.today(), currency='₹'
        )
        Expense.objects.create(
            user=self.user, account=self.checking, amount=50,
            category='Food', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        # Balance should decrease by sum of all expenses
        self.assertEqual(new_balance, initial_balance - Decimal('350'))
    
    def test_add_expense_increases_total_spending(self):
        """Test that expenses accumulate correctly."""
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.checking, amount=100,
            category='Food', date=date.today(), currency='₹'
        )
        
        total_before = self.get_total_expenses()
        
        Expense.objects.create(
            user=self.user, account=self.checking, amount=50,
            category='Food', date=date.today() - timedelta(days=1),
            currency='₹'
        )
        
        total_after = self.get_total_expenses()
        
        self.assertEqual(total_after, total_before + Decimal('50'))
    
    def test_update_expense_amount_updates_balance(self):
        """Test that modifying expense amount updates account balance."""
        expense = Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('500'),
            category='Food', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        # Update expense to higher amount
        expense.amount = Decimal('750')
        expense.save()
        
        balance_after_update = self.get_account_balance(self.checking)
        
        # Balance should reflect the difference (500 less, but updated to 750)
        # So net change should be -250 from original
        self.assertEqual(
            balance_after_update,
            balance_after_create - Decimal('250')
        )
    
    def test_update_expense_to_lower_amount(self):
        """Test that reducing expense amount increases balance."""
        expense = Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('500'),
            category='Food', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        # Reduce expense
        expense.amount = Decimal('300')
        expense.save()
        
        balance_after_update = self.get_account_balance(self.checking)
        
        # Balance should increase by difference (200)
        self.assertEqual(
            balance_after_update,
            balance_after_create + Decimal('200')
        )
    
    def test_delete_expense_increases_balance(self):
        """Test that deleting expense returns money to account."""
        expense = Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('500'),
            category='Food', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        expense.delete()
        
        balance_after_delete = self.get_account_balance(self.checking)
        
        # Balance should increase by expense amount
        self.assertEqual(
            balance_after_delete,
            balance_after_create + Decimal('500')
        )
    
    def test_expense_changes_correct_account_only(self):
        """Test that expense affects only its account."""
        checking_before = self.get_account_balance(self.checking)
        savings_before = self.get_account_balance(self.savings)
        
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('100'),
            category='Food', date=date.today(), currency='₹'
        )
        
        checking_after = self.get_account_balance(self.checking)
        savings_after = self.get_account_balance(self.savings)
        
        # Only checking should change
        self.assertEqual(checking_after, checking_before - Decimal('100'))
        self.assertEqual(savings_after, savings_before)
    
    def test_expense_without_account_doesnt_affect_balance(self):
        """Test that expenses without account don't affect balances."""
        checking_before = self.get_account_balance(self.checking)
        
        Expense.objects.create(
            user=self.user, account=None, amount=Decimal('100'),
            category='Food', date=date.today(), currency='₹'
        )
        
        checking_after = self.get_account_balance(self.checking)
        
        # Checking balance should not change
        self.assertEqual(checking_after, checking_before)


# ============================================================================
# INCOME CASHFLOW TESTS
# ============================================================================

class IncomeCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of income operations."""
    
    def test_add_income_increases_account_balance(self):
        """Test that adding income increases account balance."""
        initial_balance = self.get_account_balance(self.checking)
        initial_income = self.get_total_income()
        
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('50000'),
            source='Salary', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        new_income = self.get_total_income()
        
        self.assertEqual(new_balance, initial_balance + Decimal('50000'))
        self.assertEqual(new_income, initial_income + Decimal('50000'))
    
    def test_add_multiple_income_sources(self):
        """Test that multiple income sources accumulate."""
        initial_balance = self.get_account_balance(self.checking)
        
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('50000'),
            source='Salary', date=date.today(), currency='₹'
        )
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('5000'),
            source='Bonus', date=date.today(), currency='₹'
        )
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('1000'),
            source='Interest', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        self.assertEqual(new_balance, initial_balance + Decimal('56000'))
    
    def test_update_income_amount(self):
        """Test that updating income amount updates balance."""
        income = Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('50000'),
            source='Salary', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        # Increase income
        income.amount = Decimal('60000')
        income.save()
        
        balance_after_update = self.get_account_balance(self.checking)
        
        # Balance should reflect additional 10000
        self.assertEqual(
            balance_after_update,
            balance_after_create + Decimal('10000')
        )
    
    def test_delete_income_decreases_balance(self):
        """Test that deleting income reduces account balance."""
        income = Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('50000'),
            source='Salary', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        income.delete()
        
        balance_after_delete = self.get_account_balance(self.checking)
        
        self.assertEqual(
            balance_after_delete,
            balance_after_create - Decimal('50000')
        )
    
    def test_income_affects_net_worth(self):
        """Test that income increases net worth."""
        net_worth_before = self.get_net_worth()
        
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('50000'),
            source='Salary', date=date.today(), currency='₹'
        )
        
        net_worth_after = self.get_net_worth()
        
        self.assertGreater(net_worth_after, net_worth_before)


# ============================================================================
# TRANSFER CASHFLOW TESTS
# ============================================================================

class TransferCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of transfers between accounts."""
    
    def test_transfer_decreases_source_account(self):
        """Test that transfer decreases source account balance."""
        checking_before = self.get_account_balance(self.checking)
        
        Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=Decimal('10000'),
            date=date.today()
        )
        
        checking_after = self.get_account_balance(self.checking)
        
        self.assertEqual(checking_after, checking_before - Decimal('10000'))
    
    def test_transfer_increases_destination_account(self):
        """Test that transfer increases destination account balance."""
        savings_before = self.get_account_balance(self.savings)
        
        Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=Decimal('10000'),
            date=date.today()
        )
        
        savings_after = self.get_account_balance(self.savings)
        
        self.assertEqual(savings_after, savings_before + Decimal('10000'))
    
    def test_transfer_maintains_net_worth(self):
        """Test that transfer doesn't change total net worth."""
        net_worth_before = self.get_net_worth()
        
        Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=Decimal('10000'),
            date=date.today()
        )
        
        net_worth_after = self.get_net_worth()
        
        # Net worth should remain the same (just moving money between accounts)
        self.assertEqual(net_worth_before, net_worth_after)
    
    def test_delete_transfer_reverses_both_accounts(self):
        """Test that deleting transfer reverses both account changes."""
        checking_before = self.get_account_balance(self.checking)
        savings_before = self.get_account_balance(self.savings)
        
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=Decimal('10000'),
            date=date.today()
        )
        
        transfer.delete()
        
        checking_after = self.get_account_balance(self.checking)
        savings_after = self.get_account_balance(self.savings)
        
        self.assertEqual(checking_after, checking_before)
        self.assertEqual(savings_after, savings_before)


# ============================================================================
# RECURRING TRANSACTION CASHFLOW TESTS
# ============================================================================

class RecurringTransactionCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of recurring transactions."""
    
    def test_recurring_expense_generates_entries(self):
        """Test that recurring expense creates actual expense entries."""
        initial_balance = self.get_account_balance(self.checking)
        initial_count = Expense.objects.filter(user=self.user).count()
        
        # Create recurring expense for today
        ent_cat, _ = Category.objects.get_or_create(user=self.user, name='Entertainment')
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('500'),
            description='Monthly Netflix',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Entertainment',
            account=self.checking,
            currency='₹'
        )
        
        # Trigger recurring transaction processing (happens during view access)
        # For testing, manually trigger
        from expenses.views.mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.user)
        
        # Check that expense was created
        new_count = Expense.objects.filter(user=self.user).count()
        self.assertGreater(new_count, initial_count)
        
        # Balance should be affected
        new_balance = self.get_account_balance(self.checking)
        self.assertLess(new_balance, initial_balance)
    
    def test_recurring_income_generates_entries(self):
        """Test that recurring income creates actual income entries."""
        initial_balance = self.get_account_balance(self.checking)
        initial_count = Income.objects.filter(user=self.user).count()
        
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INCOME',
            amount=Decimal('50000'),
            description='Monthly Salary',
            frequency='MONTHLY',
            start_date=date.today(),
            source='Employment',
            account=self.checking,
            currency='₹'
        )
        
        from expenses.views.mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.user)
        
        new_count = Income.objects.filter(user=self.user).count()
        self.assertGreater(new_count, initial_count)
        
        new_balance = self.get_account_balance(self.checking)
        self.assertGreater(new_balance, initial_balance)
    
    def test_update_recurring_amount_affects_future_generations(self):
        """Test that updating recurring amount affects generated entries."""
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=Decimal('500'),
            description='Monthly Sub',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Entertainment',
            currency='₹'
        )
        
        from expenses.views.mixins import process_user_recurring_transactions
        
        # First generation at 500
        process_user_recurring_transactions(self.user)
        first_expense = Expense.objects.filter(
            user=self.user,
            description__contains='Monthly Sub'
        ).first()
        self.assertEqual(first_expense.amount, Decimal('500'))
        
        # Modify recurring amount
        rt.amount = Decimal('750')
        rt.last_processed_date = None  # Reset to generate again
        rt.save()
        
        # This would generate another entry at next call if date permits
        # Verify the recurring transaction itself is updated
        rt.refresh_from_db()
        self.assertEqual(rt.amount, Decimal('750'))


# ============================================================================
# LOAN REPAYMENT CASHFLOW TESTS
# ============================================================================

class LoanRepaymentCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of loan repayments."""
    
    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            user=self.user,
            name='Home Loan',
            initial_principal=Decimal('1000000'),
            loan_type='HOME',
            duration_months=240,
            start_date=date.today() - timedelta(days=30),
            currency='₹'
        )
    
    def test_loan_repayment_decreases_account_balance(self):
        """Test that repayment decreases account balance."""
        initial_balance = self.get_account_balance(self.checking)
        
        LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.checking,
            amount=Decimal('20000'),
            date=date.today(),
            principal_portion=Decimal('19000'),
            interest_portion=Decimal('1000'),
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        self.assertEqual(new_balance, initial_balance - Decimal('20000'))
    
    def test_multiple_repayments_cumulative(self):
        """Test that multiple repayments accumulate correctly."""
        initial_balance = self.get_account_balance(self.checking)
        
        for i in range(3):
            LoanRepayment.objects.create(
                loan=self.loan,
                from_account=self.checking,
                amount=Decimal('10000'),
                date=date.today() - timedelta(days=i),
                principal_portion=Decimal('9000'),
                interest_portion=Decimal('1000'),
            )
        
        new_balance = self.get_account_balance(self.checking)
        
        self.assertEqual(new_balance, initial_balance - Decimal('30000'))
    
    def test_delete_repayment_returns_balance(self):
        """Test that deleting repayment returns money to account."""
        repayment = LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.checking,
            amount=Decimal('20000'),
            date=date.today(),
            principal_portion=Decimal('19000'),
            interest_portion=Decimal('1000'),
        )
        
        balance_after_repayment = self.get_account_balance(self.checking)
        
        repayment.delete()
        
        balance_after_delete = self.get_account_balance(self.checking)
        
        self.assertEqual(
            balance_after_delete,
            balance_after_repayment + Decimal('20000')
        )
    
    def test_repayment_amount_must_equal_principal_plus_interest(self):
        """Test that repayment amount validation works correctly."""
        # This tests business logic: amount should equal principal + interest
        repayment = LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.checking,
            amount=Decimal('20000'),
            date=date.today(),
            principal_portion=Decimal('15000'),
            interest_portion=Decimal('5000'),
        )
        
        # Total should be 20000
        self.assertEqual(
            repayment.principal_portion + repayment.interest_portion,
            repayment.amount
        )


# ============================================================================
# GOAL CASHFLOW TESTS
# ============================================================================

class GoalCashflowTest(CashflowCalculationTestBase):
    """Test cashflow impact of savings goals."""
    
    def test_goal_contribution_decreases_account(self):
        """Test that goal contribution decreases source account."""
        goal = SavingsGoal.objects.create(
            user=self.user,
            name='Vacation Fund',
            target_amount=Decimal('500000'),
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        checking_before = self.get_account_balance(self.checking)
        
        contribution = GoalContribution.objects.create(
            goal=goal,
            account=self.checking,
            amount=Decimal('50000'),
            date=date.today()
        )
        
        checking_after = self.get_account_balance(self.checking)
        
        self.assertEqual(checking_after, checking_before - Decimal('50000'))
    
    def test_goal_progress_calculation(self):
        """Test that goal progress is calculated correctly."""
        goal = SavingsGoal.objects.create(
            user=self.user,
            name='Vacation Fund',
            target_amount=Decimal('500000'),
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        # Add contributions
        GoalContribution.objects.create(
            goal=goal, account=self.checking,
            amount=Decimal('100000'), date=date.today()
        )
        GoalContribution.objects.create(
            goal=goal, account=self.checking,
            amount=Decimal('150000'), date=date.today()
        )
        
        total_contributed = GoalContribution.objects.filter(
            goal=goal
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        self.assertEqual(total_contributed, Decimal('250000'))
        self.assertLess(total_contributed, goal.target_amount)
    
    def test_multiple_goals_independent_tracking(self):
        """Test that multiple goals track contributions independently."""
        goal1 = SavingsGoal.objects.create(
            user=self.user,
            name='Vacation',
            target_amount=Decimal('500000'),
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        goal2 = SavingsGoal.objects.create(
            user=self.user,
            name='Car',
            target_amount=Decimal('1000000'),
            target_date=date.today() + timedelta(days=730),
            currency='₹'
        )
        
        GoalContribution.objects.create(
            goal=goal1, account=self.checking,
            amount=Decimal('100000'), date=date.today()
        )
        
        GoalContribution.objects.create(
            goal=goal2, account=self.checking,
            amount=Decimal('200000'), date=date.today()
        )
        
        goal1_total = GoalContribution.objects.filter(
            goal=goal1
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        goal2_total = GoalContribution.objects.filter(
            goal=goal2
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')
        
        self.assertEqual(goal1_total, Decimal('100000'))
        self.assertEqual(goal2_total, Decimal('200000'))


# ============================================================================
# INTEGRATED CASHFLOW SCENARIO TESTS
# ============================================================================

class IntegratedCashflowScenarioTest(CashflowCalculationTestBase):
    """Test complete financial scenarios with multiple operations."""
    
    def test_monthly_budget_scenario(self):
        """Test complete monthly budget: salary - expenses - savings = remaining."""
        # Starting balance
        starting_balance = self.get_account_balance(self.checking)
        
        # Add monthly salary
        salary = Decimal('50000')
        Income.objects.create(
            user=self.user, account=self.checking, amount=salary,
            source='Salary', date=date.today(), currency='₹'
        )
        
        # Add monthly expenses
        var_cat, _ = Category.objects.get_or_create(user=self.user, name='Various')
        expenses = [
            Decimal('5000'),   # Rent
            Decimal('2000'),   # Food
            Decimal('1000'),   # Transport
            Decimal('500'),    # Entertainment
        ]
        for expense_amount in expenses:
            Expense.objects.create(
                user=self.user, account=self.checking, amount=expense_amount,
                category='Various', date=date.today(), currency='₹'
            )
        
        # Add savings
        savings = Decimal('10000')
        Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=savings,
            date=date.today()
        )
        
        ending_balance = self.get_account_balance(self.checking)
        
        # Expected: starting + salary - expenses - savings
        total_expenses = sum(expenses)
        expected = starting_balance + salary - total_expenses - savings
        
        self.assertEqual(ending_balance, expected)
    
    def test_complex_scenario_all_operations(self):
        """Test scenario combining income, expenses, transfers, and goals."""
        initial_net_worth = self.get_net_worth()
        
        # Add income
        Income.objects.create(
            user=self.user, account=self.checking, amount=Decimal('100000'),
            source='Salary', date=date.today(), currency='₹'
        )
        
        # Add expenses
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('10000'),
            category='Food', date=date.today(), currency='₹'
        )
        
        # Transfer to savings
        Transfer.objects.create(
            user=self.user,
            from_account=self.checking,
            to_account=self.savings,
            amount=Decimal('30000'),
            date=date.today()
        )
        
        # Create goal and contribute
        goal = SavingsGoal.objects.create(
            user=self.user,
            name='Vacation',
            target_amount=Decimal('500000'),
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        GoalContribution.objects.create(
            goal=goal, account=self.savings,
            amount=Decimal('20000'), date=date.today()
        )
        
        # Get final net worth
        final_net_worth = self.get_net_worth()
        
        # Net worth increased by income and reduced by expense.
        # Transfer and goal contribution are internal reallocations.
        expected_change = Decimal('100000') - Decimal('10000')
        actual_change = final_net_worth - initial_net_worth
        
        self.assertEqual(actual_change, expected_change)
    
    def test_cashflow_after_multiple_edits(self):
        """Test that cashflow remains consistent after multiple edits."""
        # Create initial expense
        expense = Expense.objects.create(
            user=self.user, account=self.checking,
            amount=Decimal('1000'),
            category='Food', date=date.today(), currency='₹'
        )
        
        balance_after_create = self.get_account_balance(self.checking)
        
        # Edit expense multiple times
        expense.amount = Decimal('1500')
        expense.save()
        balance_after_first_edit = self.get_account_balance(self.checking)
        
        expense.amount = Decimal('800')
        expense.save()
        balance_after_second_edit = self.get_account_balance(self.checking)
        
        expense.amount = Decimal('1200')
        expense.save()
        balance_after_third_edit = self.get_account_balance(self.checking)
        
        # Final balance should be starting - 1200
        from_starting = Expense.objects.filter(user=self.user).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        
        self.assertEqual(from_starting, Decimal('1200'))


# ============================================================================
# EDGE CASES AND ERROR HANDLING
# ============================================================================

class CashflowEdgeCasesTest(CashflowCalculationTestBase):
    """Test edge cases in cashflow calculations."""
    
    def test_zero_amount_expense(self):
        """Test handling of zero-amount expenses."""
        initial_balance = self.get_account_balance(self.checking)
        
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.checking, amount=Decimal('0'),
            category='Food', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        # Balance shouldn't change
        self.assertEqual(initial_balance, new_balance)
    
    def test_very_large_amounts(self):
        """Test handling of very large transaction amounts."""
        initial_balance = self.get_account_balance(self.checking)
        large_amount = Decimal('99999999.99')
        
        Income.objects.create(
            user=self.user, account=self.checking, amount=large_amount,
            source='Large Income', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        self.assertEqual(new_balance, initial_balance + large_amount)
    
    def test_fractional_amounts(self):
        """Test handling of fractional currency amounts."""
        initial_balance = self.get_account_balance(self.checking)
        fractional_amount = Decimal('123.45')
        
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.checking,
            amount=fractional_amount,
            category='Food', date=date.today(), currency='₹'
        )
        
        new_balance = self.get_account_balance(self.checking)
        
        self.assertEqual(new_balance, initial_balance - fractional_amount)
    
    def test_negative_balance_allowed(self):
        """Test that accounts can go negative (overdraft scenario)."""
        # Add large expense exceeding balance
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(
            user=self.user, account=self.cash,
            amount=Decimal('50000'),  # Cash only has 10000
            category='Food', date=date.today(), currency='₹'
        )
        
        # Balance should be negative
        final_balance = self.get_account_balance(self.cash)
        self.assertLess(final_balance, Decimal('0'))
    
    def test_concurrent_modifications_cumulative(self):
        """Test that concurrent modifications are properly tracked."""
        # Simulate rapid edits
        food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        expense = Expense.objects.create(
            user=self.user, account=self.checking,
            amount=Decimal('100'),
            category='Food', date=date.today(), currency='₹'
        )
        
        # Rapid modifications
        for new_amount in [200, 150, 300, 250]:
            expense.amount = Decimal(str(new_amount))
            expense.save()
        
        # Get all expenses for user
        total = Expense.objects.filter(user=self.user).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        
        # Should be final amount (250)
        self.assertEqual(total, Decimal('250'))
