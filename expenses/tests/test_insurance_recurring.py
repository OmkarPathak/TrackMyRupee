from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from expenses.account_valuation import PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY
from expenses.forms import AccountForm, RecurringTransactionForm
from expenses.management.commands.send_notifications import (
    Command as SendNotificationsCommand,
)
from expenses.models import (
    Account,
    Expense,
    Notification,
    PhysicalAsset,
    RecurringTransaction,
    UserProfile,
)


class InsuranceRecurringTransactionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="insurancetest", password="password")
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})

        self.payment_account = Account.objects.create(
            user=self.user,
            name="HDFC Savings",
            account_type="SAVINGS_ACCOUNT",
            currency="₹",
            balance=Decimal("50000.00"),
        )

    def test_frequency_mapping_table_exact_matches(self):
        """Explicit test per mapping for each of the 4 premium_frequency values."""
        self.assertEqual(PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY['ANNUAL'], 'YEARLY')
        self.assertEqual(PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY['SEMI_ANNUAL'], 'SEMIANNUALLY')
        self.assertEqual(PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY['QUARTERLY'], 'QUARTERLY')
        self.assertEqual(PREMIUM_FREQUENCY_TO_RECURRING_FREQUENCY['MONTHLY'], 'MONTHLY')

    def test_create_life_insurance_account_creates_recurring_transaction(self):
        """Creating a LIFE_INSURANCE account/policy with premium details creates exactly 1 RecurringTransaction."""
        form_data = {
            'name': 'LIC Policy Account',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'LIC Jeevan Anand',
            'policy_number': 'POL-123456',
            'premium_amount': '25000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': '2024-01-01',
            'sum_assured': '500000.00',
            'premium_payment_account': self.payment_account.id,
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        policy = account.linked_physical_asset
        self.assertIsNotNone(policy)

        recurring_qs = RecurringTransaction.objects.filter(physical_asset=policy)
        self.assertEqual(recurring_qs.count(), 1)

        rt = recurring_qs.first()
        self.assertEqual(rt.transaction_type, 'INSURANCE_PREMIUM')
        self.assertEqual(rt.account, self.payment_account)
        self.assertEqual(rt.amount, Decimal('25000.00'))
        self.assertEqual(rt.frequency, 'YEARLY')
        self.assertEqual(rt.start_date, date(2024, 1, 1))
        self.assertEqual(rt.description, "LIC Jeevan Anand premium")
        self.assertTrue(rt.is_active)

    def test_edit_policy_premium_updates_recurring_transaction_in_place(self):
        """Editing policy premium amount updates existing RecurringTransaction without creating a duplicate."""
        form_data = {
            'name': 'LIC Policy Account',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'LIC Policy',
            'policy_number': 'POL-999999',
            'premium_amount': '25000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': '2024-01-01',
            'sum_assured': '500000.00',
            'premium_payment_account': self.payment_account.id,
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        policy = account.linked_physical_asset
        original_rt = RecurringTransaction.objects.get(physical_asset=policy)

        # Edit account with updated premium amount
        edit_data = {
            'name': 'LIC Policy Account Updated',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'linked_physical_asset': policy.id,
            'create_new_asset': 'SELECT',
            'policy_number': 'POL-999999',
            'premium_amount': '30000.00',
            'premium_frequency': 'SEMI_ANNUAL',
            'policy_start_date': '2024-01-01',
            'sum_assured': '500000.00',
            'premium_payment_account': self.payment_account.id,
        }
        edit_form = AccountForm(data=edit_data, instance=account, user=self.user)
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        updated_account = edit_form.save()

        recurring_qs = RecurringTransaction.objects.filter(physical_asset=policy)
        self.assertEqual(recurring_qs.count(), 1)

        updated_rt = recurring_qs.first()
        self.assertEqual(updated_rt.id, original_rt.id)
        self.assertEqual(updated_rt.amount, Decimal('30000.00'))
        self.assertEqual(updated_rt.frequency, 'SEMIANNUALLY')

    def test_clearing_payment_account_deactivates_linked_schedule(self):
        """Clearing payment account on edit deactivates (is_active=False) the schedule."""
        form_data = {
            'name': 'Max Life Policy',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'Max Life',
            'policy_number': 'POL-777',
            'premium_amount': '12000.00',
            'premium_frequency': 'MONTHLY',
            'policy_start_date': '2024-03-01',
            'premium_payment_account': self.payment_account.id,
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        policy = account.linked_physical_asset

        # Edit form clearing payment account
        edit_data = {
            'name': 'Max Life Policy',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'linked_physical_asset': policy.id,
            'create_new_asset': 'SELECT',
            'policy_number': 'POL-777',
            'premium_amount': '12000.00',
            'premium_frequency': 'MONTHLY',
            'policy_start_date': '2024-03-01',
            'premium_payment_account': '',
        }
        edit_form = AccountForm(data=edit_data, instance=account, user=self.user)
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        edit_form.save()

        rt = RecurringTransaction.objects.get(physical_asset=policy)
        self.assertFalse(rt.is_active)

    def test_create_recurring_transaction_directly(self):
        """Creating an INSURANCE_PREMIUM RecurringTransaction directly from Recurring Transactions page."""
        policy = PhysicalAsset.objects.create(
            user=self.user,
            name="ICICI Prudential",
            asset_class="INSURANCE",
            policy_number="POL-888",
            premium_amount=Decimal("15000.00"),
            premium_frequency="ANNUAL",
            policy_start_date=date(2024, 6, 1),
        )

        form_data = {
            'transaction_type': 'INSURANCE_PREMIUM',
            'amount': '15000.00',
            'currency': '₹',
            'payment_method': 'Cash',
            'account': self.payment_account.id,
            'physical_asset': policy.id,
            'frequency': 'YEARLY',
            'start_date': '2024-06-01',
            'description': 'ICICI Prudential premium',
            'is_active': True,
        }
        form = RecurringTransactionForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        rt = form.save(commit=False)
        rt.user = self.user
        rt.save()

        db_rt = RecurringTransaction.objects.get(pk=rt.pk)
        self.assertEqual(db_rt.transaction_type, 'INSURANCE_PREMIUM')
        self.assertEqual(db_rt.physical_asset, policy)
        self.assertEqual(db_rt.account, self.payment_account)
        self.assertEqual(db_rt.amount, Decimal('15000.00'))

    def test_notification_reminder_fires_for_insurance_premium(self):
        """_process_recurring_reminders fires for INSURANCE_PREMIUM 3 days before next_due_date."""
        policy = PhysicalAsset.objects.create(
            user=self.user,
            name="Tata AIA Shield",
            asset_class="INSURANCE",
        )

        insurance_account = Account.objects.create(
            user=self.user,
            name="Tata AIA Policy Account",
            account_type="LIFE_INSURANCE",
            currency="₹",
            linked_physical_asset=policy,
        )

        today = date(2026, 9, 2)
        due_date = today + timedelta(days=3)

        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            physical_asset=policy,
            account=self.payment_account,
            amount=Decimal("18000.00"),
            frequency='YEARLY',
            start_date=due_date,
            next_due_date=due_date,
            description="Tata AIA Shield premium",
            is_active=True,
        )

        cmd = SendNotificationsCommand()
        cmd.today = today
        cmd.active_recurring_by_user = {self.user.id: [rt]}
        cmd.sent_notifications_by_user = {}
        cmd.users_with_push = set()
        cmd.current_user_notifications = []
        cmd.stdout = open('/dev/null', 'w')

        cmd._process_recurring_reminders(self.user)

        notifications = Notification.objects.filter(user=self.user, notification_type='RECURRING')
        self.assertEqual(notifications.count(), 1)
        notif = notifications.first()
        self.assertIn("Tata AIA Shield", notif.title)
        self.assertIn(f"/accounts/{insurance_account.uuid}/", notif.link)

    def test_notification_reminder_fallback_link_when_no_linked_account(self):
        """Notification link falls back to /accounts/ when policy has no linked account."""
        policy = PhysicalAsset.objects.create(
            user=self.user,
            name="Standalone Policy",
            asset_class="INSURANCE",
        )

        today = date(2026, 9, 2)
        due_date = today + timedelta(days=3)

        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            physical_asset=policy,
            account=self.payment_account,
            amount=Decimal("10000.00"),
            frequency='YEARLY',
            start_date=due_date,
            next_due_date=due_date,
            description="Standalone Policy premium",
            is_active=True,
        )

        cmd = SendNotificationsCommand()
        cmd.today = today
        cmd.active_recurring_by_user = {self.user.id: [rt]}
        cmd.sent_notifications_by_user = {}
        cmd.users_with_push = set()
        cmd.current_user_notifications = []
        cmd.stdout = open('/dev/null', 'w')

        cmd._process_recurring_reminders(self.user)

        notif = Notification.objects.get(user=self.user, notification_type='RECURRING')
        self.assertEqual(notif.link, "/accounts/")

    def test_delete_physical_asset_cascades_to_recurring_transaction(self):
        """Deleting PhysicalAsset cascades to delete its linked RecurringTransaction and handles no-schedule asset."""
        policy = PhysicalAsset.objects.create(
            user=self.user,
            name="Policy To Delete",
            asset_class="INSURANCE",
        )
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            physical_asset=policy,
            account=self.payment_account,
            amount=Decimal("5000.00"),
            frequency='MONTHLY',
            start_date=date(2024, 1, 1),
            is_active=True,
        )

        rt_id = rt.id
        policy.delete()

        self.assertFalse(RecurringTransaction.objects.filter(id=rt_id).exists())

        policy_no_schedule = PhysicalAsset.objects.create(
            user=self.user,
            name="Unscheduled Policy",
            asset_class="INSURANCE",
        )
        policy_no_schedule.delete()  # Should not raise error

    def test_deactivating_account_or_policy_deactivates_recurring_schedule(self):
        """Deactivating an Account or PhysicalAsset deactivates the linked RecurringTransaction."""
        policy = PhysicalAsset.objects.create(
            user=self.user,
            name="Policy For Deactivation",
            asset_class="INSURANCE",
        )
        account = Account.objects.create(
            user=self.user,
            name="Insurance Account",
            account_type="LIFE_INSURANCE",
            currency="₹",
            linked_physical_asset=policy,
        )
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            physical_asset=policy,
            account=self.payment_account,
            amount=Decimal("15000.00"),
            frequency='YEARLY',
            start_date=date(2024, 1, 1),
            is_active=True,
        )

        # Deactivate policy
        policy.is_active = False
        policy.save()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)

        # Reactivate rt, then deactivate account
        rt.is_active = True
        rt.save()
        account.is_active = False
        account.save()
        rt.refresh_from_db()
        self.assertFalse(rt.is_active)

    def test_past_start_date_auto_triggers_expense_and_advances_next_due_date(self):
        """Creating/saving an insurance policy with a past start date automatically generates an Expense transaction."""
        today = date.today()
        past_start = today - timedelta(days=1)

        form_data = {
            'name': 'HDFC Ergo Term Insurance',
            'account_type': 'LIFE_INSURANCE',
            'currency': '₹',
            'balance': '0.00',
            'create_new_asset': 'CREATE_NEW',
            'asset_name': 'HDFC Term Policy',
            'policy_number': 'POL-999',
            'sum_assured': '5000000',
            'premium_amount': '15000.00',
            'premium_frequency': 'ANNUAL',
            'policy_start_date': past_start.strftime('%Y-%m-%d'),
            'premium_payment_account': self.payment_account.id,
        }

        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()

        # Check recurring transaction created
        rt = RecurringTransaction.objects.get(physical_asset=account.linked_physical_asset, transaction_type='INSURANCE_PREMIUM')
        self.assertEqual(rt.last_processed_date, past_start)
        self.assertGreater(rt.next_due_date, today)

        # Check Expense record generated
        expense = Expense.objects.filter(
            user=self.user,
            account=self.payment_account,
            amount=Decimal('15000.00'),
            date=past_start
        ).first()
        self.assertIsNotNone(expense)
        self.assertEqual(expense.category, 'Insurance')
        self.assertIn('HDFC Term Policy premium', expense.description)

    def test_cooldown_bypassed_when_schedules_due(self):
        """process_user_recurring_transactions runs even if cooldown cache key exists when due items exist."""
        today = date.today()
        past_start = today - timedelta(days=1)

        # Create schedule manually with past start date and not processed yet
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            account=self.payment_account,
            amount=Decimal('5000.00'),
            frequency='YEARLY',
            start_date=past_start,
            description="Test Policy Premium",
            is_active=True,
        )

        # Simulate existing 24hr cooldown cache
        cache_key = f'recurring_processed_{self.user.id}_{today}'
        cache.set(cache_key, True, 86400)

        # Call process_user_recurring_transactions without force
        from expenses.views.mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.user, force=False)

        rt.refresh_from_db()
        self.assertEqual(rt.last_processed_date, past_start)
        self.assertGreater(rt.next_due_date, today)

        # Verify expense was created
        expense = Expense.objects.filter(user=self.user, date=past_start, amount=Decimal('5000.00')).first()
        self.assertIsNotNone(expense)

    def test_inactive_account_never_triggers_recurring_transactions(self):
        """Inactive accounts and physical assets never trigger recurring transactions."""
        today = date.today()
        past_start = today - timedelta(days=5)

        # Deactivate payment account
        self.payment_account.is_active = False
        self.payment_account.save()

        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            account=self.payment_account,
            amount=Decimal('1200.00'),
            frequency='MONTHLY',
            start_date=past_start,
            description="Inactive Account Expense",
            is_active=True,
        )

        from expenses.views.mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.user, force=True)

        rt.refresh_from_db()
        self.assertFalse(rt.is_active)
        self.assertIsNone(rt.last_processed_date)

        # Confirm no expense posted
        expense_exists = Expense.objects.filter(user=self.user, account=self.payment_account).exists()
        self.assertFalse(expense_exists)

