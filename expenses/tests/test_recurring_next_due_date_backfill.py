import importlib
from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import RecurringTransaction

migration_module = importlib.import_module('expenses.migrations.0090_backfill_recurringtransaction_next_due_date')
backfill_next_due_date = migration_module.backfill_next_due_date


class RecurringNextDueDateBackfillTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='backfilluser', password='password')
        self.client = Client()
        self.client.login(username='backfilluser', password='password')

    def test_backfill_next_due_date_migration(self):
        """Test that backfill migration populates missing next_due_date correctly."""
        # Create un-processed recurring transaction
        rt_unprocessed = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=1000,
            currency='INR',
            description='Unprocessed Sub',
            frequency='MONTHLY',
            start_date=date(2026, 1, 15),
            last_processed_date=None,
            is_active=True
        )

        # Create processed recurring transaction
        rt_processed = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=500,
            currency='INR',
            description='Processed Sub',
            frequency='MONTHLY',
            start_date=date(2026, 1, 1),
            last_processed_date=date(2026, 1, 1),
            is_active=True
        )

        # Create expired recurring transaction
        rt_expired = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=200,
            currency='INR',
            description='Expired Sub',
            frequency='MONTHLY',
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 1),
            last_processed_date=date(2025, 6, 1),
            is_active=True
        )

        # Simulate pre-0087 database state where next_due_date IS NULL
        RecurringTransaction.objects.filter(id__in=[rt_unprocessed.id, rt_processed.id, rt_expired.id]).update(next_due_date=None)

        rt_unprocessed.refresh_from_db()
        rt_processed.refresh_from_db()
        rt_expired.refresh_from_db()

        self.assertIsNone(rt_unprocessed.next_due_date)
        self.assertIsNone(rt_processed.next_due_date)
        self.assertIsNone(rt_expired.next_due_date)

        # Run the backfill data migration function
        backfill_next_due_date(None, None)

        rt_unprocessed.refresh_from_db()
        rt_processed.refresh_from_db()
        rt_expired.refresh_from_db()

        # Unprocessed should be start_date
        self.assertEqual(rt_unprocessed.next_due_date, date(2026, 1, 15))

        # Processed should be next monthly date (2026-02-01)
        self.assertEqual(rt_processed.next_due_date, date(2026, 2, 1))

        # Expired should remain None
        self.assertIsNone(rt_expired.next_due_date)

    def test_recurring_list_mobile_view_does_not_show_sentinel_days(self):
        """Regression test ensuring a transaction with null next_due_date does not render 999999."""
        rt_expired = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=200,
            currency='INR',
            description='Expired Sub UI Test',
            frequency='MONTHLY',
            start_date=date(2025, 1, 1),
            end_date=date(2025, 6, 1),
            last_processed_date=date(2025, 6, 1),
            is_active=True
        )
        self.assertIsNone(rt_expired.next_due_date)

        url = reverse('recurring-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('999999', response.content.decode('utf-8'))
