from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.utils import timezone

from expenses.models import (
    Account,
    JournalEntry,
    JournalLine,
    LedgerReconciliationReport,
)
from expenses.utils import get_exchange_rate

MATCH_RETENTION_DAYS = 90


class Command(BaseCommand):
    help = "Reconcile account balances against ledger-derived balances"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, help="Reconcile for a single user")
        parser.add_argument("--threshold", type=str, default="0.01", help="Absolute drift threshold")
        parser.add_argument(
            "--alert-threshold",
            type=str,
            default=str(getattr(settings, "LEDGER_RECONCILE_ALERT_THRESHOLD", "10.00")),
            help="Absolute drift amount above which a user alert is created",
        )
        parser.add_argument(
            "--fail-on-drift",
            action="store_true",
            help="Exit with non-zero status when any account is in DRIFT.",
        )
        parser.add_argument(
            "--require-opening-balances",
            action="store_true",
            help="Exit with non-zero status when any reconciled account has no opening balance journal entry.",
        )

    def handle(self, *args, **options):
        threshold = Decimal(options["threshold"])
        accounts = Account.objects.filter(is_active=True).select_related("user")
        if options.get("user_id"):
            accounts = accounts.filter(user_id=options["user_id"])

        accounts = list(accounts)
        user_ids = {account.user_id for account in accounts}
        opening_account_ids = set()
        if user_ids:
            opening_account_ids = set(
                JournalEntry.objects.filter(
                    user_id__in=user_ids,
                    source_type="ADJUSTMENT",
                    status="POSTED",
                    metadata__has_key="opening_account_id",
                ).values_list("metadata__opening_account_id", flat=True)
            )
            opening_account_ids = {int(val) for val in opening_account_ids if val is not None}

        as_of_date = timezone.localdate()

        # Every account's ledger balance in ONE query, each line in the account's own currency (an older line
        # can be recorded in another currency; adding its raw amount would be wrong).
        accounts_by_id = {a.id: a for a in accounts}
        ledger_balances = {account_id: Decimal("0.00") for account_id in accounts_by_id}
        rows = (
            JournalLine.objects.filter(account_ref_id__in=list(accounts_by_id), journal_entry__status="POSTED")
            .values("account_ref_id", "direction", "currency").annotate(total=Sum("amount")).order_by()
        )
        for row in rows:
            account = accounts_by_id[row["account_ref_id"]]
            amount = row["total"]
            if row["currency"] and row["currency"] != account.currency:
                amount = (amount * get_exchange_rate(row["currency"], account.currency)).quantize(Decimal("0.01"))
            ledger_balances[account.id] += amount if row["direction"] == "DEBIT" else -amount
        total = 0
        drifts = 0
        missing_opening_entries = 0

        for account in accounts:
            total += 1

            has_opening_entry = account.id in opening_account_ids
            if not has_opening_entry:
                missing_opening_entries += 1

            ledger_balance = ledger_balances.get(account.id, Decimal("0.00"))
            account_balance = account.balance.quantize(Decimal("0.01"))
            drift_amount = (account_balance - ledger_balance).quantize(Decimal("0.01"))
            status = "MATCH" if abs(drift_amount) <= threshold else "DRIFT"
            if status == "DRIFT":
                drifts += 1

            # One report per account per day: a re-run refreshes it instead of adding another row.
            LedgerReconciliationReport.objects.update_or_create(
                account=account,
                as_of_date=as_of_date,
                defaults={
                    "user": account.user,
                    "account_balance": account_balance,
                    "ledger_balance": ledger_balance,
                    "drift_amount": drift_amount,
                    "status": status,
                    "metadata": {
                        "threshold": str(threshold),
                        "has_opening_entry": has_opening_entry,
                    },
                },
            )

        # Clean matches are only noise after a while; drifts are kept for investigation.
        if accounts:
            LedgerReconciliationReport.objects.filter(
                account__in=accounts,
                status="MATCH",
                as_of_date__lt=as_of_date - timedelta(days=MATCH_RETENTION_DAYS),
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Reconciliation complete: accounts={total}, drifts={drifts}, missing_opening_entries={missing_opening_entries}"
            )
        )

        if options.get("require_opening_balances") and missing_opening_entries > 0:
            raise CommandError(
                f"Reconciliation gate failed: {missing_opening_entries} account(s) are missing opening balance journal entries."
            )

        if options.get("fail_on_drift") and drifts > 0:
            raise CommandError(f"Reconciliation gate failed: {drifts} account(s) in DRIFT state.")
