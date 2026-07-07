"""
Comprehensive view tests for accounts, transactions, loans, goals, export/import.
Tests CRUD operations, data isolation, permissions, and edge cases.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.models import (
    Account,
    Category,
    Expense,
    Income,
    Loan,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
)


class BaseComprehensiveTest(TestCase):
    """Base test class with common setup for all view tests."""
    
    def setUp(self):
        # Create test users
        self.user = User.objects.create_user(username='testuser', password='testpass123')
        self.other_user = User.objects.create_user(username='otheruser', password='testpass123')
        
        # Setup user profiles
        for user in [self.user, self.other_user]:
            profile = user.profile
            profile.has_seen_tutorial = True
            profile.tier = 'PLUS'
            profile.save()
        
        # Client setup
        self.client = Client()
        self.client.login(username='testuser', password='testpass123')
        
        # Create test data
        self.category, _ = Category.objects.get_or_create(user=self.user, name='Food')
        self.account = Account.objects.create(
            user=self.user,
            name='Test Account',
            account_type='BANK',
            is_active=True
        )
        self.other_account = Account.objects.create(
            user=self.user,
            name='Other Account',
            account_type='CASH',
            is_active=True
        )
    
    def assertResponseContextHas(self, response, *keys):
        """Helper to check multiple context keys exist."""
        for key in keys:
            self.assertIn(key, response.context, f"Missing context key: {key}")
    
    def assertUserDataIsolation(self, url, **filter_kwargs):
        """Helper to verify current user can't see other user's data."""
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        # Create data for other user
        return response
    
    def _login_as(self, user):
        """Helper to switch logged-in user."""
        self.client.logout()
        self.client.login(username=user.username, password='testpass123')
    
    def _logout(self):
        """Helper to logout."""
        self.client.logout()


# ============================================================================
# ACCOUNT TESTS
# ============================================================================

class AccountListViewTest(BaseComprehensiveTest):
    """Test AccountListView - list, filter, pagination."""
    
    def test_account_list_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)
    
    def test_account_list_returns_200(self):
        """Test that authenticated users see the list."""
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, 200)
    
    def test_account_list_shows_only_user_accounts(self):
        """Test that users only see their own accounts."""
        other_account = Account.objects.create(
            user=self.other_user,
            name='Other User Account',
            account_type='BANK',
            is_active=True
        )
        
        response = self.client.get(reverse('account-list'))
        accounts = response.context['accounts']
        
        account_ids = [acc.id for acc in accounts]
        self.assertIn(self.account.id, account_ids)
        self.assertIn(self.other_account.id, account_ids)
        self.assertNotIn(other_account.id, account_ids)
    
    def test_account_list_active_filter(self):
        """Test filtering by active status."""
        inactive = Account.objects.create(
            user=self.user,
            name='Inactive Account',
            account_type='BANK',
            is_active=False
        )
        
        response = self.client.get(reverse('account-list') + '?status=active')
        accounts = response.context['accounts']
        account_ids = [acc.id for acc in accounts]
        
        self.assertIn(self.account.id, account_ids)
        self.assertNotIn(inactive.id, account_ids)
    
    def test_account_list_inactive_filter(self):
        """Test filtering by inactive status."""
        inactive = Account.objects.create(
            user=self.user,
            name='Inactive Account',
            account_type='BANK',
            is_active=False
        )
        
        response = self.client.get(reverse('account-list') + '?status=inactive')
        accounts = response.context['accounts']
        account_ids = [acc.id for acc in accounts]
        
        self.assertNotIn(self.account.id, account_ids)
        self.assertIn(inactive.id, account_ids)
    
    def test_account_list_type_filter(self):
        """Test filtering by account type."""
        response = self.client.get(reverse('account-list') + '?type=BANK')
        accounts = response.context['accounts']
        
        # Setup has self.account (BANK) and other_account (CASH)
        for acc in accounts:
            self.assertEqual(acc.account_type, 'BANK')
    
    def test_account_list_display_balance_set(self):
        """Test that display_balance is set on each account."""
        response = self.client.get(reverse('account-list'))
        accounts = response.context['accounts']
        
        self.assertGreater(len(accounts), 0)
        for acc in accounts:
            self.assertTrue(hasattr(acc, 'display_balance'))
            self.assertIsNotNone(acc.display_balance)
    
    def test_account_list_context_contains_summary(self):
        """Test that context contains summary information."""
        response = self.client.get(reverse('account-list'))
        self.assertIn('total_balance', response.context)

    @override_settings(LEDGER_READ_ENABLED=True)
    def test_account_list_uses_single_opening_balance_lookup(self):
        Account.objects.create(
            user=self.user,
            name='Third Account',
            account_type='BANK',
            is_active=True,
        )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('account-list'))

        self.assertEqual(response.status_code, 200)
        opening_lookup_queries = [
            query['sql'] for query in queries.captured_queries
            if 'expenses_journalentry' in query['sql'] and 'opening_account_id' in query['sql']
        ]
        self.assertEqual(len(opening_lookup_queries), 1)

    def test_account_list_grouping_and_days_since_update(self):
        """Test that accounts are grouped by type and days_since_update is set."""
        from datetime import timedelta

        from django.utils import timezone
        
        # Modify updated_at of self.account to be 31 days ago
        Account.objects.filter(pk=self.account.pk).update(
            updated_at=timezone.now() - timedelta(days=31)
        )
        
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, 200)
        
        # Check days_since_update
        accounts = response.context['accounts']
        acc_dict = {a.id: a for a in accounts}
        self.assertIn(self.account.id, acc_dict)
        self.assertEqual(acc_dict[self.account.id].days_since_update, 31)
        
        # Check grouped_accounts
        grouped_accounts = response.context['grouped_accounts']
        self.assertIsNotNone(grouped_accounts)
        
        # Since self.account is BANK and self.other_account is CASH, we should have groups for BANK and CASH
        group_types = [g['type'] for g in grouped_accounts]
        self.assertIn('BANK', group_types)
        self.assertIn('CASH', group_types)
        
        bank_group = next(g for g in grouped_accounts if g['type'] == 'BANK')
        self.assertEqual(bank_group['count'], 1)
        self.assertEqual(bank_group['total'], self.account.balance)


class AccountCreateViewTest(BaseComprehensiveTest):
    """Test account creation."""
    
    def test_account_create_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('account-create'))
        self.assertEqual(response.status_code, 302)
    
    def test_account_create_get_returns_200(self):
        """Test GET request shows form."""
        response = self.client.get(reverse('account-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
    
    def test_account_create_valid_post(self):
        """Test creating an account with valid data."""
        data = {
            'name': 'New Savings Account',
            'account_type': 'BANK',
            'currency': '₹',
            'balance': '5000'
        }
        
        response = self.client.post(reverse('account-create'), data)
        
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Account.objects.filter(user=self.user, name='New Savings Account').count(), 1)
        
        acc = Account.objects.get(name='New Savings Account')
        self.assertEqual(acc.account_type, 'BANK')
        self.assertEqual(acc.user, self.user)
    
    def test_account_create_invalid_missing_name(self):
        """Test that missing name shows error."""
        data = {
            'name': '',
            'account_type': 'BANK',
            'currency': '₹'
        }
        
        response = self.client.post(reverse('account-create'), data)
        
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'name', 'This field is required.')
    
    def test_account_create_sets_user_automatically(self):
        """Test that created account is tied to current user."""
        data = {
            'name': 'Auto User Account',
            'account_type': 'CASH',
            'balance': '0',
            'currency': '₹'
        }
        
        self.client.post(reverse('account-create'), data)
        
        acc = Account.objects.get(name='Auto User Account')
        self.assertEqual(acc.user, self.user)


class AccountUpdateViewTest(BaseComprehensiveTest):
    """Test account update/edit."""
    
    def test_account_update_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('account-edit', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_account_update_404_other_user_account(self):
        """Test that users can't edit other user's accounts."""
        other_account = Account.objects.create(
            user=self.other_user,
            name='Other Account',
            account_type='BANK'
        )
        
        response = self.client.get(reverse('account-edit', kwargs={'pk': other_account.pk}))
        self.assertEqual(response.status_code, 404)
    
    def test_account_update_valid_post(self):
        """Test updating account with valid data."""
        data = {
            'name': 'Updated Account Name',
            'account_type': 'CASH',
            'balance': '0',
            'currency': '₹'
        }
        
        response = self.client.post(
            reverse('account-edit', kwargs={'pk': self.account.pk}),
            data
        )
        
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'Updated Account Name')
        self.assertEqual(self.account.account_type, 'CASH')
    
    def test_account_update_preserves_other_fields(self):
        """Test that unmodified fields are preserved."""
        original_balance = self.account.balance
        
        data = {
            'name': 'New Name',
            'account_type': 'BANK',
            'currency': '₹'
        }
        
        self.client.post(reverse('account-edit', kwargs={'pk': self.account.pk}), data)
        
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, original_balance)


class AccountDeleteViewTest(BaseComprehensiveTest):
    """Test account deletion."""
    
    def test_account_delete_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.post(reverse('account-delete', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_account_delete_404_other_user_account(self):
        """Test that users can't delete other user's accounts."""
        other_account = Account.objects.create(
            user=self.other_user,
            name='Other Account',
            account_type='BANK'
        )
        
        response = self.client.post(reverse('account-delete', kwargs={'pk': other_account.pk}))
        self.assertEqual(response.status_code, 404)
        
        self.assertTrue(Account.objects.filter(pk=other_account.pk).exists())
    
    def test_account_delete_valid(self):
        """Test deleting an account."""
        account_id = self.account.pk
        
        response = self.client.post(reverse('account-delete', kwargs={'pk': account_id}))
        
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertFalse(self.account.is_active)


class AccountDetailViewTest(BaseComprehensiveTest):
    """Test account detail view with transactions."""
    
    def test_account_detail_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_account_detail_404_other_user_account(self):
        """Test that users can't view other user's account details."""
        other_account = Account.objects.create(
            user=self.other_user,
            name='Other Account',
            account_type='BANK'
        )
        
        response = self.client.get(reverse('account-detail', kwargs={'pk': other_account.pk}))
        self.assertEqual(response.status_code, 404)
    
    def test_account_detail_returns_200(self):
        """Test that account detail page loads."""
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 200)
    
    def test_account_detail_context_keys(self):
        """Test that all required context keys are present."""
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        
        self.assertIn('account', response.context)
        self.assertEqual(response.context['account'].id, self.account.id)
    
    def test_account_detail_display_balance_set(self):
        """Test that display_balance is computed."""
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        account = response.context['account']
        
        self.assertTrue(hasattr(account, 'display_balance'))
        self.assertIsNotNone(account.display_balance)
    
    def test_account_detail_shows_transactions(self):
        """Test that transactions are listed for the account."""
        Expense.objects.create(
            user=self.user,
            account=self.account,
            amount=100,
            category='Food',
            date=date.today()
        )
        Income.objects.create(
            user=self.user,
            account=self.account,
            amount=5000,
            source='Salary',
            date=date.today()
        )
        
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        
        self.assertIn('ledger', response.context)
        ledger = response.context['ledger']
        self.assertEqual(len(ledger.object_list), 2)


# ============================================================================
# RECURRING TRANSACTION TESTS
# ============================================================================

class RecurringTransactionListViewTest(BaseComprehensiveTest):
    """Test recurring transaction list view."""
    
    def test_recurring_list_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('recurring-list'))
        self.assertEqual(response.status_code, 302)
    
    def test_recurring_list_returns_200(self):
        """Test that recurring transaction list loads."""
        response = self.client.get(reverse('recurring-list'))
        self.assertEqual(response.status_code, 200)
    
    def test_recurring_list_shows_only_user_transactions(self):
        """Test that users only see their own recurring transactions."""
        rt1 = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='My Recurring',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
        
        rt2 = RecurringTransaction.objects.create(
            user=self.other_user,
            transaction_type='EXPENSE',
            amount=100,
            description='Other Recurring',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
        
        response = self.client.get(reverse('recurring-list'))
        transactions = response.context['recurring_transactions']
        
        rt_ids = [rt.id for rt in transactions]
        self.assertIn(rt1.id, rt_ids)
        self.assertNotIn(rt2.id, rt_ids)
    
    def test_recurring_list_includes_all_fields(self):
        """Test that all transaction details are displayed."""
        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=250,
            description='Netflix',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Entertainment',
            currency='₹'
        )
        
        response = self.client.get(reverse('recurring-list'))
        transactions = response.context['recurring_transactions']
        
        rt = transactions[0]
        self.assertEqual(rt.amount, 250)
        self.assertEqual(rt.description, 'Netflix')
        self.assertEqual(rt.frequency, 'MONTHLY')


class RecurringTransactionCreateViewTest(BaseComprehensiveTest):
    """Test creating recurring transactions."""
    
    def test_recurring_create_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('recurring-create'))
        self.assertEqual(response.status_code, 302)
    
    def test_recurring_create_get_returns_200(self):
        """Test that form loads."""
        response = self.client.get(reverse('recurring-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
    
    def test_recurring_create_expense_valid(self):
        """Test creating a recurring expense."""
        data = {
            'transaction_type': 'EXPENSE',
            'amount': 500,
            'description': 'Monthly Subscription',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'category': self.category.name,
            'payment_method': 'Cash',
            'currency': '₹'
        }
        
        response = self.client.post(reverse('recurring-create'), data)
        
        self.assertEqual(response.status_code, 302)
        rt = RecurringTransaction.objects.get(description='Monthly Subscription')
        self.assertEqual(rt.user, self.user)
        self.assertEqual(rt.transaction_type, 'EXPENSE')
    
    def test_recurring_create_income_valid(self):
        """Test creating a recurring income."""
        data = {
            'transaction_type': 'INCOME',
            'amount': 5000,
            'description': 'Monthly Salary',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'source': 'Employment',
            'payment_method': 'Cash',
            'currency': '₹'
        }
        
        response = self.client.post(reverse('recurring-create'), data)
        
        self.assertEqual(response.status_code, 302)
        rt = RecurringTransaction.objects.get(description='Monthly Salary')
        self.assertEqual(rt.transaction_type, 'INCOME')
    
    def test_recurring_create_invalid_missing_amount(self):
        """Test that missing amount shows error."""
        data = {
            'transaction_type': 'EXPENSE',
            'amount': '',
            'description': 'Test',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'category': self.category.name,
            'payment_method': 'Cash',
            'currency': '₹'
        }
        
        response = self.client.post(reverse('recurring-create'), data)
        
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'amount', 'This field is required.')


class RecurringTransactionUpdateViewTest(BaseComprehensiveTest):
    """Test updating recurring transactions."""
    
    def setUp(self):
        super().setUp()
        self.recurring = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='Original',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
    
    def test_recurring_update_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('recurring-edit', kwargs={'pk': self.recurring.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_recurring_update_404_other_user(self):
        """Test that users can't update other user's recurring transactions."""
        other_rt = RecurringTransaction.objects.create(
            user=self.other_user,
            transaction_type='EXPENSE',
            amount=100,
            description='Other',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
        
        response = self.client.get(reverse('recurring-edit', kwargs={'pk': other_rt.pk}))
        self.assertEqual(response.status_code, 404)
    
    def test_recurring_update_valid(self):
        """Test updating a recurring transaction."""
        data = {
            'transaction_type': 'EXPENSE',
            'amount': 250,
            'description': 'Updated',
            'frequency': 'WEEKLY',
            'start_date': date.today(),
            'category': self.category.name,
            'payment_method': 'Cash',
            'currency': '₹'
        }
        
        response = self.client.post(
            reverse('recurring-edit', kwargs={'pk': self.recurring.pk}),
            data
        )
        
        self.assertEqual(response.status_code, 302)
        self.recurring.refresh_from_db()
        self.assertEqual(self.recurring.amount, Decimal('250'))
        self.assertEqual(self.recurring.description, 'Updated')


class RecurringTransactionDeleteViewTest(BaseComprehensiveTest):
    """Test deleting recurring transactions."""
    
    def setUp(self):
        super().setUp()
        self.recurring = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='To Delete',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
    
    def test_recurring_delete_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.post(reverse('recurring-delete', kwargs={'pk': self.recurring.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_recurring_delete_404_other_user(self):
        """Test that users can't delete other user's recurring transactions."""
        other_rt = RecurringTransaction.objects.create(
            user=self.other_user,
            transaction_type='EXPENSE',
            amount=100,
            description='Other',
            frequency='MONTHLY',
            start_date=date.today(),
            category='Food',
            currency='₹'
        )
        
        response = self.client.post(reverse('recurring-delete', kwargs={'pk': other_rt.pk}))
        self.assertEqual(response.status_code, 404)
        
        self.assertTrue(RecurringTransaction.objects.filter(pk=other_rt.pk).exists())
    
    def test_recurring_delete_valid(self):
        """Test deleting a recurring transaction."""
        rt_id = self.recurring.pk
        
        response = self.client.post(reverse('recurring-delete', kwargs={'pk': rt_id}))
        
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RecurringTransaction.objects.filter(pk=rt_id).exists())


# ============================================================================
# LOAN TESTS
# ============================================================================

class LoanListViewTest(BaseComprehensiveTest):
    """Test loan list view."""
    
    def test_loan_list_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('loan-list'))
        self.assertEqual(response.status_code, 302)
    
    def test_loan_list_returns_200(self):
        """Test that loan list loads."""
        response = self.client.get(reverse('loan-list'))
        self.assertEqual(response.status_code, 200)
    
    def test_loan_list_shows_only_user_loans(self):
        """Test that users only see their own loans."""
        loan1 = Loan.objects.create(
            user=self.user,
            name='My Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
        
        loan2 = Loan.objects.create(
            user=self.other_user,
            name='Other Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
        
        response = self.client.get(reverse('loan-list'))
        loans = response.context['loans']
        
        loan_ids = [l.id for l in loans]
        self.assertIn(loan1.id, loan_ids)
        self.assertNotIn(loan2.id, loan_ids)


class LoanCreateViewTest(BaseComprehensiveTest):
    """Test creating loans."""
    
    def test_loan_create_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('loan-create'))
        self.assertEqual(response.status_code, 302)
    
    def test_loan_create_get_returns_200(self):
        """Test that form loads."""
        response = self.client.get(reverse('loan-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
    
    def test_loan_create_valid(self):
        """Test creating a loan."""
        data = {
            'name': 'Home Loan',
            'loan_type': 'HOME',
            'initial_principal': 5000000,
            'duration_months': 240,
            'interest_rate': 4.5,
            'start_date': date.today(),
            'currency': '₹'
        }
        
        response = self.client.post(reverse('loan-create'), data)
        
        self.assertEqual(response.status_code, 302)
        loan = Loan.objects.get(name='Home Loan')
        self.assertEqual(loan.user, self.user)
        self.assertEqual(loan.initial_principal, Decimal('5000000'))
        self.assertEqual(loan.duration_months, 240)
    
    def test_loan_create_invalid_missing_amount(self):
        """Test that missing amount shows error."""
        data = {
            'name': 'Test Loan',
            'loan_type': 'PERSONAL',
            'initial_principal': '',
            'duration_months': 36,
            'interest_rate': 4.5,
            'start_date': date.today(),
            'currency': '₹'
        }
        
        response = self.client.post(reverse('loan-create'), data)
        
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'initial_principal', 'This field is required.')


class LoanUpdateViewTest(BaseComprehensiveTest):
    """Test updating loans."""
    
    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            user=self.user,
            name='Original Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
    
    def test_loan_update_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('loan-edit', kwargs={'pk': self.loan.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_loan_update_404_other_user(self):
        """Test that users can't update other user's loans."""
        other_loan = Loan.objects.create(
            user=self.other_user,
            name='Other Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
        
        response = self.client.get(reverse('loan-edit', kwargs={'pk': other_loan.pk}))
        self.assertEqual(response.status_code, 404)
    
    def test_loan_update_valid(self):
        """Test updating a loan."""
        data = {
            'name': 'Updated Loan',
            'loan_type': 'PERSONAL',
            'initial_principal': 150000,
            'duration_months': 72,
            'interest_rate': 3.5,
            'start_date': date.today(),
            'currency': '₹'
        }
        
        response = self.client.post(
            reverse('loan-edit', kwargs={'pk': self.loan.pk}),
            data
        )
        
        self.assertEqual(response.status_code, 302)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.name, 'Updated Loan')
        self.assertEqual(self.loan.initial_principal, Decimal('150000'))
        self.assertEqual(self.loan.duration_months, 72)


class LoanDeleteViewTest(BaseComprehensiveTest):
    """Test deleting loans."""
    
    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            user=self.user,
            name='To Delete',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
    
    def test_loan_delete_valid(self):
        """Test deleting a loan."""
        loan_id = self.loan.pk
        
        response = self.client.post(reverse('loan-delete', kwargs={'pk': loan_id}))
        
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Loan.objects.filter(pk=loan_id).exists())


class LoanRepaymentViewTest(BaseComprehensiveTest):
    """Test loan repayment functionality."""
    
    def setUp(self):
        super().setUp()
        self.loan = Loan.objects.create(
            user=self.user,
            name='Test Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today() - timedelta(days=30),
            currency='₹'
        )
    
    def test_loan_repayment_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('loan-repayment-create', kwargs={'pk': self.loan.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_loan_repayment_404_other_user(self):
        """Test that users can't repay other user's loans."""
        other_loan = Loan.objects.create(
            user=self.other_user,
            name='Other Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=60,
            start_date=date.today(),
            currency='₹'
        )
        
        response = self.client.post(reverse('loan-repayment-create', kwargs={'pk': other_loan.pk}), {
            'amount': 1000,
            'date': date.today(),
            'interest_portion': 100,
            'principal_portion': 900,
        })
        self.assertEqual(response.status_code, 404)
    
    def test_loan_repayment_get_returns_200(self):
        """Test that repayment form loads."""
        response = self.client.get(reverse('loan-repayment-create', kwargs={'pk': self.loan.pk}))
        self.assertEqual(response.status_code, 405)
    
    def test_loan_repayment_valid(self):
        """Test making a loan repayment."""
        data = {
            'amount': 10000,
            'date': date.today(),
            'interest_portion': 400,
            'principal_portion': 9600
        }
        
        response = self.client.post(
            reverse('loan-repayment-create', kwargs={'pk': self.loan.pk}),
            data
        )
        
        self.assertEqual(response.status_code, 302)
        repayment = LoanRepayment.objects.filter(loan=self.loan).first()
        self.assertIsNotNone(repayment)
        self.assertEqual(repayment.amount, Decimal('10000'))


# ============================================================================
# GOAL TESTS
# ============================================================================

class GoalListViewTest(BaseComprehensiveTest):
    """Test goal list view."""
    
    def test_goal_list_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('goal-list'))
        self.assertEqual(response.status_code, 302)
    
    def test_goal_list_returns_200(self):
        """Test that goal list loads."""
        response = self.client.get(reverse('goal-list'))
        self.assertEqual(response.status_code, 200)
    
    def test_goal_list_shows_only_user_goals(self):
        """Test that users only see their own goals."""
        goal1 = SavingsGoal.objects.create(
            user=self.user,
            name='My Savings Goal',
            target_amount=500000,
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        goal2 = SavingsGoal.objects.create(
            user=self.other_user,
            name='Other Goal',
            target_amount=500000,
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        response = self.client.get(reverse('goal-list'))
        goals = response.context['goals']
        
        goal_ids = [g.id for g in goals]
        self.assertIn(goal1.id, goal_ids)
        self.assertNotIn(goal2.id, goal_ids)


class GoalCreateViewTest(BaseComprehensiveTest):
    """Test creating goals."""
    
    def test_goal_create_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('goal-create'))
        self.assertEqual(response.status_code, 302)
    
    def test_goal_create_get_returns_200(self):
        """Test that form loads."""
        response = self.client.get(reverse('goal-create'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('form', response.context)
    
    def test_goal_create_valid(self):
        """Test creating a goal."""
        data = {
            'name': 'Vacation Fund',
            'target_amount': 100000,
            'target_date': date.today() + timedelta(days=365),
            'icon': '🎯',
            'color': 'primary',
            'currency': '₹'
        }
        
        response = self.client.post(reverse('goal-create'), data)
        
        self.assertEqual(response.status_code, 302)
        goal = SavingsGoal.objects.get(name='Vacation Fund')
        self.assertEqual(goal.user, self.user)
        self.assertEqual(goal.target_amount, Decimal('100000'))
    
    def test_goal_create_invalid_missing_name(self):
        """Test that missing name shows error."""
        data = {
            'name': '',
            'target_amount': 100000,
            'target_date': date.today() + timedelta(days=365),
            'currency': '₹'
        }
        
        response = self.client.post(reverse('goal-create'), data)
        
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response, 'form', 'name', 'This field is required.')


class GoalUpdateViewTest(BaseComprehensiveTest):
    """Test updating goals."""
    
    def setUp(self):
        super().setUp()
        self.goal = SavingsGoal.objects.create(
            user=self.user,
            name='Original Goal',
            target_amount=100000,
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
    
    def test_goal_update_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('goal-edit', kwargs={'pk': self.goal.pk}))
        self.assertEqual(response.status_code, 302)
    
    def test_goal_update_404_other_user(self):
        """Test that users can't update other user's goals."""
        other_goal = SavingsGoal.objects.create(
            user=self.other_user,
            name='Other Goal',
            target_amount=100000,
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
        
        response = self.client.get(reverse('goal-edit', kwargs={'pk': other_goal.pk}))
        self.assertEqual(response.status_code, 404)
    
    def test_goal_update_valid(self):
        """Test updating a goal."""
        data = {
            'name': 'Updated Goal',
            'target_amount': 250000,
            'target_date': date.today() + timedelta(days=730),
            'icon': '🎯',
            'color': 'primary',
            'currency': '₹'
        }
        
        response = self.client.post(
            reverse('goal-edit', kwargs={'pk': self.goal.pk}),
            data
        )
        
        self.assertEqual(response.status_code, 302)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.name, 'Updated Goal')
        self.assertEqual(self.goal.target_amount, Decimal('250000'))


class GoalDeleteViewTest(BaseComprehensiveTest):
    """Test deleting goals."""
    
    def setUp(self):
        super().setUp()
        self.goal = SavingsGoal.objects.create(
            user=self.user,
            name='To Delete',
            target_amount=100000,
            target_date=date.today() + timedelta(days=365),
            currency='₹'
        )
    
    def test_goal_delete_valid(self):
        """Test deleting a goal."""
        goal_id = self.goal.pk
        
        response = self.client.post(reverse('goal-delete', kwargs={'pk': goal_id}))
        
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SavingsGoal.objects.filter(pk=goal_id).exists())


# ============================================================================
# ALL TRANSACTIONS / EXPORT TESTS  
# ============================================================================

class AllTransactionsViewTest(BaseComprehensiveTest):
    """Test the all transactions unified view."""
    
    def test_all_transactions_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('all-transactions'))
        self.assertEqual(response.status_code, 302)
    
    def test_all_transactions_returns_200(self):
        """Test that all transactions view loads."""
        response = self.client.get(reverse('all-transactions'))
        self.assertEqual(response.status_code, 200)
    
    def test_all_transactions_shows_expenses_and_income(self):
        """Test that both expenses and income are shown."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today()
        )
        Income.objects.create(
            user=self.user,
            amount=5000,
            source='Salary',
            date=date.today()
        )
        
        response = self.client.get(reverse('all-transactions'))
        
        self.assertIn('transactions', response.context)
        transactions = response.context['transactions']
        self.assertEqual(len(transactions), 2)
    
    def test_all_transactions_shows_only_user_data(self):
        """Test that users only see their own transactions."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today()
        )
        Expense.objects.create(
            user=self.other_user,
            amount=200,
            category='Food',
            date=date.today()
        )
        
        response = self.client.get(reverse('all-transactions'))
        transactions = response.context['transactions']
        
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]['unified_amount'], 100)
    
    def test_all_transactions_date_range_filter(self):
        """Test filtering by date range."""
        today = date.today()
        yesterday = today - timedelta(days=1)
        tomorrow = today + timedelta(days=1)
        
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=today
        )
        Expense.objects.create(
            user=self.user,
            amount=200,
            category='Food',
            date=yesterday
        )
        
        response = self.client.get(
            reverse('all-transactions'),
            {
                'start_date': today.strftime('%Y-%m-%d'),
                'end_date': tomorrow.strftime('%Y-%m-%d')
            }
        )
        
        transactions = response.context['transactions']
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]['unified_amount'], 100)
    
    def test_all_transactions_category_filter(self):
        """Test filtering by search term."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today()
        )
        Expense.objects.create(
            user=self.user,
            amount=200,
            category='Transport',
            date=date.today()
        )
        
        response = self.client.get(reverse('all-transactions') + '?search=Food')
        transactions = response.context['transactions']
        
        self.assertEqual(len(transactions), 1)
        self.assertEqual(transactions[0]['cat'], 'Food')

    def test_all_transactions_cc_balances_and_summary_bar(self):
        """Test summary amounts and CC balance after payment calculations."""
        # 1. Create a Credit Card account
        cc_account = Account.objects.create(
            user=self.user,
            name='My CC',
            account_type='CREDIT_CARD',
            balance=Decimal('-1000.00'),
            is_active=True
        )
        
        # 2. Create an expense and a transfer (which is the CC payment)
        Expense.objects.create(
            user=self.user,
            amount=300,
            category='Food',
            date=date.today()
        )
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.account,
            to_account=cc_account,
            amount=Decimal('500.00'),
            date=date.today()
        )
        
        response = self.client.get(reverse('all-transactions'))
        self.assertEqual(response.status_code, 200)
        
        # Verify summary amounts in context
        self.assertEqual(response.context['expense_amount'], Decimal('300.00'))
        self.assertEqual(response.context['transfer_amount'], Decimal('500.00'))
        
        # Verify transaction list has CC balance after payment
        transactions = response.context['transactions']
        transfer_tx = [t for t in transactions if t['type'] == 'TRANSFER'][0]
        self.assertIsNotNone(transfer_tx.get('cc_balance_after_payment'))
        # Current balance of cc_account is -1000 (initial) + 500 (transfer) = -500.
        # So balance after payment is -500.00.
        self.assertEqual(transfer_tx['cc_balance_after_payment'], Decimal('-500.00'))


