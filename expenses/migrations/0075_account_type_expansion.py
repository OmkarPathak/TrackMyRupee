# Generated manually 2026-07-14
#
# This migration is fully reversible and safe on existing production rows:
#   - AlterField(account_type max_length 20→32): safe Postgres column widening (no table rewrite)
#   - choices change is DB-no-op; validation is Django-only
#   - All new fields are nullable (default null) — existing rows are unaffected
#   - All new indexes add read performance; removing them (reverse) is safe

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0074_transfer_expenses_tr_user_id_828e0d_idx'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Widen account_type from max_length=20 to max_length=32
        #    Also updates the choices to the new grouped structure.
        #    On PostgreSQL this is a metadata-only operation (no table rewrite for VARCHAR widening).
        migrations.AlterField(
            model_name='account',
            name='account_type',
            field=models.CharField(
                default='BANK',
                max_length=32,
                verbose_name='Account Type',
                choices=[
                    ('Cash & Bank', (
                        ('CASH_WALLET', 'Cash Wallet'),
                        ('SAVINGS_ACCOUNT', 'Savings Account'),
                        ('SALARY_ACCOUNT', 'Salary Account'),
                        ('CURRENT_ACCOUNT', 'Current Account'),
                        ('DIGITAL_WALLET', 'Digital Wallet (Paytm, Amazon Pay, etc.)'),
                    )),
                    ('Fixed-Income', (
                        ('FD', 'Fixed Deposit (FD)'),
                        ('RD', 'Recurring Deposit (RD)'),
                        ('PPF', 'Public Provident Fund (PPF)'),
                        ('EPF', "Employees' Provident Fund (EPF)"),
                        ('VPF', 'Voluntary Provident Fund (VPF)'),
                        ('NPS', 'National Pension System (NPS)'),
                        ('SSY', 'Sukanya Samriddhi Yojana (SSY)'),
                        ('POST_OFFICE', 'Post Office Savings (SCSS, NSC, KVP)'),
                        ('APY', 'Atal Pension Yojana (APY)'),
                    )),
                    ('Investments', (
                        ('DEMAT', 'Demat Account (Direct Equity)'),
                        ('MUTUAL_FUND', 'Mutual Funds'),
                        ('ETF', 'Exchange Traded Funds (ETF)'),
                        ('SGB', 'Sovereign Gold Bonds (SGB)'),
                        ('ULIP', 'Unit Linked Insurance Plan (ULIP)'),
                        ('GOLD', 'Digital / Physical Gold'),
                    )),
                    ('Short-Term Credit', (
                        ('CREDIT_CARD', 'Credit Card'),
                        ('BNPL', 'Buy Now Pay Later (BNPL)'),
                        ('OVERDRAFT', 'Overdraft (OD) / Cash Credit (CC)'),
                    )),
                    ('Long-Term Loans', (
                        ('HOME_LOAN', 'Home Loan'),
                        ('VEHICLE_LOAN', 'Vehicle Loan'),
                        ('EDUCATION_LOAN', 'Education Loan'),
                        ('PERSONAL_LOAN', 'Personal Loan'),
                        ('BUSINESS_LOAN', 'Business Loan'),
                        ('LAP', 'Loan Against Property (LAP)'),
                        ('GOLD_LOAN', 'Gold Loan'),
                    )),
                    ('Physical Assets', (
                        ('REAL_ESTATE', 'Real Estate'),
                        ('VEHICLE', 'Vehicle'),
                        ('LIFE_INSURANCE', 'Traditional Life Insurance (Surrender Value)'),
                    )),
                    ('Legacy', (
                        ('CASH', 'Cash (legacy)'),
                        ('BANK', 'Bank Account (legacy)'),
                        ('CREDIT_CARD', 'Credit Card (legacy)'),
                        ('INVESTMENT', 'Investment (legacy)'),
                        ('FIXED_DEPOSIT', 'Fixed Deposit (legacy)'),
                        ('OTHER', 'Other (legacy)'),
                    )),
                ],
            ),
        ),

        # 2. Add nullable FK: Account → Loan  (for LOAN_OUTSTANDING strategy)
        migrations.AddField(
            model_name='account',
            name='linked_loan',
            field=models.ForeignKey(
                blank=True,
                help_text='Link to a Loan record for LOAN_OUTSTANDING valuation strategy.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_accounts',
                to='expenses.loan',
                verbose_name='Linked Loan',
            ),
        ),

        # 3. Add nullable FK: Account → PhysicalAsset  (for PHYSICAL_VALUATION / INSURANCE_SURRENDER)
        migrations.AddField(
            model_name='account',
            name='linked_physical_asset',
            field=models.ForeignKey(
                blank=True,
                help_text='Link to a PhysicalAsset for PHYSICAL_VALUATION / INSURANCE_SURRENDER strategy.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='linked_accounts',
                to='expenses.physicalasset',
                verbose_name='Linked Physical Asset',
            ),
        ),

        # 4. Optional DEPOSIT accrual fields (all nullable — existing rows unaffected)
        migrations.AddField(
            model_name='account',
            name='deposit_principal',
            field=models.DecimalField(
                blank=True, decimal_places=2, max_digits=15,
                null=True, verbose_name='Deposit Principal',
            ),
        ),
        migrations.AddField(
            model_name='account',
            name='deposit_rate',
            field=models.DecimalField(
                blank=True, decimal_places=4, max_digits=7,
                null=True, verbose_name='Annual Interest Rate (%)',
            ),
        ),
        migrations.AddField(
            model_name='account',
            name='deposit_start_date',
            field=models.DateField(blank=True, null=True, verbose_name='Deposit Start Date'),
        ),
        migrations.AddField(
            model_name='account',
            name='deposit_compounding',
            field=models.CharField(
                blank=True,
                choices=[
                    ('SIMPLE', 'Simple Interest'),
                    ('QUARTERLY', 'Quarterly Compounding'),
                    ('ANNUAL', 'Annual Compounding'),
                ],
                max_length=10,
                null=True,
                verbose_name='Compounding Frequency',
            ),
        ),

        # 5. New composite index: (user, account_type, is_active)
        migrations.AddIndex(
            model_name='account',
            index=models.Index(
                fields=['user', 'account_type', 'is_active'],
                name='acc_user_type_active_idx',
            ),
        ),

        # 6. Index for Holding: (account, is_active) — backs net-worth active-holdings filter
        migrations.AddIndex(
            model_name='holding',
            index=models.Index(
                fields=['account', 'is_active'],
                name='holding_account_active_idx',
            ),
        ),

        # 7. Index for Valuation: (holding, as_of_date) — backs DISTINCT ON holding_id query
        migrations.AddIndex(
            model_name='valuation',
            index=models.Index(
                fields=['holding', 'as_of_date'],
                name='val_holding_date_idx',
            ),
        ),

        # 8. Index for AssetValuation: (asset, as_of_date) — backs DISTINCT ON asset_id query
        migrations.AddIndex(
            model_name='assetvaluation',
            index=models.Index(
                fields=['asset', 'as_of_date'],
                name='assetval_asset_date_idx',
            ),
        ),
    ]
