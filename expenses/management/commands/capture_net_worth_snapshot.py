from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.models import NetWorthSnapshot


class Command(BaseCommand):
    help = 'Capture daily net worth snapshots for all active users'

    def handle(self, *args, **options):
        users = User.objects.filter(is_active=True)
        today = timezone.now().date()
        snapshots_created = 0

        for user in users:
            try:
                # get_net_worth returns net_worth, account_base_balances
                net_worth, account_balances = LedgerReadService.get_net_worth(user)
                
                # Convert Decimals to strings for JSON serialization
                account_balances = {k: str(v) for k, v in account_balances.items()}
                
                # Create snapshot
                # In a more complex scenario, we would compute assets vs liabilities explicitly.
                # For now, we store net_worth as total_assets and 0 as liabilities if positive,
                # or absolute net_worth as liabilities if negative (simplification).
                # Proper breakdown could be added later based on account types.
                total_assets = net_worth if net_worth > 0 else 0
                total_liabilities = abs(net_worth) if net_worth < 0 else 0

                NetWorthSnapshot.objects.update_or_create(
                    user=user,
                    as_of_date=today,
                    defaults={
                        'total_net_worth': net_worth,
                        'total_assets': total_assets,
                        'total_liabilities': total_liabilities,
                        'breakdown': account_balances
                    }
                )
                snapshots_created += 1
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error capturing snapshot for user {user.username}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Successfully captured {snapshots_created} net worth snapshots for {today}."))
