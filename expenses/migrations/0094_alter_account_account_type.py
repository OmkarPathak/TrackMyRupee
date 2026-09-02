from django.db import migrations, models
import expenses.account_types


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0093_recurringtransaction_physical_asset_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='account',
            name='account_type',
            field=models.CharField(
                choices=expenses.account_types.ACCOUNT_TYPES,
                default='SAVINGS_ACCOUNT',
                max_length=32,
                verbose_name='Account Type',
            ),
        ),
    ]
