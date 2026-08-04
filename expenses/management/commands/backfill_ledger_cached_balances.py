from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Sum

from expenses.models import JournalLine, LedgerAccount


class Command(BaseCommand):
    help = "One-off backfill of cached_balance on LedgerAccount from existing posted JournalLines (SPEC §4)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Recompute cached_balance for all LedgerAccounts even if already set.',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)
        qs = LedgerAccount.objects.all()
        if not force:
            qs = qs.filter(cached_balance__isnull=True)

        updated_count = 0
        for ledger_account in qs:
            lines = JournalLine.objects.filter(
                ledger_account=ledger_account,
                journal_entry__status='POSTED',
            )
            debit = lines.filter(direction='DEBIT').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            credit = lines.filter(direction='CREDIT').aggregate(s=Sum('amount'))['s'] or Decimal('0.00')
            balance = (debit - credit).quantize(Decimal('0.01'))

            ledger_account.cached_balance = balance
            ledger_account.save(update_fields=['cached_balance'])
            updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(f"Successfully backfilled cached_balance for {updated_count} LedgerAccounts.")
        )
