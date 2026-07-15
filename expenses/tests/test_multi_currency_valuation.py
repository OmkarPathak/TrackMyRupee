"""
tests/test_multi_currency_valuation.py
======================================
Tests for currency conversion correctness, reversal value reuse, and historical snapshots.
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.fx import FXService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, FXRate, JournalEntry


def _make_user(username='testuser_fx'):
    user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@test.com'})
    try:
        user.profile.currency = '₹'
        user.profile.save(update_fields=['currency'])
    except Exception:
        pass
    return user


def _make_account(user, name, account_type='SAVINGS_ACCOUNT', balance=Decimal('0.00'), currency='₹'):
    acc = Account.objects.create(user=user, name=name, account_type=account_type, balance=balance, currency=currency)
    return acc


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestMultiCurrencyValuation(TestCase):

    def setUp(self):
        from unittest.mock import patch
        self.user = _make_user()
        
        # Setup get_exchange_rate patches to check DB first and avoid live calls
        symbol_to_code = {'₹': 'INR', '$': 'USD', '€': 'EUR', 'INR': 'INR', 'USD': 'USD', 'EUR': 'EUR'}
        def mock_get_rate(from_curr, to_curr):
            if from_curr == to_curr:
                return Decimal("1.0")
            from_code = symbol_to_code.get(from_curr, from_curr)
            to_code = symbol_to_code.get(to_curr, to_curr)
            if from_code == to_code:
                return Decimal("1.0")
            row = FXRate.objects.filter(from_currency=from_code, to_currency=to_code).order_by('-as_of_date', '-created_at').first()
            if row:
                return row.rate
            return Decimal("80.00")

        self.patches = [
            patch('expenses.models.get_exchange_rate', side_effect=mock_get_rate),
            patch('expenses.fx.get_exchange_rate', side_effect=mock_get_rate),
            patch('expenses.ledger_service.get_exchange_rate', side_effect=mock_get_rate),
            patch('expenses.ledger_read_service.get_exchange_rate', side_effect=mock_get_rate),
        ]
        for p in self.patches:
            p.start()

        # Ensure base rate INR -> INR is stored
        FXRate.objects.get_or_create(from_currency='INR', to_currency='INR', defaults={'rate': Decimal('1.0')})
        FXRate.objects.get_or_create(from_currency='₹', to_currency='₹', defaults={'rate': Decimal('1.0')})

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_multi_currency_net_worth(self):
        """Net worth computes correctly across INR, USD, and EUR accounts using stored FX rates."""
        from expenses.ledger_read_service import LedgerReadService

        # Store FX rates to INR
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=Decimal('83.50'), as_of_date=datetime.date.today())
        FXRate.objects.create(from_currency='EUR', to_currency='INR', rate=Decimal('90.00'), as_of_date=datetime.date.today())

        acc_inr = _make_account(self.user, 'INR Savings', currency='₹', balance=Decimal('10000.00'))
        acc_usd = _make_account(self.user, 'USD Savings', currency='$', balance=Decimal('100.00'))  # $ = USD
        acc_eur = _make_account(self.user, 'EUR Savings', currency='€', balance=Decimal('50.00'))   # € = EUR

        # Initialize with opening balances
        LedgerPostingService.post_opening_balance(account=acc_inr)
        LedgerPostingService.post_opening_balance(account=acc_usd)
        LedgerPostingService.post_opening_balance(account=acc_eur)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)

        # Expected base balance = 10000 + (100 * 83.50) + (50 * 90.00) = 10000 + 8350 + 4500 = 22850
        self.assertAlmostEqual(float(net_worth), 22850.0, places=2)

    def test_create_reverse_cross_currency_nets_zero(self):
        """Posting an expense in a currency other than base, and then deleting it, nets exactly to zero in both currencies."""
        from expenses.ledger_read_service import LedgerReadService
        from expenses.models import JournalLine, JournalEntry

        # Store FX rate USD -> INR (base currency is ₹/INR)
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=Decimal('80.00'), as_of_date=datetime.date.today())

        # Account is USD
        acc_usd = _make_account(self.user, 'USD Account', currency='$', balance=Decimal('1000.00'))
        LedgerPostingService.post_opening_balance(account=acc_usd)

        # 1. Post expense of $10.00
        expense = Expense.objects.create(
            user=self.user,
            account=acc_usd,
            amount=Decimal('10.00'),
            currency='$',
            date=datetime.date.today(),
            description="Coffee",
            category="Food",
        )

        # Retrieve the original posted journal entry and line for the expense
        orig_entry = JournalEntry.objects.get(source_type="EXPENSE", source_id=expense.id, metadata__shadow_action="CREATE")
        orig_line = orig_entry.lines.filter(account_ref=acc_usd).first()
        self.assertEqual(orig_line.fx_rate_to_base, Decimal('80.00'))
        self.assertEqual(orig_line.base_amount, Decimal('800.00'))

        # 2. Change the exchange rate in DB! (e.g. rate drops to 70.00)
        # Reversal MUST reuse the original rate (80.00) so that it nets to zero.
        FXRate.objects.filter(from_currency='USD', to_currency='INR').update(rate=Decimal('70.00'))

        # 3. Delete the expense (triggers reversal)
        expense_id = expense.id
        expense.delete()

        # Retrieve the reversal entry
        reversal_entry = JournalEntry.objects.get(source_type="EXPENSE", source_id=expense_id, metadata__shadow_action="DELETE_REVERSE")
        reversal_line = reversal_entry.lines.filter(account_ref=acc_usd).first()

        # Reversal line must reuse original rate (80.00) and base amount (800.00), not the new rate (70.00)
        self.assertEqual(reversal_line.fx_rate_to_base, Decimal('80.00'))
        self.assertEqual(reversal_line.base_amount, Decimal('800.00'))

        # 4. Assert that the ledger balance in USD is exactly 1000.00 (reversal cancelled it out)
        # and converted at the new rate (70.00) it results in 70000.00 base balance.
        _, final_balances = LedgerReadService.get_net_worth(self.user)
        final_usd_base = final_balances[acc_usd.pk]
        self.assertAlmostEqual(float(final_usd_base), 70000.0, places=2)

    def test_historical_snapshot_uses_as_of_rate(self):
        """Historical net-worth retrieval uses FX rates corresponding to the as_of date."""
        from expenses.ledger_read_service import LedgerReadService

        acc_usd = _make_account(self.user, 'USD Account', currency='$', balance=Decimal('100.00'))
        LedgerPostingService.post_opening_balance(account=acc_usd)

        # Rate on past date: 80.00
        past_date = datetime.date(2024, 1, 1)
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=Decimal('80.00'), as_of_date=past_date)

        # Rate today: 85.00
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=Decimal('85.00'), as_of_date=datetime.date.today())

        # Net worth today
        net_worth_today, _ = LedgerReadService.get_net_worth(self.user)
        self.assertAlmostEqual(float(net_worth_today), 8500.0, places=2)

        # Historical net worth
        net_worth_past, _ = LedgerReadService.get_net_worth(self.user, as_of=past_date)
        self.assertAlmostEqual(float(net_worth_past), 8000.0, places=2)
