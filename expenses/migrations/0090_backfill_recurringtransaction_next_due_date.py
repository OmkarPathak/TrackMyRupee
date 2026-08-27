from django.db import migrations


def backfill_next_due_date(apps, schema_editor):
    # Import the real model so its custom .save() method runs _calculate_next_due_date()
    from expenses.models import RecurringTransaction
    for rt in RecurringTransaction.objects.filter(next_due_date__isnull=True):
        rt.save()


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0089_alter_account_credit_card_billing_day'),
    ]

    operations = [
        migrations.RunPython(backfill_next_due_date, reverse_code=migrations.RunPython.noop),
    ]
