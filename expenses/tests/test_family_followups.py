"""Follow-ups to family sharing: the cache after outside changes, owner protection, and copy that matches behaviour."""
from decimal import Decimal
from pathlib import Path

from django.db.models import ProtectedError
from django.test import override_settings
from django.utils import timezone

from expenses.family import shares, summary
from expenses.models import Account, FamilyShare, Household

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


@override_settings(**dict(LEDGER, FAMILY_SUMMARY_CACHE_SECONDS=0))
class HistoryStartPointTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner)

    def data(self):
        self.household.refresh_from_db()
        return summary.family_summary(self.household, self.partner)

    def test_the_value_on_the_day_amounts_are_shared_is_recorded(self):
        account = add_account(self.owner, 'Savings', balance='50000.00')
        shares.set_share(self.owner, account, 'AMOUNTS')
        self.assertEqual(FamilyShare.objects.get().amounts_start_value, D('50000.00'))

    def test_a_list_only_share_records_nothing_until_amounts_are_shown(self):
        account = add_account(self.owner, 'Savings', balance='50000.00')
        shares.set_share(self.owner, account, 'LIST')
        self.assertIsNone(FamilyShare.objects.get().amounts_start_value)
        Account.objects.filter(pk=account.pk).update(balance=D('52000.00'))
        shares.set_share(self.owner, account, 'AMOUNTS')
        self.assertEqual(FamilyShare.objects.get().amounts_start_value, D('52000.00'))

    def test_what_is_owed_is_recorded_as_negative(self):
        card = add_account(self.owner, 'Card', account_type='CREDIT_CARD', balance='-4000.00')
        shares.set_share(self.owner, card, 'AMOUNTS')
        self.assertEqual(FamilyShare.objects.get().amounts_start_value, D('-4000.00'))

    def test_sharing_a_whole_group_records_each_start_value(self):
        a, b = add_account(self.owner, 'A', balance='100.00'), add_account(self.owner, 'B', balance='250.00')
        shares.set_section(self.owner, [a, b], 'Bank and cash')
        self.assertEqual(sorted(FamilyShare.objects.values_list('amounts_start_value', flat=True)),
                         [D('100.00'), D('250.00')])

    def test_the_history_starts_on_day_one_so_the_chart_has_a_line_straight_away(self):
        shares.set_share(self.owner, add_account(self.owner, 'Savings', balance='50000.00'), 'AMOUNTS')
        data = self.data()
        self.assertEqual(data.history, ((timezone.localdate(), D('50000.00')),))

    def test_the_first_point_is_what_it_was_then_not_what_it_is_now(self):
        account = add_account(self.owner, 'Savings', balance='50000.00')
        shares.set_share(self.owner, account, 'AMOUNTS')
        Account.objects.filter(pk=account.pk).update(balance=D('70000.00'))
        data = self.data()
        self.assertEqual(data.history[0][1], D('50000.00'))
        self.assertEqual(data.net_worth, D('70000.00'))

    def test_the_chart_page_labels_the_first_point_start(self):
        shares.set_share(self.owner, add_account(self.owner, 'Savings', balance='50000.00'), 'AMOUNTS')
        self.client.force_login(self.partner)
        from django.urls import reverse
        page = self.client.get(reverse('family-networth'))
        self.assertContains(page, '>Start</text>')
        self.assertContains(page, '>Now</text>')

    def test_a_share_made_before_start_values_were_recorded_still_works(self):
        shares.set_share(self.owner, add_account(self.owner, 'Savings', balance='50000.00'), 'AMOUNTS')
        FamilyShare.objects.update(amounts_start_value=None)
        self.assertEqual(self.data().history, ())

    def test_the_start_point_never_includes_a_list_only_account(self):
        shares.set_share(self.owner, add_account(self.owner, 'Hidden', balance='99999.00'), 'LIST')
        shares.set_share(self.owner, add_account(self.owner, 'Shown', balance='1000.00'), 'AMOUNTS')
        self.assertEqual(self.data().history[0][1], D('1000.00'))


class EncryptionClaimsAgreeTests(FamilyTestCase):
    """The security page and the privacy policy must say the same thing about encryption, in every language."""

    def pages(self, language):
        from django.urls import reverse
        profile = self.owner.profile
        profile.language = language
        profile.save()
        self.client.force_login(self.owner)
        html = ''
        for name in ('security', 'privacy_policy'):
            try:
                html += ' ' + self.client.get(reverse(name)).content.decode()
            except Exception:                                   # url name differs: fall back to the paths
                html += ' ' + self.client.get(f'/{name.replace("_", "-")}/').content.decode()
        return ' '.join(html.split())

    def test_both_pages_say_storage_is_encrypted_at_rest_and_the_app_adds_no_extra_layer(self):
        from django.utils import translation
        self.addCleanup(translation.activate, 'en')
        html = self.pages('en')
        self.assertEqual(html.count('Supabase encrypts stored data at rest (storage-level encryption)'), 2)
        self.assertEqual(html.count('does not add a second layer of encryption inside the app'), 2)
        self.assertNotIn('which encrypts stored data at the storage level', html)

    def test_the_new_wording_is_translated(self):
        from django.utils import translation
        self.addCleanup(translation.activate, 'en')
        for language in ('hi', 'mr'):
            html = self.pages(language)
            self.assertNotIn('Supabase encrypts stored data at rest', html, language)


class DataLocationClaimsTests(FamilyTestCase):
    """Where the data lives is stated once, the same on both pages, in every language, and without overclaiming."""

    def html(self, language):
        profile = self.owner.profile
        profile.language = language
        profile.save()
        self.client.force_login(self.owner)
        return ' '.join(' '.join(self.client.get(f'/{page}/').content.decode() for page in ('security', 'privacy-policy')).split())

    def test_servers_in_bangalore_database_in_mumbai_and_the_outside_services_are_named(self):
        from django.utils import translation
        self.addCleanup(translation.activate, 'en')
        html = self.html('en')
        self.assertEqual(html.count('the application runs in Bangalore and the database in Mumbai')
                         + html.count('Our application servers run in Bangalore and our database in Mumbai'), 2)
        self.assertEqual(html.count('Region: Bangalore, India.'), 1)      # the application servers row
        self.assertEqual(html.count('Region: Mumbai, India.'), 1)         # the database row only
        self.assertIn('outside India', html)
        self.assertNotIn('all your data stays in India', html)

    def test_it_is_translated(self):
        from django.utils import translation
        self.addCleanup(translation.activate, 'en')
        for language in ('hi', 'mr'):
            html = self.html(language)
            self.assertNotIn('Our application servers run in Bangalore', html, language)
            self.assertNotIn('Where your data lives', html, language)
