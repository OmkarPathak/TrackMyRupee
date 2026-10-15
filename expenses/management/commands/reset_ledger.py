import json

from django.core import serializers
from django.core.serializers.json import DjangoJSONEncoder
from django.core.management.base import BaseCommand, CommandError
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
    help = (
        'Reset ledger tables (all users, or one with --user-id) and optionally re-baseline from account balances. '
        'A dry run unless --commit is given; --commit needs --backup FILE (or an explicit --no-backup).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--commit', action='store_true', help='Execute the wipe')
        parser.add_argument('--user-id', type=int, help='Reset only this user (default: everyone)')
        parser.add_argument('--backup', type=str, help='Path to save a JSON backup of what is wiped')
        parser.add_argument('--no-backup', action='store_true', help='Wipe without a backup (not recommended)')
        parser.add_argument('--rebaseline', action='store_true', help='Re-post opening balances from account.balance')
        parser.add_argument('--yes', action='store_true', help='Skip confirmation prompt')

    def scoped(self, user_id):
        """The querysets to wipe, children before parents. LedgerPostingFailure has no user, so it is only
        cleared on a full reset."""
        if user_id:
            querysets = [
                JournalLine.objects.filter(journal_entry__user_id=user_id),
                JournalEntry.objects.filter(user_id=user_id),
                LedgerAccount.objects.filter(user_id=user_id),
                LedgerReconciliationReport.objects.filter(user_id=user_id),
            ]
        else:
            querysets = [
                JournalLine.objects.all(),
                JournalEntry.objects.all(),
                LedgerAccount.objects.all(),
                LedgerPostingFailure.objects.all(),
                LedgerReconciliationReport.objects.all(),
            ]
        return querysets

    def handle(self, *args, **options):
        commit = options['commit']
        backup_path = options['backup']
        rebaseline = options['rebaseline']
        user_id = options.get('user_id')

        if commit and not backup_path and not options['no_backup']:
            raise CommandError("Refusing to wipe the ledger without a backup: pass --backup FILE "
                               "(or --no-backup if you really mean it).")

        if commit and not options['yes']:
            scope = f"user {user_id}" if user_id else "ALL users"
            confirm = input(f"This will WIPE the ledger of {scope}. Are you sure? Type 'yes' to continue: ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.WARNING("Aborted."))
                return

        querysets = self.scoped(user_id)

        if backup_path and commit:
            self.stdout.write(f"Backing up ledger tables to {backup_path}...")
            with open(backup_path, 'w') as f:
                f.write('[')
                first = True
                for queryset in reversed(querysets):      # parents first, so it loads back in order
                    for chunk in serializers.serialize('python', queryset.iterator()):
                        f.write(('' if first else ',\n') + json.dumps(chunk, cls=DjangoJSONEncoder))
                        first = False
                f.write(']')
            self.stdout.write(self.style.SUCCESS("Backup complete."))

        with transaction.atomic():
            if commit:
                self.stdout.write(self.style.WARNING("WIPING ledger tables..."))
            else:
                self.stdout.write(self.style.SUCCESS("DRY RUN: Would WIPE the following counts:"))

            for queryset in querysets:
                name = queryset.model.__name__
                if commit:
                    deleted, _ = queryset.delete()
                    self.stdout.write(f"Deleted {deleted} rows from {name}")
                else:
                    self.stdout.write(f"Would delete {queryset.count()} rows from {name}")

            if rebaseline:
                accounts = Account.objects.all()
                if user_id:
                    accounts = accounts.filter(user_id=user_id)
                if commit:
                    self.stdout.write(self.style.SUCCESS("Re-baselining opening balances..."))
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
                    self.stdout.write(self.style.SUCCESS("DRY RUN: Would re-baseline opening balances for the accounts."))

        if not commit:
            self.stdout.write(self.style.WARNING("Run with --commit to apply changes."))
