"""Follow-ups to family sharing: the cache after outside changes, owner protection, and copy that matches behaviour."""
from decimal import Decimal
from pathlib import Path

from django.db.models import ProtectedError
from django.test import override_settings

from expenses.family import shares, summary
from expenses.models import Account, Household

from .family_base import FamilyTestCase, add_account, make_family

D = Decimal
LEDGER = dict(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True,
              FAMILY_SUMMARY_CACHE_SECONDS=60)


@override_settings(**LEDGER)
class CacheAfterOutsideChangesTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)
        self.household = make_family(self.owner, self.partner)
        self.account = add_account(self.owner, 'Savings', balance='1000.00')
        shares.set_share(self.owner, self.account, 'AMOUNTS')
        self.household.refresh_from_db()

    def names(self):
        self.household.refresh_from_db()
        return [i.name for i in summary.family_summary(self.household, self.partner).items]

    def test_a_deactivated_account_leaves_the_family_view_at_once(self):
        self.assertEqual(self.names(), ['Savings'])
        self.account.is_active = False
        self.account.save()
        self.assertEqual(self.names(), [])

    def test_a_deleted_account_leaves_the_family_view_at_once(self):
        self.assertEqual(self.names(), ['Savings'])
        self.account.delete()
        self.assertEqual(self.names(), [])

    def test_an_ordinary_balance_save_does_not_touch_the_cache_version(self):
        before = Household.objects.get(pk=self.household.pk).cache_version
        self.account.balance = D('2000.00')
        self.account.save()
        self.assertEqual(Household.objects.get(pk=self.household.pk).cache_version, before)

    def test_an_account_nobody_shares_costs_no_bump(self):
        other = add_account(self.owner, 'Private')
        before = Household.objects.get(pk=self.household.pk).cache_version
        other.is_active = False
        other.save()
        self.assertEqual(Household.objects.get(pk=self.household.pk).cache_version, before)
        self.assertTrue(Account.objects.filter(pk=other.pk).exists())


class OwnerProtectionTests(FamilyTestCase):
    def test_deleting_an_owner_directly_is_refused_rather_than_ending_the_family(self):
        household = make_family(self.owner, self.partner)
        with self.assertRaises(ProtectedError):
            self.owner.delete()
        self.assertTrue(Household.objects.filter(pk=household.pk).exists())
        self.assertEqual(household.members.count(), 2)

    def test_the_account_deletion_flow_still_works_for_an_owner(self):
        from expenses.views.settings import delete_user_account
        household = make_family(self.owner, self.partner)
        self.assertTrue(delete_user_account(self.owner.pk))
        household.refresh_from_db()
        self.assertEqual(household.owner_id, self.partner.pk)


class PublicCopyTests(FamilyTestCase):
    def test_the_security_and_landing_pages_do_not_claim_the_emergency_sheet_is_only_ever_on_request(self):
        root = Path(__file__).resolve().parents[2] / 'templates'
        security = (root / 'security.html').read_text(encoding='utf-8')
        landing = (root / 'landing.html').read_text(encoding='utf-8')
        self.assertNotIn('is opened only on request', security)
        self.assertIn('By default it opens only on request', security)
        self.assertNotIn('opened on request, with a delay and a veto for you', landing)
        self.assertIn('By default it opens on request', landing)
