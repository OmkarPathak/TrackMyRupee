
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction

from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerPostingFailure,
    LedgerReconciliationReport,
)


class Command(BaseCommand):
    help = 'Reset ledger tables and optionally re-baseline from account balances'

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Execute the wipe')
        parser.add_argument('--backup', type=str, help='Path to save JSON backup before wipe')
        parser.add_argument('--rebaseline', action='store_true', help='Re-post opening balances from account.balance')
        parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    def handle(self, *args, **options):
        commit = options['commit']
        backup_path = options['backup']
        rebaseline = options['rebaseline']
        skip_confirm = options['yes']

        if commit and not skip_confirm:
            confirm = input("This will WIPE all ledger data. Are you sure? Type 'yes' to continue: ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        # Order matters to respect FKs without cascade issues if any
        models_to_wipe = [
            JournalLine,
            JournalEntry,
            LedgerAccount,
            LedgerPostingFailure,
            LedgerReconciliationReport,
        ]

        if backup_path and commit:
            self.stdout.write(f"Backing up ledger tables to {backup_path}...")
            with open(backup_path, 'w') as f:
                call_command(
                    'dumpdata',
                    'expenses.JournalLine',
                    'expenses.JournalEntry',
                    'expenses.LedgerAccount',
                    'expenses.LedgerPostingFailure',
                    'expenses.LedgerReconciliationReport',
                    stdout=f,
                    indent=2
                )
            self.stdout.write(self.style.SUCCESS("Backup complete."))

        with transaction.atomic():
            if commit:
                self.stdout.write(self.style.WARNING("WIPING ledger tables..."))
            else:
                self.stdout.write(self.style.SUCCESS("DRY RUN: Would WIPE the following counts:"))

            for model in models_to_wipe:
                count = model.objects.count()
                if commit:
                    # model.objects.all().delete() might fail if other models point here, 
                    # but we are wiping the dependents first (JournalLine -> JournalEntry, etc)
                    deleted, _ = model.objects.all().delete()
                    self.stdout.write(f"Deleted {deleted} rows from {model.__name__}")
                else:
                    self.stdout.write(f"Would delete {count} rows from {model.__name__}")

            if rebaseline:
                if commit:
                    self.stdout.write(self.style.SUCCESS("Re-baselining opening balances..."))
                    accounts = Account.objects.all()
                    created_count = 0
                    skipped_count = 0
                    for account in accounts:
                        entry, created = LedgerPostingService.post_opening_balance(account=account)
                        if created:
                            created_count += 1
                        else:
                            skipped_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Re-baselined {created_count} accounts. Skipped {skipped_count}."))
                else:
                    self.stdout.write(self.style.SUCCESS("DRY RUN: Would re-baseline opening balances for all accounts."))
        
        if not commit:
            self.stdout.write(self.style.WARNING("Run with --commit to apply changes."))
