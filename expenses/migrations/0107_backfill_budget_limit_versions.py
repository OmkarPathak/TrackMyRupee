"""Backfill one BudgetLimitVersion per existing Category.limit.

effective_from is the 1st of the month of the user's earliest expense (fallback: the current
month), so every past month that has data keeps showing the limit it shows today. Data only; the
tables are created in 0106 so no DDL lock is held while rows are copied. Batched by category id.
Re-runnable: ignore_conflicts leaves existing versions alone. Reverse is a no-op (the tables are
dropped when 0106 is reversed).
"""

from django.db import migrations
from django.db.models import Min
from django.utils import timezone

BATCH = 500


def backfill(apps, schema_editor):
    Category = apps.get_model('expenses', 'Category')
    Expense = apps.get_model('expenses', 'Expense')
    Version = apps.get_model('expenses', 'BudgetLimitVersion')

    fallback = timezone.localdate().replace(day=1)
    last_id = 0
    while True:
        cats = list(
            Category.objects.filter(id__gt=last_id, limit__isnull=False, limit__gt=0)
            .order_by('id')[:BATCH]
        )
        if not cats:
            break
        last_id = cats[-1].id
        user_ids = {c.user_id for c in cats}
        earliest = {
            row['user_id']: row['first']
            for row in Expense.objects.filter(user_id__in=user_ids)
            .values('user_id').annotate(first=Min('date'))
        }
        rows = []
        for c in cats:
            first = earliest.get(c.user_id)
            start = first.replace(day=1) if first else fallback
            rows.append(Version(
                user_id=c.user_id,
                category_id=c.id,
                category_name=(c.name or '').strip().lower(),
                amount=c.limit,
                effective_from=start,
            ))
        Version.objects.bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0106_budget_limit_versions'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
