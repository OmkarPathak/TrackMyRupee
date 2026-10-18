from django.core.management.base import BaseCommand

from finance_tracker import css_purge


class Command(BaseCommand):
    help = 'Rebuild the trimmed Bootstrap stylesheet (static/vendor/bootstrap/bootstrap.purged.min.css).'

    def handle(self, *args, **options):
        css = css_purge.build()
        css_purge.OUTPUT_CSS.write_text(css, encoding='utf-8')
        full = css_purge.SOURCE_CSS.stat().st_size
        self.stdout.write(f'bootstrap.purged.min.css: {len(css.encode()) // 1024} KB (from {full // 1024} KB)')
