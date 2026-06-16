import decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0058_deletionrequestauditlog'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Extend JournalEntry.source_type to accommodate 'CAPITAL_EVENT' (already ≤30 chars)
        migrations.AlterField(
            model_name='journalentry',
            name='source_type',
            field=models.CharField(
                choices=[
                    ('EXPENSE', 'Expense'),
                    ('INCOME', 'Income'),
                    ('TRANSFER', 'Transfer'),
                    ('LOAN_REPAYMENT', 'Loan Repayment'),
                    ('GOAL_CONTRIBUTION', 'Goal Contribution'),
                    ('ADJUSTMENT', 'Adjustment'),
                    ('CAPITAL_EVENT', 'Capital Event'),
                ],
                max_length=30,
            ),
        ),
        migrations.CreateModel(
            name='CapitalEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=15, verbose_name='Amount')),
                ('date', models.DateField(verbose_name='Date')),
                ('subtype', models.CharField(
                    choices=[
                        ('loan_down_payment', 'Loan Down Payment'),
                        ('loan_prepayment', 'Loan Prepayment'),
                        ('large_purchase', 'Large Purchase'),
                        ('medical_lump_sum', 'Medical Lump Sum'),
                        ('gift_given', 'Gift Given'),
                        ('gift_received', 'Gift Received'),
                        ('investment_lump_sum', 'Investment Lump Sum'),
                        ('other', 'Other'),
                    ],
                    default='other',
                    max_length=30,
                    verbose_name='Subtype',
                )),
                ('note', models.TextField(blank=True, default='', verbose_name='Note')),
                ('currency', models.CharField(
                    choices=[
                        ('₹', 'Indian Rupee (₹)'),
                        ('$', 'US Dollar ($)'),
                        ('€', 'Euro (€)'),
                        ('£', 'Pound Sterling (£)'),
                        ('¥', 'Japanese Yen (¥)'),
                        ('A$', 'Australian Dollar (A$)'),
                        ('C$', 'Canadian Dollar (C$)'),
                        ('CHF', 'Swiss Franc (CHF)'),
                        ('元', 'Chinese Yuan (元)'),
                        ('₩', 'South Korean Won (₩)'),
                    ],
                    default='₹',
                    max_length=5,
                    verbose_name='Currency',
                )),
                ('exchange_rate', models.DecimalField(
                    decimal_places=6, default=decimal.Decimal('1.0'), max_digits=15, verbose_name='Exchange Rate',
                )),
                ('base_amount', models.DecimalField(
                    decimal_places=2, default=decimal.Decimal('0.00'), max_digits=15, verbose_name='Amount in Base Currency',
                )),
                ('exclude_from_averages', models.BooleanField(default=True, verbose_name='Exclude from Averages & Trends')),
                ('exclude_from_budget', models.BooleanField(default=True, verbose_name='Exclude from Budget')),
                ('include_in_net_worth', models.BooleanField(default=True, verbose_name='Include in Cash Flow / Net Worth')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='capital_events',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('account', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='capital_events',
                    to='expenses.account',
                    verbose_name='Account',
                )),
                ('linked_loan', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='capital_events',
                    to='expenses.loan',
                    verbose_name='Linked Loan',
                )),
            ],
            options={
                'ordering': ['-date'],
                'indexes': [
                    models.Index(fields=['user', 'date'], name='expenses_ca_user_id_date_idx'),
                    models.Index(fields=['user', 'subtype'], name='expenses_ca_user_id_subtype_idx'),
                    models.Index(fields=['linked_loan'], name='expenses_ca_linked_loan_idx'),
                ],
            },
        ),
    ]