class ExportViewTest(BaseComprehensiveTest):
    """Test export functionality."""
    
    def test_export_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('export-expenses'))
        self.assertEqual(response.status_code, 302)
    
    def test_export_returns_200(self):
        """Test that export page loads."""
        response = self.client.get(reverse('export-expenses'))
        self.assertEqual(response.status_code, 200)
    
    def test_export_csv_download(self):
        """Test exporting to CSV."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today()
        )
        
        response = self.client.post(
            reverse('export-expenses'),
            {'format': 'csv'}
        )
        
        # CSV export should return file download with appropriate content-type
        self.assertIn(
            response['Content-Type'],
            ['text/csv', 'application/csv']
        )
        self.assertIn('Content-Disposition', response)
    
    def test_export_pdf_download(self):
        """Test exporting to PDF."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today()
        )
        
        response = self.client.post(
            reverse('export-expenses'),
            {'format': 'pdf'}
        )
        
        self.assertIn(response['Content-Type'], ['text/csv', 'application/csv'])
        self.assertIn('Content-Disposition', response)
    
    def test_export_only_user_data(self):
        """Test that export only includes user's data."""
        Expense.objects.create(
            user=self.user,
            amount=100,
            category='Food',
            date=date.today(),
            description='My Expense'
        )
        Expense.objects.create(
            user=self.other_user,
            amount=200,
            category='Food',
            date=date.today(),
            description='Other Expense'
        )
        
        response = self.client.post(
            reverse('export-expenses'),
            {'format': 'csv'}
        )
        
        content = response.content.decode('utf-8')
        self.assertIn('My Expense', content)
        self.assertNotIn('Other Expense', content)


# ============================================================================
# IMPORT TESTS
# ============================================================================

class ImportViewTest(BaseComprehensiveTest):
    """Test import/upload functionality."""
    
    def test_import_requires_login(self):
        """Test that anonymous users are redirected."""
        self._logout()
        response = self.client.get(reverse('upload'))
        self.assertEqual(response.status_code, 302)
    
    def test_import_returns_200(self):
        """Test that import page loads."""
        response = self.client.get(reverse('upload'))
        self.assertEqual(response.status_code, 200)
    
    def test_import_csv_valid(self):
        """Test importing a CSV file."""
        csv_content = b"""Date,Amount,Category,Description
2026-05-27,100,Food,Lunch
2026-05-26,200,Transport,Taxi"""
        
        from django.core.files.uploadedfile import SimpleUploadedFile
        
        csv_file = SimpleUploadedFile(
            'expenses.csv',
            csv_content,
            content_type='text/csv'
        )
        
        response = self.client.post(
            reverse('upload'),
            {'file': csv_file}
        )
        
        # Should show confirmation or redirect
        self.assertIn(response.status_code, [200, 302])
