from django.core.management.base import BaseCommand

from expenses.models import Income


class Command(BaseCommand):
    help = 'Print all distinct source strings mapped to Other'

    def handle(self, *args, **options):
        sources = Income.objects.filter(source_type='Other').values_list('source', flat=True).distinct().order_by('source')
        if not sources:
            self.stdout.write(self.style.WARNING("No source strings mapped to 'Other' found."))
            return
            
        self.stdout.write(self.style.SUCCESS("Distinct source strings mapped to 'Other':"))
        for src in sources:
            if src:
                self.stdout.write(f"- {src}")
