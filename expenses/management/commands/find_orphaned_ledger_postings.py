from django.core.management.base import BaseCommand
from expenses.models import JournalEntry
from django.apps import apps

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

        orphans_found = 0

        for source_type, model_name in source_model_map.items():
            Model = apps.get_model('expenses', model_name)
            
            # Find all POSTED entries for this source_type
            entries = JournalEntry.objects.filter(source_type=source_type, status='POSTED')
            if not entries.exists():
                continue

            # Check if source_id exists in Model
            # A more efficient way is to find entries whose source_id is not in Model's IDs
            valid_ids = set(Model.objects.values_list('id', flat=True))
            
            for entry in entries:
                if entry.source_id not in valid_ids:
                    self.stdout.write(self.style.WARNING(f"Orphan found: JournalEntry ID {entry.id} (Source: {source_type}:{entry.source_id})"))
                    orphans_found += 1

        if orphans_found == 0:
            self.stdout.write(self.style.SUCCESS("No orphaned ledger postings found."))
        else:
            self.stdout.write(self.style.ERROR(f"Found {orphans_found} orphaned ledger postings."))
