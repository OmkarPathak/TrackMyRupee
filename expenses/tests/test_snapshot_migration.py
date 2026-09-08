from decimal import Decimal
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from expenses.models import Account, UserProfile, NetWorthSnapshot

User = get_user_model()


class NetWorthSnapshotMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='snapuser', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': 'INR'})

        # Asset account
        self.bank_acc = Account.objects.create(
            user=self.user,
            name='Bank Account',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('20000.00'),
            currency='INR'
        )

        # Liability account
        self.cc_acc = Account.objects.create(
            user=self.user,
            name='Credit Card',
            account_type='CREDIT_CARD',
            balance=Decimal('-5000.00'),
            currency='INR'
        )

    @override_settings(
        LEDGER_READ_ENABLED=True,
        LEDGER_WRITE_ENABLED=True,
        NET_WORTH_EXTENDED_MODELS_ENABLED=True
    )
    def test_snapshot_extended_flag_on(self):
        """Extended flag ON: total_assets=20000, total_liabilities=5000, total_net_worth=15000."""
        call_command('capture_net_worth_snapshot')
        today = timezone.now().date()
        snap = NetWorthSnapshot.objects.get(user=self.user, as_of_date=today)

        self.assertEqual(snap.total_net_worth, Decimal('15000.00'))
        self.assertEqual(snap.total_assets, Decimal('20000.00'))
        self.assertEqual(snap.total_liabilities, Decimal('5000.00'))

    @override_settings(
        LEDGER_READ_ENABLED=True,
        LEDGER_WRITE_ENABLED=True,
        NET_WORTH_EXTENDED_MODELS_ENABLED=False
    )
    def test_snapshot_extended_flag_off_legacy_heuristic(self):
        """Extended flag OFF: total_assets=15000, total_liabilities=0 (legacy heuristic)."""
        call_command('capture_net_worth_snapshot')
        today = timezone.now().date()
        snap = NetWorthSnapshot.objects.get(user=self.user, as_of_date=today)

        self.assertEqual(snap.total_net_worth, Decimal('15000.00'))
        self.assertEqual(snap.total_assets, Decimal('15000.00'))
        self.assertEqual(snap.total_liabilities, Decimal('0.00'))
