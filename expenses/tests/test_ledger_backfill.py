from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, JournalEntry


class LedgerOpeningBackfillTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="backfill", password="pass")
        self.user.profile.currency = "₹"
        self.user.profile.save(update_fields=["currency"])
        self.account = Account.objects.create(
            user=self.user,
            name="Main",
            account_type="BANK",
            balance=Decimal("1234.00"),
            currency="₹",
        )

    def test_backfill_creates_opening_adjustment_idempotently(self):
        call_command("backfill_ledger_opening_balances", user_id=self.user.id)
        call_command("backfill_ledger_opening_balances", user_id=self.user.id)

        entries = JournalEntry.objects.filter(
            user=self.user,
            source_type="ADJUSTMENT",
            source_id=self.account.id,
            metadata__opening_account_id=self.account.id,
        )
        self.assertEqual(entries.count(), 1)
        entry = entries.first()
        self.assertEqual((entry.metadata or {}).get("opening_balance"), "1234.00")
        self.assertEqual(entry.lines.count(), 2)

    @override_settings(LEDGER_READ_ENABLED=True)
    def test_ledger_read_uses_opening_after_backfill(self):
        call_command("backfill_ledger_opening_balances", user_id=self.user.id)
        self.assertEqual(LedgerReadService.get_account_balance(self.account), Decimal("1234.00"))

    def test_backfill_uses_single_opening_balance_lookup(self):
        # Create a second account
        Account.objects.create(
            user=self.user,
            name="Second",
            account_type="BANK",
            balance=Decimal("500.00"),
            currency="₹",
        )
        # Create a third account
        Account.objects.create(
            user=self.user,
            name="Third",
            account_type="BANK",
            balance=Decimal("100.00"),
            currency="₹",
        )

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            call_command("backfill_ledger_opening_balances", user_id=self.user.id, dry_run=True)

        opening_lookup_queries = [
            query['sql'] for query in queries.captured_queries
            if 'expenses_journalentry' in query['sql'] and 'opening_account_id' in query['sql']
        ]
        self.assertEqual(len(opening_lookup_queries), 1)

