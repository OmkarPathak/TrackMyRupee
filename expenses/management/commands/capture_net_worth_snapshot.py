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
                    # get_net_worth returns (net_worth, account_base_balances)
                    net_worth, account_balances = LedgerReadService.get_net_worth(
                        user, as_of=today
                    )

                    # Compute proper asset/liability split.
                    # When NET_WORTH_EXTENDED_MODELS_ENABLED=True, get_net_worth returns the
                    # full breakdown, but the return signature is still (net_worth, balances).
                    # We derive assets/liabilities from per-account classification here.
                    from django.conf import settings
                    if getattr(settings, 'NET_WORTH_EXTENDED_MODELS_ENABLED', False):
                        from expenses.account_types import classify, KIND
                        accounts = list(user.accounts.filter(is_active=True))
                        total_assets = sum(
                            v for a in accounts
                            if classify(a.account_type)[0] == KIND.ASSET
                            and (v := account_balances.get(a.pk)) is not None
                        )
                        total_liabilities = sum(
                            abs(v) for a in accounts
                            if classify(a.account_type)[0] == KIND.LIABILITY
                            and (v := account_balances.get(a.pk)) is not None
                        )
                    else:
                        # Legacy heuristic (preserved for flag-off path)
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
