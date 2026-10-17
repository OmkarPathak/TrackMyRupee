from django.db import migrations


def copy_fund_to_splits(apps, schema_editor):
    """A recurring transfer that named one fund becomes a split of its whole amount into that fund."""
    RecurringTransaction = apps.get_model('expenses', 'RecurringTransaction')
    RecurringFundSplit = apps.get_model('expenses', 'RecurringFundSplit')
    for rt in RecurringTransaction.objects.filter(fund__isnull=False).iterator():
        RecurringFundSplit.objects.get_or_create(recurring_id=rt.id, holding_id=rt.fund_id, defaults={'amount': rt.amount})


def copy_splits_back(apps, schema_editor):
    RecurringTransaction = apps.get_model('expenses', 'RecurringTransaction')
    RecurringFundSplit = apps.get_model('expenses', 'RecurringFundSplit')
    for split in RecurringFundSplit.objects.order_by('id'):
        RecurringTransaction.objects.filter(pk=split.recurring_id, fund__isnull=True).update(fund_id=split.holding_id)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0116_recurring_fund_split'),
    ]

    operations = [
        migrations.RunPython(copy_fund_to_splits, copy_splits_back),
    ]
