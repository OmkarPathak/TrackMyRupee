from decimal import Decimal

from django.db import migrations


def add_opening_transactions(apps, schema_editor):
    """Every holding that has units and a cost gets one opening purchase, so its units and average cost
    can be worked out from purchases from now on. Holdings without both are left alone."""
    Holding = apps.get_model('expenses', 'Holding')
    HoldingTransaction = apps.get_model('expenses', 'HoldingTransaction')
    rows = []
    holdings = Holding.objects.filter(units__isnull=False, avg_cost__isnull=False, units__gt=0)
    for h in holdings.exclude(transactions__isnull=False).iterator():
        rows.append(HoldingTransaction(
            holding_id=h.id, kind='BUY', date=h.created_at.date(), units=h.units, price=h.avg_cost,
            amount=(h.units * h.avg_cost).quantize(Decimal('0.01')), note='Opening position',
        ))
        if len(rows) >= 500:
            HoldingTransaction.objects.bulk_create(rows)
            rows = []
    if rows:
        HoldingTransaction.objects.bulk_create(rows)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0112_holding_transactions'),
    ]

    operations = [
        migrations.RunPython(add_opening_transactions, migrations.RunPython.noop),
    ]
