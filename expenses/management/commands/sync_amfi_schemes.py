from django.core.management.base import BaseCommand
from expenses.nav_provider import NAVFetchService


class Command(BaseCommand):
    help = "Populate or refresh the local AMFIScheme search mirror from AMFI NAVAll.txt."

    def handle(self, *args, **options):
        self.stdout.write("Downloading and parsing AMFI scheme list...")
        service = NAVFetchService()
        count = service.sync_amfi_scheme_list()
        self.stdout.write(
            self.style.SUCCESS(f"Successfully synced {count} AMFI schemes to local search table.")
        )
