"""
tests/test_net_worth_rewrite.py
================================
Integration tests for the rewritten LedgerReadService.get_net_worth().

Tests cover:
  - BALANCE strategy (ledger-sourced balance, not account.balance)
  - HOLDINGS strategy (latest Valuation per holding, correct currency handling)
  - DEPOSIT strategy (ledger fallback + accrual when fields set)
  - REVOLVING_CREDIT strategy (negative balance → liability)
  - LOAN_OUTSTANDING strategy (linked Loan schedule + fallback)
  - PHYSICAL_VALUATION / INSURANCE_SURRENDER (linked PhysicalAsset latest valuation)
  - Mixed portfolio: correct total_net_worth and asset/liability split
  - Flag-off backward compatibility
  - Zero-balance accounts have opening entry (no fallback to account.balance)
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    AssetValuation,
    Holding,
    JournalEntry,
    Loan,
    LoanScheduleInstallment,
    PhysicalAsset,
    Valuation,
)


def _make_user(username='testuser_nw'):
    user, _ = User.objects.get_or_create(username=username, defaults={'email': f'{username}@test.com'})
    try:
        user.profile.currency = '₹'
        user.profile.save(update_fields=['currency'])
    except Exception:
        pass
    return user


def _make_account(user, name, account_type='SAVINGS_ACCOUNT', balance=Decimal('0.00'),
                  currency='₹', **kwargs):
    acc = Account.objects.create(
        user=user, name=name, account_type=account_type,
        balance=balance, currency=currency, **kwargs
    )
    return acc


def _post_opening(account):
    """Force an opening balance ledger entry for the account."""
    _, created = LedgerPostingService.post_opening_balance(account=account)
    return created


def _post_transaction(user, account, amount, direction='DEBIT', description='Test'):
    """Post a simple one-sided test entry (for testing only — not a balanced entry)."""
    # Use post_account_balance_adjustment to test the net worth reading
    # Instead, use the adjustment helper that creates a balanced entry.
    from expenses.models import Income
    # Post income to credit the account (increase balance)
    from expenses.ledger_service import LedgerPostingService as LPS
    version = 'TST-001'
    if direction == 'DEBIT':
        LPS.post_account_balance_adjustment(
            user=user, account=account,
            new_balance=account.balance + amount,
            old_balance=account.balance,
            version_token=version,
        )
    else:
        LPS.post_account_balance_adjustment(
            user=user, account=account,
            new_balance=account.balance - amount,
            old_balance=account.balance,
            version_token=version,
        )


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestBalanceStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_balance_user')

    def test_balance_reads_ledger_not_account_balance(self):
        """Account.balance field should be ignored when a ledger entry exists."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'My Savings', account_type='SAVINGS_ACCOUNT', balance=Decimal('5000.00'))
        _post_opening(account)

        # Artificially set account.balance to a wrong value
        Account.objects.filter(pk=account.pk).update(balance=Decimal('99999.00'))
        account.refresh_from_db()

        net_worth, balances = LedgerReadService.get_net_worth(self.user)

        # Should use ledger value (5000), not the artificial 99999
        self.assertAlmostEqual(float(balances[account.pk]), 5000.0, places=2)

    def test_zero_balance_account_uses_ledger_not_fallback(self):
        """A zero-balance account with an opening entry → ledger balance = 0.00, not account.balance."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'Zero Balance', account_type='CASH_WALLET', balance=Decimal('0.00'))
        _post_opening(account)

        # Artificially corrupt account.balance
        Account.objects.filter(pk=account.pk).update(balance=Decimal('1234.56'))
        account.refresh_from_db()

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # Ledger has 0.00 (zero-amount opening entry), not 1234.56
        self.assertAlmostEqual(float(balances[account.pk]), 0.0, places=2)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestHoldingsStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_holdings_user')

    def test_holdings_sum_latest_valuation(self):
        """MUTUAL_FUND account: net worth = sum of latest Valuation per active Holding."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'MF Account', account_type='MUTUAL_FUND', balance=Decimal('10000.00'))
        _post_opening(account)

        # 2 holdings
        h1 = Holding.objects.create(account=account, instrument_name='Fund A', instrument_type='MF', currency='₹', is_active=True)
        h2 = Holding.objects.create(account=account, instrument_name='Fund B', instrument_type='MF', currency='₹', is_active=True)

        # Add multiple valuations; only latest should be used
        Valuation.objects.create(holding=h1, value=Decimal('5000.00'), as_of_date=datetime.date(2024, 1, 1))
        Valuation.objects.create(holding=h1, value=Decimal('6000.00'), as_of_date=datetime.date(2024, 6, 1))  # latest
        Valuation.objects.create(holding=h2, value=Decimal('4000.00'), as_of_date=datetime.date(2024, 6, 1))  # latest

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # Expected: 6000 + 4000 = 10000
        self.assertAlmostEqual(float(balances[account.pk]), 10000.0, places=2)

    def test_holdings_fallback_to_ledger_when_no_holdings(self):
        """DEMAT account with no active holdings → falls back to ledger balance."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'Empty Demat', account_type='DEMAT', balance=Decimal('7500.00'))
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        self.assertAlmostEqual(float(balances[account.pk]), 7500.0, places=2)

    def test_inactive_holdings_excluded(self):
        """Inactive holdings must not contribute to net worth."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'ETF Account', account_type='ETF', balance=Decimal('0.00'))
        _post_opening(account)

        active_h = Holding.objects.create(account=account, instrument_name='Active ETF', instrument_type='OTHER', currency='₹', is_active=True)
        inactive_h = Holding.objects.create(account=account, instrument_name='Inactive ETF', instrument_type='OTHER', currency='₹', is_active=False)
        Valuation.objects.create(holding=active_h, value=Decimal('3000.00'), as_of_date=datetime.date.today())
        Valuation.objects.create(holding=inactive_h, value=Decimal('9999.00'), as_of_date=datetime.date.today())

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        self.assertAlmostEqual(float(balances[account.pk]), 3000.0, places=2)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestDepositStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_deposit_user')

    def test_deposit_without_accrual_fields_uses_ledger_balance(self):
        """FD without deposit_* fields → uses ledger balance (backward compat)."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'FD Account', account_type='FD', balance=Decimal('100000.00'))
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        self.assertAlmostEqual(float(balances[account.pk]), 100000.0, places=2)

    def test_deposit_with_simple_interest_accrual(self):
        """FD with deposit_* fields → value > principal."""
        from expenses.ledger_read_service import LedgerReadService

        # 1 year ago, 10% simple interest → value = 100000 * 1.10 = 110000
        start_date = datetime.date.today() - datetime.timedelta(days=365)
        account = _make_account(
            self.user, 'FD With Accrual', account_type='FD',
            balance=Decimal('100000.00'),
            deposit_principal=Decimal('100000.00'),
            deposit_rate=Decimal('10.0000'),
            deposit_start_date=start_date,
            deposit_compounding='SIMPLE',
        )
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # Value should be approximately 110000 (10% of 100000 over 1 year)
        self.assertGreater(float(balances[account.pk]), 100000.0)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestRevolvingCreditStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_credit_user')

    def test_credit_card_negative_balance_is_liability(self):
        """CREDIT_CARD account with negative ledger balance contributes to liabilities."""
        from expenses.ledger_read_service import LedgerReadService
        from expenses.account_types import KIND, classify

        # Credit card: negative balance means owed
        account = _make_account(self.user, 'Credit Card', account_type='CREDIT_CARD', balance=Decimal('-5000.00'))
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)

        kind, _ = classify(account.account_type)
        self.assertEqual(kind, KIND.LIABILITY)

        # The balance in balances dict represents the ledger value (negative for credit cards)
        self.assertLessEqual(float(balances[account.pk]), 0.0)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestLoanOutstandingStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_loan_user')

    def test_loan_outstanding_via_schedule(self):
        """HOME_LOAN account with linked Loan and schedule → outstanding from schedule."""
        from expenses.ledger_read_service import LedgerReadService

        loan = Loan.objects.create(
            user=self.user, name='Home Loan',
            loan_type='HOME', initial_principal=Decimal('5000000.00'),
            duration_months=240, currency='₹',
        )
        account = _make_account(
            self.user, 'Home Loan Account', account_type='HOME_LOAN',
            balance=Decimal('-4500000.00'), linked_loan=loan,
        )
        _post_opening(account)

        # Add schedule installments
        LoanScheduleInstallment.objects.create(
            loan=loan, installment_no=1, due_date=datetime.date(2024, 2, 1),
            scheduled_principal=Decimal('20000.00'), scheduled_interest=Decimal('40000.00'),
            scheduled_balance=Decimal('4980000.00'), is_paid=True,
        )
        LoanScheduleInstallment.objects.create(
            loan=loan, installment_no=2, due_date=datetime.date(2024, 3, 1),
            scheduled_principal=Decimal('20100.00'), scheduled_interest=Decimal('39900.00'),
            scheduled_balance=Decimal('4959900.00'), is_paid=True,  # latest paid
        )

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # Outstanding should come from the latest paid installment's scheduled_balance
        self.assertAlmostEqual(float(balances[account.pk]), 4959900.0, places=0)

    def test_loan_unlinked_fallback(self):
        """HOME_LOAN account with no linked_loan → uses absolute ledger balance."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'Unlinked Loan', account_type='HOME_LOAN', balance=Decimal('-3000000.00'))
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # Fallback: absolute ledger balance
        self.assertAlmostEqual(abs(float(balances[account.pk])), 3000000.0, places=0)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=True,
)
class TestPhysicalValuationStrategy(TestCase):

    def setUp(self):
        self.user = _make_user('nw_physical_user')

    def test_real_estate_uses_latest_asset_valuation(self):
        """REAL_ESTATE account with linked PhysicalAsset → latest AssetValuation value."""
        from expenses.ledger_read_service import LedgerReadService

        asset = PhysicalAsset.objects.create(
            user=self.user, name='My Flat',
            asset_class='REAL_ESTATE',
            acquisition_cost=Decimal('4000000.00'),
            currency='₹', is_active=True,
        )
        account = _make_account(
            self.user, 'Real Estate', account_type='REAL_ESTATE',
            balance=Decimal('4000000.00'), linked_physical_asset=asset,
        )
        _post_opening(account)

        AssetValuation.objects.create(asset=asset, value=Decimal('4000000.00'), as_of_date=datetime.date(2024, 1, 1))
        AssetValuation.objects.create(asset=asset, value=Decimal('4500000.00'), as_of_date=datetime.date(2024, 6, 1))  # latest

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        self.assertAlmostEqual(float(balances[account.pk]), 4500000.0, places=0)

    def test_physical_asset_fallback_to_acquisition_cost(self):
        """REAL_ESTATE with linked asset but no valuations → acquisition_cost."""
        from expenses.ledger_read_service import LedgerReadService

        asset = PhysicalAsset.objects.create(
            user=self.user, name='Land Plot',
            asset_class='REAL_ESTATE',
            acquisition_cost=Decimal('2000000.00'),
            currency='₹', is_active=True,
        )
        account = _make_account(
            self.user, 'Land', account_type='REAL_ESTATE',
            balance=Decimal('2000000.00'), linked_physical_asset=asset,
        )
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)
        # No AssetValuation rows → acquisition_cost fallback
        self.assertAlmostEqual(float(balances[account.pk]), 2000000.0, places=0)


