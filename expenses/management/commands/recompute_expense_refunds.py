from decimal import Decimal

from django.core.management.base import BaseCommand

from expenses.models import Expense, Income
from expenses.refunds import recompute_expense_refunds


class Command(BaseCommand):
    help = (
        "Rebuild Expense.refunded_base (what has been refunded against each expense) from the income entries linked "
        "to it. Income keeps it up to date on its own; run this after changing refunds in bulk (the admin, SQL) or if "
        "the totals are ever in doubt. Shows what it would change unless --apply is given."
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Write the corrected totals.')

    def handle(self, *args, **options):
        linked = set(Income.objects.filter(refund_for_expense__isnull=False, is_deleted=False)
                     .values_list('refund_for_expense_id', flat=True))
        stale = set(Expense.objects.exclude(refunded_base=0).values_list('pk', flat=True))
        wanted = {}
        from django.db.models import Sum
        for pk, total in (Income.objects.filter(refund_for_expense_id__in=linked, is_deleted=False)
                          .order_by().values('refund_for_expense_id').annotate(t=Sum('base_amount'))
                          .values_list('refund_for_expense_id', 't')):
            wanted[pk] = total or Decimal('0')
        wrong = [pk for pk in linked | stale
                 if (Expense.objects.filter(pk=pk).values_list('refunded_base', flat=True).first() or Decimal('0'))
                 != wanted.get(pk, Decimal('0'))]
        if not wrong:
            self.stdout.write(self.style.SUCCESS('Every expense has the right refunded total.'))
            return
        for pk in sorted(wrong):
            have = Expense.objects.filter(pk=pk).values_list('refunded_base', flat=True).first()
            self.stdout.write(f'expense {pk}: stored {have} -> should be {wanted.get(pk, Decimal("0"))}')
        if options['apply']:
            recompute_expense_refunds(wrong)
            self.stdout.write(self.style.SUCCESS(f'Fixed {len(wrong)} expense(s).'))
        else:
            self.stdout.write(f'{len(wrong)} expense(s) differ. Run again with --apply to fix them.')
