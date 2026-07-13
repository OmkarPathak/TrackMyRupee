from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.models import (
    Account,
    CapitalEvent,
    Expense,
    GoalContribution,
    Income,
    JournalEntry,
    Loan,
    LoanRepayment,
    SavingsGoal,
    Transfer,
)


@override_settings(LEDGER_WRITE_ENABLED=True)
class LedgerWriteCoverageTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_ledger_write', password='password')
        self.account = Account.objects.create(user=self.user, name='Test Bank', account_type='BANK', balance=Decimal('1000.00'))
        self.account2 = Account.objects.create(user=self.user, name='Test Wallet', account_type='CASH', balance=Decimal('500.00'))
        
        # Clear any ledger entries that might have been created by signals/defaults
        JournalEntry.objects.all().delete()

    def test_expense_posts_to_ledger(self):
        obj = Expense.objects.create(
            user=self.user, date=date.today(), amount=Decimal('50.00'), 
            description='Test', category='Food', account=self.account
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='EXPENSE', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='EXPENSE', source_id=obj_id, status='POSTED').count(), 2) # Create + Delete (reversal)
        
    def test_income_posts_to_ledger(self):
        obj = Income.objects.create(
            user=self.user, date=date.today(), amount=Decimal('100.00'), 
            source='Salary', account=self.account
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='INCOME', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='INCOME', source_id=obj_id, status='POSTED').count(), 2)

    def test_transfer_posts_to_ledger(self):
        obj = Transfer.objects.create(
            user=self.user, from_account=self.account, to_account=self.account2, 
            amount=Decimal('100.00'), date=date.today()
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='TRANSFER', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='TRANSFER', source_id=obj_id, status='POSTED').count(), 2)

    def test_goal_contribution_posts_to_ledger(self):
        goal = SavingsGoal.objects.create(user=self.user, name='Car', target_amount=Decimal('1000.00'))
        obj = GoalContribution.objects.create(
            goal=goal, account=self.account, amount=Decimal('100.00'), date=date.today()
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='GOAL_CONTRIBUTION', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='GOAL_CONTRIBUTION', source_id=obj_id, status='POSTED').count(), 2)

    def test_loan_repayment_posts_to_ledger(self):
        loan = Loan.objects.create(user=self.user, name='Home Loan', initial_principal=Decimal('50000.00'), duration_months=120)
        obj = LoanRepayment.objects.create(
            loan=loan, from_account=self.account, amount=Decimal('500.00'), 
            principal_portion=Decimal('200.00'), interest_portion=Decimal('300.00'), date=date.today()
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='LOAN_REPAYMENT', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='LOAN_REPAYMENT', source_id=obj_id, status='POSTED').count(), 2)

    def test_capital_event_posts_to_ledger(self):
        obj = CapitalEvent.objects.create(
            user=self.user, date=date.today(), amount=Decimal('2000.00'), 
            subtype='other', account=self.account
        )
        self.assertTrue(JournalEntry.objects.filter(source_type='CAPITAL_EVENT', source_id=obj.id, status='POSTED').exists())
        
        obj_id = obj.id
        obj.delete()
        self.assertEqual(JournalEntry.objects.filter(source_type='CAPITAL_EVENT', source_id=obj_id, status='POSTED').count(), 2)
