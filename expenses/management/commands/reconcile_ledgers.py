from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from expenses.models import (
    Account,
    JournalEntry,
    JournalLine,
    LedgerReconciliationReport,
)


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

        as_of_date = timezone.now().date()
        total = 0
        drifts = 0
        missing_opening_entries = 0

        for account in accounts:
            total += 1

            has_opening_entry = JournalEntry.objects.filter(
                user=account.user,
                source_type="ADJUSTMENT",
                metadata__opening_account_id=account.id,
                status="POSTED",
            ).exists()
            if not has_opening_entry:
                missing_opening_entries += 1

            debit_total = (
                JournalLine.objects.filter(
                    account_ref=account,
                    direction="DEBIT",
                    journal_entry__status="POSTED",
                )
                .aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))
                .get("total", Decimal("0.00"))
            )
            credit_total = (
                JournalLine.objects.filter(
                    account_ref=account,
                    direction="CREDIT",
                    journal_entry__status="POSTED",
                )
                .aggregate(total=Coalesce(Sum("amount"), Decimal("0.00")))
                .get("total", Decimal("0.00"))
            )

            ledger_balance = (debit_total - credit_total).quantize(Decimal("0.01"))
            account_balance = account.balance.quantize(Decimal("0.01"))
            drift_amount = (account_balance - ledger_balance).quantize(Decimal("0.01"))
            status = "MATCH" if abs(drift_amount) <= threshold else "DRIFT"
            if status == "DRIFT":
                drifts += 1

            LedgerReconciliationReport.objects.create(
                user=account.user,
                account=account,
                as_of_date=as_of_date,
                account_balance=account_balance,
                ledger_balance=ledger_balance,
                drift_amount=drift_amount,
                status=status,
                metadata={
                    "threshold": str(threshold),
                    "has_opening_entry": has_opening_entry,
                },
            )

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
