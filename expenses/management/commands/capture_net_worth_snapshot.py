from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.models import NetWorthSnapshot


class Command(BaseCommand):
    help = 'Capture daily net worth snapshots for all active users'

    def add_arguments(self, parser):
        parser.add_argument(
            '--chunk-size', type=int, default=100,
            help='Process users in batches of this size to avoid loading all users into memory.',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        today = timezone.now().date()
        snapshots_created = 0
        snapshots_failed = 0
        chunk_size = options.get('chunk_size', 100)

        # Process in chunks to avoid loading all users at once
        user_qs = User.objects.filter(is_active=True).order_by('id')
        offset = 0

        while True:
            chunk = list(user_qs[offset:offset + chunk_size])
            if not chunk:
                break
            offset += chunk_size

            for user in chunk:
                try:
                    # The engine reports the split, so assets - liabilities == net worth.
                    split = {}
                    net_worth, account_balances = LedgerReadService.get_net_worth(
                        user, as_of=today, detail=split
                    )
                    if split:
                        total_assets = split['assets']
                        total_liabilities = split['liabilities']
                    else:
                        # Flag-off path: the engine only knows the total.
                        total_assets = net_worth if net_worth > 0 else 0
                        total_liabilities = abs(net_worth) if net_worth < 0 else 0

                    # Convert Decimals to strings for JSON serialization
                    breakdown = {k: str(v) for k, v in account_balances.items()}

                    NetWorthSnapshot.objects.update_or_create(
                        user=user,
                        as_of_date=today,
                        defaults={
                            'total_net_worth': net_worth,
                            'total_assets': total_assets,
                            'total_liabilities': total_liabilities,
                            'breakdown': breakdown,
                        }
                    )
                    snapshots_created += 1
                except Exception as e:
                    snapshots_failed += 1
                    self.stdout.write(
                        self.style.ERROR(
                            f"Error capturing snapshot for user {user.username}: {e}"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully captured {snapshots_created} net worth snapshots for {today}. "
                f"Failed: {snapshots_failed}."
            )
        )
