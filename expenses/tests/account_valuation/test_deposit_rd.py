from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.models import Account, Transfer, UserProfile


class TestDepositRD(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='rduser', password='password')
        UserProfile.objects.get_or_create(user=self.user)
        self.bank_account = Account.objects.create(
            user=self.user, name='Savings Account', account_type='SAVINGS_ACCOUNT', balance=Decimal('200000.00')
        )

    def test_rd_worked_example_expected_schedule(self):
        """
        SPEC §2.1 worked example — RD: ₹5,000/month, 6.5% annual, quarterly compounding, 18 installments.
        Verify baseline = 90,000.00 and current is accurately computed via annuity formula.
        """
        start = date(2025, 1, 1)
        rd_account = Account.objects.create(
            user=self.user,
            name='ICICI RD',
            account_type='RD',
            currency='₹',
            balance=Decimal('90000.00'),
            deposit_rate=Decimal('6.50'),
            deposit_start_date=start,
            deposit_compounding='QUARTERLY',
            rd_installment_amount=Decimal('5000.00'),
            rd_installment_day=1,
            show_accrued_balance=True,
        )

        eval_today = date(2026, 6, 1)
        baseline = get_baseline(rd_account, today=eval_today)
        current = get_current(rd_account, today=eval_today)
        display = get_display_value(rd_account, today=eval_today)

        self.assertEqual(baseline, Decimal('90000.00')) # 18 * 5000
        self.assertGreater(current, Decimal('93000.00'))
        self.assertLess(current, Decimal('96000.00'))
        self.assertEqual(display, current)

    def test_rd_actual_transfers_and_missed_installment(self):
        """
        SPEC §2.1 Edge Case 8: user misses an installment (no Transfer posted that month) ->
        baseline/current must only count installments that actually posted.
        """
        start = date(2026, 1, 1)
        rd_account = Account.objects.create(
            user=self.user,
            name='Active RD',
            account_type='RD',
            currency='₹',
            balance=Decimal('0.00'),
            deposit_rate=Decimal('6.00'),
            deposit_start_date=start,
            deposit_compounding='QUARTERLY',
            rd_installment_amount=Decimal('5000.00'),
            rd_installment_day=5,
        )

        # Post 2 actual transfers out of 3 months (Jan & Mar posted, Feb skipped)
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=rd_account,
            amount=Decimal('5000.00'),
            date=date(2026, 1, 5),
            description='Jan RD'
        )
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=rd_account,
            amount=Decimal('5000.00'),
            date=date(2026, 3, 5),
            description='Mar RD (Feb skipped)'
        )

        eval_today = date(2026, 3, 31)
        baseline = get_baseline(rd_account, today=eval_today)
        current = get_current(rd_account, today=eval_today)

        # Only 2 transfers posted -> baseline is 10,000, NOT 15,000
        self.assertEqual(baseline, Decimal('10000.00'))
        self.assertGreater(current, Decimal('10000.00'))

    def test_rd_installment_day_clamping(self):
        """
        SPEC §2.1 Edge Case 7: rd_installment_day=31 in February -> clamp to last day of month without crash.
        """
        start = date(2026, 1, 31)
        rd_account = Account.objects.create(
            user=self.user,
            name='Late Day RD',
            account_type='RD',
            currency='₹',
            balance=Decimal('10000.00'),
            deposit_rate=Decimal('7.00'),
            deposit_start_date=start,
            deposit_compounding='SIMPLE',
            rd_installment_amount=Decimal('5000.00'),
            rd_installment_day=31,
        )

        eval_today = date(2026, 2, 28)
        current = get_current(rd_account, today=eval_today)
        # Should have 2 installments: Jan 31 and Feb 28 -> total > 10000
        self.assertGreater(current, Decimal('10000.00'))

    def test_rd_maturity_date_cap(self):
        """
        SPEC §2.1 Edge Case 3: deposit_maturity_date in past for RD -> accrual freezes at maturity.
        """
        start = date(2025, 1, 1)
        maturity = date(2025, 6, 1)
        rd_account = Account.objects.create(
            user=self.user,
            name='Matured RD',
            account_type='RD',
            currency='₹',
            balance=Decimal('30000.00'),
            deposit_rate=Decimal('8.00'),
            deposit_start_date=start,
            deposit_maturity_date=maturity,
            deposit_compounding='ANNUAL',
            rd_installment_amount=Decimal('5000.00'),
            rd_installment_day=1,
        )

        val_at_mat = get_current(rd_account, today=maturity)
        val_after_mat = get_current(rd_account, today=date(2026, 1, 1))

        self.assertEqual(val_at_mat, val_after_mat)

    def test_rd_pre_existing_account_with_first_transfer_today(self):
        """
        User scenario: RD started on 2026-01-01 (5k/mo). Evaluation date 2026-08-04.
        Prior to making any transfer, expected balance for Jan-Aug (8 months) is 40k + interest.
        When user posts a 5k transfer for Aug 4 today, Jan-Jul (prior to tracking) must
        preserve expected installments (35k), and Aug uses the 5k transfer -> baseline 40k.
        """
        start = date(2026, 1, 1)
        rd_account = Account.objects.create(
            user=self.user,
            name='Existing RD',
            account_type='RD',
            currency='₹',
            balance=Decimal('35000.00'),
            deposit_rate=Decimal('7.00'),
            deposit_start_date=start,
            deposit_compounding='QUARTERLY',
            rd_installment_amount=Decimal('5000.00'),
            rd_installment_day=1,
        )

        eval_today = date(2026, 8, 4)
        # Before transfer: 8 months (Jan-Aug) = 40,000 baseline
        baseline_before = get_baseline(rd_account, today=eval_today)
        self.assertEqual(baseline_before, Decimal('40000.00'))

        # Record transfer for Aug 4
        Transfer.objects.create(
            user=self.user,
            from_account=self.bank_account,
            to_account=rd_account,
            amount=Decimal('5000.00'),
            date=date(2026, 8, 4),
            description='Aug 2026 RD Transfer'
        )

        baseline_after = get_baseline(rd_account, today=eval_today)
        current_after = get_current(rd_account, today=eval_today)

        # Baseline must remain 40,000 (35k Jan-Jul expected + 5k Aug transfer)
        self.assertEqual(baseline_after, Decimal('40000.00'))
        self.assertGreater(current_after, Decimal('40000.00'))
