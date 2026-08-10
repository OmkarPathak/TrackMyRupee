from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, UserProfile


class BackwardCompatibilityRegressionTestCase(TestCase):
    """
    SPEC §9: Flag-off / no-new-data regression suite verifying that legacy accounts
    with no new fields populated produce output identical to pre-project behavior.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="testflagoff", password="password")
        profile = self.user.profile
        profile.currency = "₹"
        profile.save()

        # Create standard legacy accounts (BANK, CASH, CREDIT_CARD) without new fields
        self.bank = Account.objects.create(
            user=self.user,
            name="SBI Savings Account",
            account_type="BANK",
            currency="₹",
            balance=Decimal("150000.00"),
        )
        self.cash = Account.objects.create(
            user=self.user,
            name="Cash Wallet",
            account_type="CASH",
            currency="₹",
            balance=Decimal("12000.00"),
        )
        self.card = Account.objects.create(
            user=self.user,
            name="HDFC Credit Card",
            account_type="CREDIT_CARD",
            currency="₹",
            balance=Decimal("-25000.00"),
        )

    @override_settings(NET_WORTH_EXTENDED_MODELS_ENABLED=False)
    def test_flag_off_preserves_pre_project_output(self):
        """When feature flag NET_WORTH_EXTENDED_MODELS_ENABLED is False, get_net_worth matches pre-project calculation exactly."""
        net_worth, base_balances = LedgerReadService.get_net_worth(self.user)
        # Pre-project calculation summed all balances (150,000 + 12,000 - 25,000 = 137,000)
        self.assertEqual(net_worth, Decimal("137000.00"))
        self.assertEqual(base_balances[self.bank.pk], Decimal("150000.00"))
        self.assertEqual(base_balances[self.cash.pk], Decimal("12000.00"))
        self.assertEqual(base_balances[self.card.pk], Decimal("-25000.00"))

    @override_settings(NET_WORTH_EXTENDED_MODELS_ENABLED=True)
    def test_legacy_accounts_with_flag_on_produce_sensible_defaults(self):
        """When feature flag is True but no strategy-specific fields are set, calculation falls back to ledger balance."""
        net_worth, base_balances = LedgerReadService.get_net_worth(self.user)
        # Assets (150,000 + 12,000) - Liabilities (25,000) = 137,000
        self.assertEqual(net_worth, Decimal("137000.00"))
