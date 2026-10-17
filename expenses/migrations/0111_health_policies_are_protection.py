from django.db import migrations


def mark_health_policies(apps, schema_editor):
    """Health cover pays nothing back. Policies created before policy_type existed defaulted to
    SAVINGS, which would count their premiums as an asset in net worth until the user edited them."""
    PhysicalAsset = apps.get_model('expenses', 'PhysicalAsset')
    Account = apps.get_model('expenses', 'Account')
    asset_ids = Account.objects.filter(
        account_type='HEALTH_INSURANCE', linked_physical_asset__isnull=False,
    ).values_list('linked_physical_asset_id', flat=True)
    PhysicalAsset.objects.filter(id__in=list(asset_ids), policy_type='SAVINGS').update(policy_type='PROTECTION')


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0110_link_recurring_premium_expenses'),
    ]

    operations = [
        migrations.RunPython(mark_health_policies, migrations.RunPython.noop),
    ]