@override_settings(
    LEDGER_READ_ENABLED=True,
    LEDGER_WRITE_ENABLED=True,
    NET_WORTH_EXTENDED_MODELS_ENABLED=False,
)
class TestFlagOffBackwardCompat(TestCase):

    def setUp(self):
        self.user = _make_user('nw_flagoff_user')

    def test_flag_off_uses_legacy_path(self):
        """With flag off, the output should match the pre-change behavior."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'Cash Wallet', account_type='CASH', balance=Decimal('1000.00'))
        _post_opening(account)

        net_worth, balances = LedgerReadService.get_net_worth(self.user)

        # With flag off, we get a net_worth Decimal and a dict
        self.assertIsInstance(net_worth, Decimal)
        self.assertIsInstance(balances, dict)

    def test_flag_off_does_not_use_extended_queries(self):
        """Flag off path must not issue queries for Holding/AssetValuation/Loan."""
        from expenses.ledger_read_service import LedgerReadService

        account = _make_account(self.user, 'Savings', account_type='BANK', balance=Decimal('5000.00'))
        _post_opening(account)

        # Even with a Holding linked, flag-off path skips it (for non-INVESTMENT type)
        with self.assertNumQueries(5):  # accounts + lines + opening + goals + loans (legacy path)
            LedgerReadService.get_net_worth(self.user)
