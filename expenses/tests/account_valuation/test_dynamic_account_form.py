import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_types import get_fields_for_account_type
from expenses.forms import AccountForm
from expenses.models import Account, UserProfile


class TestDynamicAccountForm(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='formuser', password='password')
        UserProfile.objects.get_or_create(user=self.user)

    def test_single_source_of_truth_field_mapping(self):
        """
        SPEC §8 point 1: get_fields_for_account_type mapping returns strategy fields.
        """
        fd_fields = get_fields_for_account_type('FD')
        self.assertIn('deposit_principal', fd_fields)
        self.assertIn('deposit_rate', fd_fields)
        self.assertIn('deposit_start_date', fd_fields)
        self.assertNotIn('rd_installment_amount', fd_fields)

        rd_fields = get_fields_for_account_type('RD')
        self.assertIn('deposit_principal', rd_fields)
        self.assertIn('rd_installment_amount', rd_fields)
        self.assertIn('rd_installment_day', rd_fields)

        savings_fields = get_fields_for_account_type('SAVINGS_ACCOUNT')
        self.assertEqual(savings_fields, [])

    def test_form_json_property(self):
        """
        Verify AccountForm.fields_by_type_json exports valid JSON mapping.
        """
        form = AccountForm(user=self.user)
        mapping = json.loads(form.fields_by_type_json)
        self.assertIn('FD', mapping)
        self.assertIn('RD', mapping)
        self.assertIn('SAVINGS_ACCOUNT', mapping)
        self.assertIn('deposit_rate', mapping['FD'])
        self.assertIn('rd_installment_amount', mapping['RD'])

    def test_deposit_form_validation(self):
        """
        SPEC §8 point 3: Form clean() enforces required fields for DEPOSIT strategy and RD code.
        """
        # Missing deposit_principal, rate, start_date for new FD
        form_invalid_fd = AccountForm(
            data={
                'name': 'My FD',
                'account_type': 'FD',
                'balance': '50000.00',
                'currency': '₹',
            },
            user=self.user
        )
        self.assertFalse(form_invalid_fd.is_valid())
        self.assertIn('deposit_principal', form_invalid_fd.errors)
        self.assertIn('deposit_rate', form_invalid_fd.errors)
        self.assertIn('deposit_start_date', form_invalid_fd.errors)

        # Valid FD
        form_valid_fd = AccountForm(
            data={
                'name': 'Valid FD',
                'account_type': 'FD',
                'balance': '50000.00',
                'currency': '₹',
                'deposit_principal': '50000.00',
                'deposit_rate': '7.5',
                'deposit_start_date': '2025-01-01',
                'deposit_compounding': 'QUARTERLY',
                'show_accrued_balance': True,
            },
            user=self.user
        )
        self.assertTrue(form_valid_fd.is_valid(), form_valid_fd.errors)

        # RD missing installment amount and day
        form_invalid_rd = AccountForm(
            data={
                'name': 'My RD',
                'account_type': 'RD',
                'balance': '5000.00',
                'currency': '₹',
                'deposit_principal': '5000.00',
                'deposit_rate': '7.0',
                'deposit_start_date': '2025-01-01',
            },
            user=self.user
        )
        self.assertFalse(form_invalid_rd.is_valid())
        self.assertIn('rd_installment_amount', form_invalid_rd.errors)
        self.assertIn('rd_installment_day', form_invalid_rd.errors)

    def test_stray_fields_cleared_on_submit(self):
        """
        SPEC §8 point 3: Submitting a SAVINGS_ACCOUNT form with stray deposit fields
        must succeed and clear/null out the stray deposit values.
        """
        form = AccountForm(
            data={
                'name': 'Clean Savings Account',
                'account_type': 'SAVINGS_ACCOUNT',
                'balance': '10000.00',
                'currency': '₹',
                'deposit_principal': '99999.00', # stray field
                'deposit_rate': '12.0',           # stray field
            },
            user=self.user
        )
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save(commit=False)
        account.user = self.user
        account.save()
        self.assertIsNone(account.deposit_principal)
        self.assertIsNone(account.deposit_rate)

    def test_strategy_transition_clears_old_fields(self):
        """
        SPEC §8 point 5: Switching an existing account from FD to SAVINGS_ACCOUNT
        nulls out the now-irrelevant deposit fields on save.
        """
        existing_fd = Account.objects.create(
            user=self.user,
            name='Transition Account',
            account_type='FD',
            currency='₹',
            balance=Decimal('50000.00'),
            deposit_principal=Decimal('50000.00'),
            deposit_rate=Decimal('7.5'),
            deposit_start_date=date(2025, 1, 1),
        )

        form = AccountForm(
            data={
                'name': 'Transition Account',
                'account_type': 'SAVINGS_ACCOUNT',
                'balance': '50000.00',
                'currency': '₹',
            },
            instance=existing_fd,
            user=self.user
        )
        self.assertTrue(form.is_valid(), form.errors)
        updated_account = form.save(commit=False)
        updated_account.user = self.user
        updated_account.save()

        self.assertEqual(updated_account.account_type, 'SAVINGS_ACCOUNT')
        self.assertIsNone(updated_account.deposit_principal)
        self.assertIsNone(updated_account.deposit_rate)
        self.assertIsNone(updated_account.deposit_start_date)

    def test_account_name_uniqueness_checks_only_active_accounts(self):
        """
        Account name uniqueness validation should only trigger for active accounts.
        If an account with the same name exists but is soft-deleted (is_active=False),
        creating a new active account with that name must succeed.
        """
        # Create an inactive account
        Account.objects.create(
            user=self.user,
            name='Old Deleted Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('0.00'),
            is_active=False,
        )

        # Form with same name for active account should be valid
        form_inactive_dup = AccountForm(
            data={
                'name': 'Old Deleted Account',
                'account_type': 'SAVINGS_ACCOUNT',
                'balance': '1000.00',
                'currency': '₹',
            },
            user=self.user,
        )
        self.assertTrue(form_inactive_dup.is_valid(), form_inactive_dup.errors)

        # Create an active account
        Account.objects.create(
            user=self.user,
            name='Active Unique Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('0.00'),
            is_active=True,
        )

        # Form with same name as an active account should fail
        form_active_dup = AccountForm(
            data={
                'name': 'Active Unique Account',
                'account_type': 'SAVINGS_ACCOUNT',
                'balance': '1000.00',
                'currency': '₹',
            },
            user=self.user,
        )
        self.assertFalse(form_active_dup.is_valid())
        self.assertIn('name', form_active_dup.errors)

    def test_deposit_closed_date_and_record_maturity_income(self):
        """
        Test that deposit_closed_date is saved and record_maturity_income creates an Income entry
        with source_type='Investment Returns'.
        """
        form = AccountForm(
            data={
                'name': 'Matured FD',
                'account_type': 'FD',
                'balance': '100000.00',
                'currency': '₹',
                'deposit_principal': '100000.00',
                'deposit_rate': '10.0',
                'deposit_start_date': '2025-01-01',
                'deposit_maturity_date': '2026-01-01',
                'deposit_closed_date': '2026-01-01',
                'deposit_compounding': 'SIMPLE',
                'record_maturity_income': True,
            },
            user=self.user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save(commit=False)
        account.user = self.user
        account.save()
        form._record_maturity_income(account)

        self.assertEqual(account.deposit_closed_date, date(2026, 1, 1))

        # Check Income entry
        from expenses.models import Income
        income = Income.objects.filter(user=self.user, source_type='Investment Returns').first()
        self.assertIsNotNone(income)
        self.assertEqual(income.source_type, 'Investment Returns')
        self.assertGreater(income.amount, Decimal('9900.00'))

    def test_edit_account_preserves_record_maturity_income_toggle_on_ui(self):
        """Test that editing an account with record_maturity_income=True populates the form toggle as checked."""
        account = Account.objects.create(
            user=self.user,
            name='Saved Matured Deposit',
            account_type='FD',
            balance=Decimal('100000.00'),
            record_maturity_income=True,
        )
        form = AccountForm(instance=account, user=self.user)
        self.assertTrue(form.initial.get('record_maturity_income'))
        self.assertIn('checked', form['record_maturity_income'].as_widget())

    def test_holdings_and_revolving_credit_form_rendering_and_cleaning(self):
        """Test form validation and cleaning for HOLDINGS and REVOLVING_CREDIT account types."""
        # 1. HOLDINGS platform account creation (e.g. DEMAT)
        form_demat = AccountForm(
            data={
                'name': 'Zerodha Demat Platform',
                'account_type': 'DEMAT',
                'balance': '0.00',
                'currency': '₹',
                'deposit_principal': '10000.00',  # stray deposit field
            },
            user=self.user,
        )
        self.assertTrue(form_demat.is_valid(), form_demat.errors)
        account_demat = form_demat.save(commit=False)
        # Stray deposit field must be nulled out for HOLDINGS
        self.assertIsNone(account_demat.deposit_principal)

        # 2. REVOLVING_CREDIT account creation (CREDIT_CARD)
        form_card = AccountForm(
            data={
                'name': 'HDFC Diners Card',
                'account_type': 'CREDIT_CARD',
                'balance': '-15000.00',
                'currency': '₹',
                'credit_limit': '250000.00',
            },
            user=self.user,
        )
        self.assertTrue(form_card.is_valid(), form_card.errors)
        account_card = form_card.save(commit=False)
        self.assertEqual(account_card.credit_limit, Decimal('250000.00'))
