from django.core.management.base import BaseCommand

from expenses.nav_provider import NAVFetchService


class Command(BaseCommand):
    help = "Sync latest NAV values for all schemes referenced by active Holdings."

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Bypass circuit-breaker backoff and force fetch for all active scheme codes.',
        )

    def handle(self, *args, **options):
        force = options.get('force', False)
        self.stdout.write("Starting active holdings NAV sync...")
        service = NAVFetchService()
        results = service.sync_active_holdings_navs(force=force)
        self.stdout.write(
            self.style.SUCCESS(
                f"NAV sync complete: total={results['total_schemes']}, "
                f"successful={results['successful']}, failed={results['failed']}, "
                f"skipped={results['skipped']}"
            )
        )
