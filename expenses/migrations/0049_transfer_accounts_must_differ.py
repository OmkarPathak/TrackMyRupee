from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0048_loanrepayment_base_amount_and_more'),
    ]

    operations = [
        migrations.AddConstraint(
            model_name='transfer',
            constraint=models.CheckConstraint(
                condition=~models.Q(from_account=models.F('to_account')),
                name='transfer_accounts_must_differ',
            ),
        ),
    ]
