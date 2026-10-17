from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0108_budget_suggestion_dismissal'),
    ]

    operations = [
        migrations.AddField(
            model_name='physicalasset',
            name='policy_type',
            field=models.CharField(
                choices=[
                    ('SAVINGS', 'Savings / endowment (pays back on maturity)'),
                    ('PROTECTION', 'Pure protection (term / health, no payout)'),
                ],
                default='SAVINGS', max_length=12, verbose_name='Policy Type',
                help_text='Savings policies count the premiums paid as their value; pure protection policies are worth nothing.',
            ),
        ),
    ]
