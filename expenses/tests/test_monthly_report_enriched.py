from decimal import Decimal
from datetime import date
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from expenses.models import Account, UserProfile, Holding, Valuation, Expense, CapitalEvent
from expenses.management.commands.send_monthly_report import Command

User = get_user_model()


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True
)
class MonthlyReportEnrichedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='reportuser', password='password', email='user@example.com')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': 'INR'})

        # Investment Account (MUTUAL_FUND worth ₹30,000)
        self.mf_acc = Account.objects.create(
            user=self.user,
            name='Mutual Funds',
            account_type='MUTUAL_FUND',
            balance=Decimal('0.00'),
            currency='INR'
        )
        self.holding = Holding.objects.create(
            account=self.mf_acc,
            instrument_name='Nifty 50 Index',
            units=Decimal('100.00'),
            avg_cost=Decimal('200.00'),
            currency='INR',
            is_active=True
        )
        Valuation.objects.create(
            holding=self.holding,
            as_of_date='2026-01-15',
            value=Decimal('30000.00')
        )

        # Credit Card Account (owed ₹4,000)
        self.cc_acc = Account.objects.create(
            user=self.user,
            name='Credit Card',
            account_type='CREDIT_CARD',
            balance=Decimal('-4000.00'),
            currency='INR'
        )

        # Expense in Jan 2026
        Expense.objects.create(
            user=self.user,
            amount=Decimal('5000.00'),
            base_amount=Decimal('5000.00'),
            category='Groceries',
            date=date(2026, 1, 15)
        )

        # Capital Event in Jan 2026
        CapitalEvent.objects.create(
            user=self.user,
            subtype='LAPTOP_PURCHASE',
            note='MacBook Pro M3',
            amount=Decimal('150000.00'),
            base_amount=Decimal('150000.00'),
            date=date(2026, 1, 20),
            is_deleted=False,
            exclude_from_averages=False
        )

    def test_get_report_data_enriched_fields(self):
        cmd = Command()
        start_date = date(2026, 1, 1)
        end_date = date(2026, 1, 31)

        data = cmd.get_report_data(self.user, start_date, end_date)

        self.assertTrue(data['has_data'])
        self.assertEqual(data['total_investments'], Decimal('30000.00'))
        self.assertEqual(data['credit_card_pending'], Decimal('4000.00'))

        # Top categories should include percentage key
        self.assertTrue(len(data['top_categories']) > 0)
        self.assertIn('pct', data['top_categories'][0])

        # Capital events should be populated
        self.assertEqual(len(data['capital_events']), 1)
        self.assertEqual(data['capital_events'][0]['amount'], Decimal('150000.00'))
        self.assertIn('MacBook Pro M3', data['capital_events'][0]['label'])
