from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.models import Account, Category, Expense, Income, JournalEntry, Transfer


class AccountTransferTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        # Ensure profile exists and tutorial is seen
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PLUS'
        profile.save()
        
        # Create categories
        self.food_cat, _ = Category.objects.get_or_create(user=self.user, name='Food', defaults={'limit': 1000})
        
        # Create accounts
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=Decimal('5000.00'))
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH', balance=Decimal('1000.00'))
        self.cc = Account.objects.create(user=self.user, name='Credit Card', account_type='CREDIT_CARD', balance=Decimal('-500.00'))

    def test_income_updates_account_balance(self):
        Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal('500.00'),
            source='Freelance',
            account=self.bank
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('5500.00'))

    def test_income_update_reverts_old_balance(self):
        income = Income.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal('500.00'),
            source='Freelance',
            account=self.bank
        )
        # Update amount
        income.amount = Decimal('700.00')
        income.save()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('5700.00'))
        
        # Switch account
        income.account = self.cash
        income.amount = Decimal('200.00')
        income.save()
        
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('5000.00'))
        self.assertEqual(self.cash.balance, Decimal('1200.00'))

    def test_expense_updates_account_balance(self):
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal('100.00'),
            category='Food',
            description='Lunch',
            account=self.bank
        )
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('4900.00'))

    def test_transfer_protocol(self):
        # Transfer from Bank to Cash
        transfer = Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cash,
            amount=Decimal('500.00'),
            date=date.today()
        )
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('4500.00'))
        self.assertEqual(self.cash.balance, Decimal('1500.00'))

        # Update transfer
        transfer.amount = Decimal('200.00')
        transfer.save()
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('4800.00'))
        self.assertEqual(self.cash.balance, Decimal('1200.00'))

        # Delete transfer
        transfer.delete()
        self.bank.refresh_from_db()
        self.cash.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('5000.00'))
        self.assertEqual(self.cash.balance, Decimal('1000.00'))

    def test_credit_card_spending_liability(self):
        # Spend on CC
        Expense.objects.create(
            user=self.user,
            date=date.today(),
            amount=Decimal('100.00'),
            category='Food',
            description='Dinner',
            account=self.cc
        )
        self.cc.refresh_from_db()
        self.assertEqual(self.cc.balance, Decimal('-600.00'))
        
        # Pay CC bill from Bank
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank,
            to_account=self.cc,
            amount=Decimal('600.00'),
            date=date.today()
        )
        self.bank.refresh_from_db()
        self.cc.refresh_from_db()
        self.assertEqual(self.bank.balance, Decimal('4400.00'))
        self.assertEqual(self.cc.balance, Decimal('0.00'))

    def test_view_account_crud(self):
        # List
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bank')
        
        # Create
        response = self.client.post(reverse('account-create'), {
            'name': 'New Wallet',
            'account_type': 'CASH',
            'balance': '100.00',
            'currency': '₹'
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Account.objects.filter(name='New Wallet').exists())
        
        # Edit
        account = Account.objects.get(name='New Wallet')
        response = self.client.post(reverse('account-edit', kwargs={'pk': account.pk}), {
            'name': 'Updated Wallet',
            'account_type': 'CASH',
            'balance': '200.00',
            'currency': '₹'
        })
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.name, 'Updated Wallet')
        
        # Delete (soft delete)
        response = self.client.post(reverse('account-delete', kwargs={'pk': account.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.filter(name='Updated Wallet', is_active=True).exists())
        self.assertTrue(Account.objects.filter(name='Updated Wallet', is_active=False).exists())

    def test_account_list_does_not_n_plus_one_auth_user_queries(self):
        for index in range(5):
            Account.objects.create(
                user=self.user,
                name=f'Extra {index}',
                account_type='BANK',
                balance=Decimal('10.00'),
            )

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse('account-list'))

        self.assertEqual(response.status_code, 200)
        auth_user_queries = [
            query for query in queries.captured_queries
            if 'FROM "auth_user"' in query.get('sql', '')
        ]
        self.assertLessEqual(len(auth_user_queries), 1)

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_account_balance_edit_creates_adjustment_entry(self):
        account = Account.objects.create(
            user=self.user,
            name='Adjustment Wallet',
            account_type='CASH',
            balance=Decimal('0.00'),
            currency='₹',
        )

        response = self.client.post(reverse('account-edit', kwargs={'pk': account.pk}), {
            'name': 'Adjustment Wallet',
            'account_type': 'CASH',
            'balance': '200.00',
            'currency': '₹',
        })

        self.assertEqual(response.status_code, 302)
        entries = JournalEntry.objects.filter(source_type='ADJUSTMENT', source_id=account.id)
        self.assertEqual(entries.count(), 1)
        entry = entries.first()
        self.assertEqual(entry.status, 'POSTED')
        self.assertEqual(entry.metadata.get('kind'), 'MANUAL_BALANCE_EDIT')

    @override_settings(LEDGER_WRITE_ENABLED=True, LEDGER_ENFORCE_BALANCED_WRITE=False)
    def test_account_edit_without_balance_change_creates_adjustment_when_drift_exists(self):
        account = Account.objects.create(
            user=self.user,
            name='No Delta Wallet',
            account_type='CASH',
            balance=Decimal('200.00'),
            currency='₹',
        )

        response = self.client.post(reverse('account-edit', kwargs={'pk': account.pk}), {
            'name': 'No Delta Wallet Renamed',
            'account_type': 'CASH',
            'balance': '200.00',
            'currency': '₹',
        })

        self.assertEqual(response.status_code, 302)
        entries = JournalEntry.objects.filter(source_type='ADJUSTMENT', source_id=account.id)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.first().metadata.get('kind'), 'MANUAL_BALANCE_EDIT')

    def test_view_transfer_crud(self):
        # Create via view
        response = self.client.post(reverse('transfer-create'), {
            'from_account': self.bank.pk,
            'to_account': self.cash.pk,
            'amount': '300.00',
            'date': date.today().strftime('%Y-%m-%d'),
            'description': 'View transfer'
        })
        self.assertEqual(response.status_code, 302)
        transfer = Transfer.objects.get(description='View transfer')
        self.assertEqual(transfer.amount, Decimal('300.00'))
        
        # List
        response = self.client.get(reverse('transfer-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'View transfer')
        
        # Edit
        response = self.client.post(reverse('transfer-edit', kwargs={'pk': transfer.pk}), {
            'from_account': self.bank.pk,
            'to_account': self.cash.pk,
            'amount': '400.00',
            'date': date.today().strftime('%Y-%m-%d'),
            'description': 'Updated transfer'
        })
        self.assertEqual(response.status_code, 302)
        transfer.refresh_from_db()
        self.assertEqual(transfer.amount, Decimal('400.00'))
        
        # Delete
        response = self.client.post(reverse('transfer-delete', kwargs={'pk': transfer.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Transfer.objects.filter(pk=transfer.pk).exists())

    def test_record_maturity_income_duplicate_prevention(self):
        """
        Tests RecordMaturityIncomeView duplicate prevention:
        1. Calling post() records accrued interest as an Income entry.
        2. Calling post() a second time in succession (simulating a double-submit or retry)
           detects the already-recorded maturity income and does NOT create a duplicate entry.
        3. Scoping by maturity cycle: when an account is renewed for a new cycle
           (with an updated maturity date), subsequent recording for that new cycle
           succeeds without being blocked by previous cycles.
        """
        # Create an FD account that matured yesterday
        past_maturity = date.today() - timedelta(days=1)
        start_date = past_maturity - timedelta(days=365)
        fd_account = Account.objects.create(
            user=self.user,
            name='Test Fixed Deposit',
            account_type='FIXED_DEPOSIT',
            balance=Decimal('100000.00'),
            deposit_principal=Decimal('100000.00'),
            deposit_rate=Decimal('7.0000'),
            deposit_start_date=start_date,
            deposit_maturity_date=past_maturity,
            deposit_compounding='ANNUAL',
            record_maturity_income=True,
        )

        url = reverse('account-record-maturity-income', kwargs={'pk': fd_account.pk})

        # 1. First submission -> should create Income entry
        resp1 = self.client.post(url)
        self.assertEqual(resp1.status_code, 302)
        incomes = Income.objects.filter(account=fd_account, source_type='Investment Returns')
        self.assertEqual(incomes.count(), 1)
        first_income = incomes.first()
        self.assertEqual(first_income.date, past_maturity)
        self.assertGreater(first_income.amount, Decimal('0.00'))

        # 2. Second submission in succession (double-click / browser resubmit)
        resp2 = self.client.post(url)
        self.assertEqual(resp2.status_code, 302)
        # Verify still only 1 Income entry exists (no duplicate created)
        self.assertEqual(Income.objects.filter(account=fd_account, source_type='Investment Returns').count(), 1)

        # 3. Simulate deposit renewal (new maturity cycle)
        # Suppose the FD is renewed with a new start date and a new maturity date that has arrived
        renewed_maturity = date.today()
        fd_account.deposit_start_date = past_maturity
        fd_account.deposit_maturity_date = renewed_maturity
        fd_account.deposit_principal = Decimal('107000.00')
        fd_account.save()

        resp3 = self.client.post(url)
        self.assertEqual(resp3.status_code, 302)
        # Now there should be exactly 2 Income entries: one for cycle 1, one for cycle 2
        incomes = Income.objects.filter(account=fd_account, source_type='Investment Returns').order_by('date')
        self.assertEqual(incomes.count(), 2)
        self.assertEqual(incomes[0].date, past_maturity)
        self.assertEqual(incomes[1].date, renewed_maturity)

    def test_account_update_view_balance_edit_locking_and_adjustment(self):
        """
        Tests manual balance edit via AccountUpdateView under LEDGER_WRITE_ENABLED.
        Verifies that select_for_update() is acquired on the Account row when computing
        and posting the shadow balance adjustment.

        NOTE ON CONCURRENCY & SQLITE TEST LIMITATION:
        Under SQLite (the test database backend configured in settings.py),
        `connection.features.has_select_for_update` is False, meaning SQLite ignores
        row-level SELECT FOR UPDATE statements and only utilizes database-level locking.
        Under production PostgreSQL (where has_select_for_update is True),
        Account.objects.select_for_update() ensures that concurrent edits serialize,
        preventing stale ledger delta reads during balance reconciliation.
        """
        with override_settings(LEDGER_WRITE_ENABLED=True):
            with patch.object(Account.objects, 'select_for_update', wraps=Account.objects.select_for_update) as mock_sfu:
                edit_url = reverse('account-edit', kwargs={'pk': self.bank.pk})
                response = self.client.post(edit_url, {
                    'name': self.bank.name,
                    'account_type': self.bank.account_type,
                    'balance': '6500.00',
                    'currency': self.bank.currency,
                })
                self.assertEqual(response.status_code, 302)
                self.assertTrue(mock_sfu.called)

                self.bank.refresh_from_db()
                self.assertEqual(self.bank.balance, Decimal('6500.00'))
