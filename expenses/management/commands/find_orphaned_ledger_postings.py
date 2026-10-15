from django.apps import apps
from django.core.management.base import BaseCommand

from expenses.models import JournalEntry


class Command(BaseCommand):
    help = 'Find POSTED ledger entries whose source_id no longer exists'

    def handle(self, *args, **options):
        # We need to map SOURCE_TYPE to the actual Model
        source_model_map = {
            'EXPENSE': 'Expense',
            'INCOME': 'Income',
            'TRANSFER': 'Transfer',
            'LOAN_REPAYMENT': 'LoanRepayment',
            'GOAL_CONTRIBUTION': 'GoalContribution',
            'CAPITAL_EVENT': 'CapitalEvent',
            'ADJUSTMENT': 'Account', # Adjustments are tied to an Account ID
        }

        # A deleted transaction normally leaves two entries (the posting and its reversal) that cancel out, so
        # a missing source alone proves nothing. What matters is a deleted source whose entries do NOT cancel:
        # that is money the ledger still holds for something that no longer exists.
        from collections import defaultdict
        from decimal import Decimal

        from expenses.models import JournalLine

        orphans_found = 0

        for source_type, model_name in source_model_map.items():
            Model = apps.get_model('expenses', model_name)

            entries = JournalEntry.objects.filter(source_type=source_type, status='POSTED')
            if not entries.exists():
                continue

            valid_ids = set(Model.objects.values_list('id', flat=True))
            missing_ids = set(entries.values_list('source_id', flat=True).distinct()) - valid_ids
            if not missing_ids:
                continue

            net = defaultdict(lambda: defaultdict(lambda: Decimal('0.00')))
            lines = JournalLine.objects.filter(
                journal_entry__source_type=source_type, journal_entry__source_id__in=missing_ids,
                journal_entry__status='POSTED',
            ).values('journal_entry__source_id', 'ledger_account_id', 'direction', 'amount')
            for line in lines:
                sign = 1 if line['direction'] == 'DEBIT' else -1
                net[line['journal_entry__source_id']][line['ledger_account_id']] += sign * line['amount']

            for source_id in sorted(missing_ids):
                leftovers = {acc: amt for acc, amt in net[source_id].items() if amt != 0}
                if leftovers:
                    orphans_found += 1
                    self.stdout.write(self.style.WARNING(
                        f"Orphan found: {source_type}:{source_id} no longer exists but its entries leave "
                        f"{sum(abs(a) for a in leftovers.values()) / 2} in the ledger"))

        if orphans_found == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned ledger postings found."))
        else:
            self.stdout.write(self.style.ERROR(f"Found {orphans_found} orphaned ledger postings."))
