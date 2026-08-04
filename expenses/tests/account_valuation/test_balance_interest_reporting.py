from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import (
    get_baseline,
    get_current,
    get_display_value,
    get_interest_summary,
)
from expenses.models import Account, Category, Expense, Income, UserProfile


class TestBalanceInterestReporting(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='balanceuser', password='password')
        UserProfile.objects.get_or_create(user=self.user)
        self.account = Account.objects.create(
            user=self.user,
            name='Savings Account',
            account_type='SAVINGS_ACCOUNT',
            currency='₹',
            balance=Decimal('25000.00'),
            show_accrued_balance=True,
        )

    def test_balance_strategy_baseline_is_none(self):
        """
        SPEC §2.7: BALANCE strategy has no baseline concept (get_baseline returns None).
        get_display_value returns current value regardless of show_accrued_balance setting.
        """
        balance_types = [
            'CASH_WALLET', 'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT',
            'CURRENT_ACCOUNT', 'DIGITAL_WALLET', 'CASH', 'BANK', 'OTHER'
        ]
        for b_type in balance_types:
            acc = Account(user=self.user, account_type=b_type, balance=Decimal('1000.00'))
            self.assertIsNone(get_baseline(acc), f"Baseline for {b_type} should be None")
            self.assertEqual(get_current(acc), Decimal('1000.00'))
            self.assertEqual(get_display_value(acc), Decimal('1000.00'))

            acc.show_accrued_balance = False
            self.assertEqual(get_display_value(acc), Decimal('1000.00'))

    def test_interest_summary_report(self):
        """
        SPEC §3: get_interest_summary aggregates interest income and interest expenses DB-side.
        """
        interest_income_cat = Category.objects.create(
            user=self.user, name='Interest Received', is_interest_category=True
        )
        interest_expense_cat = Category.objects.create(
            user=self.user, name='Card Interest', is_interest_category=True
        )

        Income.objects.create(
            user=self.user,
            date=date(2026, 8, 1),
            amount=Decimal('340.00'),
            source='Bank Interest',
            source_fk=interest_income_cat,
            account=self.account,
            exchange_rate=Decimal('1.0'),
            base_amount=Decimal('340.00'),
        )
        Income.objects.create(
            user=self.user,
            date=date(2026, 8, 2),
            amount=Decimal('160.00'),
            source='FD Interest',
            source_fk=interest_income_cat,
            account=self.account,
            exchange_rate=Decimal('1.0'),
            base_amount=Decimal('160.00'),
        )

        Expense.objects.create(
            user=self.user,
            date=date(2026, 8, 3),
            amount=Decimal('150.00'),
            category='Interest Charged',
            category_fk=interest_expense_cat,
            exchange_rate=Decimal('1.0'),
            base_amount=Decimal('150.00'),
        )

        summary = get_interest_summary(self.user, start_date=date(2026, 8, 1), end_date=date(2026, 8, 5))
        self.assertEqual(summary['interest_earned'], Decimal('500.00'))
        self.assertEqual(summary['interest_charged'], Decimal('150.00'))
