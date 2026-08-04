from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.models import Account, UserProfile


class TestDepositFD(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fduser', password='password')
        UserProfile.objects.get_or_create(user=self.user)

    def test_fd_accrual_basic_quarterly(self):
        """
        Worked example — FD: ₹5,00,000 principal, 7.5% annual, quarterly compounding,
        500 days elapsed.
        years = 500 / 365.25 = 1.36892539
        value = 500000 * (1 + 0.075/4)^(4 * 1.36892539) = 552140.70
        """
        start = date(2025, 1, 1)
        account = Account.objects.create(
            user=self.user,
            name='ICICI FD 3yr',
            account_type='FD',
            currency='₹',
            balance=Decimal('500000.00'),
            deposit_principal=Decimal('500000.00'),
            deposit_rate=Decimal('7.50'),
            deposit_start_date=start,
            deposit_compounding='QUARTERLY',
            show_accrued_balance=True,
        )

        target_today = start + timedelta(days=500)
        baseline = get_baseline(account, today=target_today)
        current = get_current(account, today=target_today)
        display = get_display_value(account, today=target_today)

        self.assertEqual(baseline, Decimal('500000.00'))
        self.assertEqual(current, Decimal('553536.03'))
        self.assertEqual(display, Decimal('553536.03'))

        # Test toggle off (show_accrued_balance=False)
        account.show_accrued_balance = False
        account.save()
        self.assertEqual(get_display_value(account, today=target_today), Decimal('500000.00'))

    def test_fd_maturity_date_cap(self):
        """
        SPEC §2.1 Edge Case 3: deposit_maturity_date in past -> accrual must freeze at maturity date.
        """
        start = date(2025, 1, 1)
        maturity = start + timedelta(days=365)
        account = Account.objects.create(
            user=self.user,
            name='Matured FD',
            account_type='FD',
            currency='₹',
            balance=Decimal('100000.00'),
            deposit_principal=Decimal('100000.00'),
            deposit_rate=Decimal('10.00'),
            deposit_start_date=start,
            deposit_maturity_date=maturity,
            deposit_compounding='SIMPLE',
        )

        # Accrual at maturity (365 days): 100000 * (1 + 0.10 * 365/365.25) = 109993.16
        val_at_maturity = get_current(account, today=maturity)
        self.assertEqual(val_at_maturity, Decimal('109993.16'))

        # 100 days after maturity -> must remain capped at 109993.16
        after_maturity = maturity + timedelta(days=100)
        val_after_maturity = get_current(account, today=after_maturity)
        self.assertEqual(val_after_maturity, Decimal('109993.16'))

    def test_fd_closed_date_cap(self):
        """
        SPEC §2.1 Edge Case 9: Premature closure (deposit_closed_date) -> caps accrual at closed date.
        """
        start = date(2025, 1, 1)
        closed = start + timedelta(days=180)
        account = Account.objects.create(
            user=self.user,
            name='Closed FD',
            account_type='FD',
            currency='₹',
            balance=Decimal('100000.00'),
            deposit_principal=Decimal('100000.00'),
            deposit_rate=Decimal('10.00'),
            deposit_start_date=start,
            deposit_closed_date=closed,
            deposit_compounding='SIMPLE',
        )

        after_closed = start + timedelta(days=365)
        val = get_current(account, today=after_closed)
        # Accrues for 180 days only: 100000 * (1 + 0.10 * (180/365.25)) = 104928.13
        self.assertEqual(val, Decimal('104928.13'))

    def test_fd_start_date_today_or_future(self):
        """
        SPEC §2.1 Edge Cases 1 & 2: Start date today or in future -> returns principal.
        """
        eval_today = date(2026, 8, 4)
        account_today = Account.objects.create(
            user=self.user,
            name='Today FD',
            account_type='FD',
            currency='₹',
            balance=Decimal('50000.00'),
            deposit_principal=Decimal('50000.00'),
            deposit_rate=Decimal('8.00'),
            deposit_start_date=eval_today,
        )
        account_future = Account.objects.create(
            user=self.user,
            name='Future FD',
            account_type='FD',
            currency='₹',
            balance=Decimal('50000.00'),
            deposit_principal=Decimal('50000.00'),
            deposit_rate=Decimal('8.00'),
            deposit_start_date=eval_today + timedelta(days=10),
        )

        self.assertEqual(get_current(account_today, today=eval_today), Decimal('50000.00'))
        self.assertEqual(get_current(account_future, today=eval_today), Decimal('50000.00'))

    def test_fd_null_fields_fallback(self):
        """
        SPEC §2.1 Edge Cases 4 & 6: Missing rate/start_date or zero principal -> fallback to ledger balance.
        """
        account_no_rate = Account.objects.create(
            user=self.user, name='No Rate', account_type='FD', currency='₹', balance=Decimal('1234.00')
        )
        account_zero_principal = Account.objects.create(
            user=self.user,
            name='Zero Principal',
            account_type='FD',
            currency='₹',
            balance=Decimal('500.00'),
            deposit_principal=Decimal('0.00'),
            deposit_rate=Decimal('7.00'),
            deposit_start_date=date(2025, 1, 1),
        )

        self.assertEqual(get_current(account_no_rate), Decimal('1234.00'))
        self.assertEqual(get_current(account_zero_principal), Decimal('500.00'))

    def test_backward_compatibility_no_maturity_date(self):
        """
        SPEC §9: Existing FD with no maturity date accrues as before.
        """
        start = date(2025, 1, 1)
        account = Account.objects.create(
            user=self.user,
            name='Legacy FD',
            account_type='FIXED_DEPOSIT',
            currency='₹',
            balance=Decimal('100000.00'),
            deposit_principal=Decimal('100000.00'),
            deposit_rate=Decimal('10.00'),
            deposit_start_date=start,
            deposit_compounding='ANNUAL',
        )

        eval_date = start + timedelta(days=730) # 2 years (730 days)
        # 100000 * (1 + 0.10)^(730/365.25) = 120984.21
        self.assertEqual(get_current(account, today=eval_date), Decimal('120984.21'))
