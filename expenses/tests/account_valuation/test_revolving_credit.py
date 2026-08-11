from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.models import Account


class TestRevolvingCreditStrategy(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='test_credit_user', password='password')
        self.card_account = Account.objects.create(
            user=self.user,
            name='Amazon Pay ICICI Card',
            account_type='CREDIT_CARD',
            balance=Decimal('-4150.00'),  # negative ledger balance for liability
            credit_limit=Decimal('150000.00'),
            currency='₹',
        )

    def test_revolving_credit_baseline_is_none(self):
        """Test get_baseline returns None for REVOLVING_CREDIT strategy."""
        baseline = get_baseline(self.card_account)
        self.assertIsNone(baseline)

    def test_revolving_credit_current_preserves_negative_sign(self):
        """Test get_current preserves negative ledger balance as liability."""
        current = get_current(self.card_account)
        self.assertEqual(current, Decimal('-4150.00'))

    def test_revolving_credit_overpayment(self):
        """Test overpayment resulting in positive card balance is preserved."""
        card_overpaid = Account.objects.create(
            user=self.user,
            name='HDFC Credit Card (In Credit)',
            account_type='CREDIT_CARD',
            balance=Decimal('500.00'),
            currency='₹',
        )
        current = get_current(card_overpaid)
        self.assertEqual(current, Decimal('500.00'))

    def test_credit_limit_optional_field(self):
        """Test credit_limit is present on instance when set."""
        self.assertEqual(self.card_account.credit_limit, Decimal('150000.00'))
