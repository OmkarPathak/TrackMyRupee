import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0133_income_refund_for_capital_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="expense",
            name="refunded_base",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                editable=False,
                max_digits=15,
                verbose_name="Refunded (base currency)",
            ),
        ),
        migrations.AddField(
            model_name="income",
            name="refund_for_expense",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="refunds",
                to="expenses.expense",
                verbose_name="Refund for expense",
            ),
        ),
    ]
