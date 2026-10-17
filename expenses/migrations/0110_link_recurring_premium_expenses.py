from django.db import migrations


def link_premium_expenses(apps, schema_editor):
    """Premiums posted by an insurance schedule were not tied to the policy; tie them so premiums paid add up."""
    RecurringTransaction = apps.get_model('expenses', 'RecurringTransaction')
    Expense = apps.get_model('expenses', 'Expense')
    schedules = RecurringTransaction.objects.filter(
        transaction_type='INSURANCE_PREMIUM', physical_asset__isnull=False,
    )
    for rt in schedules.iterator():
        Expense.objects.filter(
            user_id=rt.user_id, account_id=rt.account_id, amount=rt.amount,
            description=f"{rt.description} (Recurring)", linked_physical_asset__isnull=True,
        ).update(linked_physical_asset_id=rt.physical_asset_id)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0109_physicalasset_policy_type'),
    ]

    operations = [
        migrations.RunPython(link_premium_expenses, migrations.RunPython.noop),
    ]
