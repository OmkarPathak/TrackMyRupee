import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0132_emergency_access_expiry"),
    ]

    operations = [
        migrations.AddField(
            model_name="income",
            name="refund_for_capital_event",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="refunds",
                to="expenses.capitalevent",
                verbose_name="Refund for",
            ),
        ),
    ]
