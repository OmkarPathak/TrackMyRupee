from django.db import migrations, transaction


def backfill_next_due_date(apps, schema_editor):
    try:
        # A savepoint, so that a failed query here does not abort the whole migration transaction
        # on PostgreSQL (the fallback below could not run in an aborted transaction).
        with transaction.atomic():
            from expenses.models import RecurringTransaction
            for rt in RecurringTransaction.objects.filter(next_due_date__isnull=True):
                rt.save()
    except Exception:
        # Fallback for fresh test DB migration runs before future schema columns exist
        HistoricalRecurring = apps.get_model('expenses', 'RecurringTransaction')
        for rt in HistoricalRecurring.objects.filter(next_due_date__isnull=True):
            if hasattr(rt, 'start_date'):
                rt.next_due_date = rt.start_date
                rt.save()


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0089_alter_account_credit_card_billing_day'),
    ]

    operations = [
        migrations.RunPython(backfill_next_due_date, reverse_code=migrations.RunPython.noop),
    ]
