from django.core.management.base import BaseCommand

from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, JournalEntry


class Command(BaseCommand):
    help = "Backfill opening-balance adjustment journals for existing accounts"

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, help="Limit to a single user")
        parser.add_argument("--account-id", type=int, help="Limit to a single account")
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        accounts = Account.objects.select_related("user").all().order_by("id")
        if options.get("user_id"):
            accounts = accounts.filter(user_id=options["user_id"])
        if options.get("account_id"):
            accounts = accounts.filter(id=options["account_id"])

        accounts = list(accounts[: options["limit"]])
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

        created = 0
        skipped = 0
        zero_balance = 0

        for account in accounts:
            exists = account.id in opening_account_ids
            if exists:
                skipped += 1
                continue

            if account.balance == 0:
                zero_balance += 1
                continue

            if options.get("dry_run"):
                self.stdout.write(f"[dry-run] would backfill account {account.id} ({account.name})")
                created += 1
                continue

            try:
                _, is_created = LedgerPostingService.post_opening_balance(account=account)
                if is_created:
                    created += 1
                else:
                    skipped += 1
            except Exception as e:
                self.stdout.write(
                    self.style.WARNING(
                        f"Error backfilling account {account.id} ({account.name}): {str(e)}"
                    )
                )
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Opening backfill done: created={created}, skipped={skipped}, zero_balance={zero_balance}"
            )
        )
