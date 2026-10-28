"""Phase 4 of family sharing: gentle notes."""
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone, translation

from expenses.family import notes, shares, summary
from expenses.models import Account, GentleNoteDismissal, HouseholdMember, PhysicalAsset

from .family_base import FamilyTestCase, end_pro, make_family
from .family_canary import CanaryWorld

D = Decimal


@override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True,
                   FAMILY_SUMMARY_CACHE_SECONDS=0)
class NotesFixture(FamilyTestCase):
    def setUp(self):
        super().setUp()
        translation.activate('en')       # another test in the same process may have left a language switched on
        for user, first in ((self.owner, 'Arjun'), (self.partner, 'Priya'), (self.parent, 'Meera')):
            user.first_name = first
            user.save()
        self.household = make_family(self.owner, self.partner, self.parent)
        self.world = CanaryWorld.seed(self.owner)

    def health_policy(self, covers='Arjun, Priya', due_in=None, name='Star Health'):
        start = timezone.localdate() + timedelta(days=due_in) if due_in is not None else timezone.localdate() - timedelta(days=200)
        policy = PhysicalAsset.objects.create(
            user=self.owner, name=name, asset_class='INSURANCE', sum_assured=D('1000000'),
            premium_amount=D('28000'), premium_frequency='ANNUAL', policy_start_date=start,
            policy_type='PROTECTION', covers=covers, policy_number='SECRET')
        account = Account.objects.create(user=self.owner, name=name, account_type='HEALTH_INSURANCE',
                                         balance=D('0'), currency='₹', linked_physical_asset=policy)
        shares.set_share(self.owner, account, 'AMOUNTS')
        return account

    def data(self):
        self.household.refresh_from_db()
        return summary.family_summary(self.household, self.owner)


class NoteRulesTests(NotesFixture):
    def test_no_notes_when_nothing_is_shared(self):
        self.assertEqual(notes.build_notes(self.data()), [])

    def test_a_health_cover_gap_is_named_calmly(self):
        self.health_policy(covers='Arjun, Priya')
        texts = [n.text for n in notes.build_notes(self.data())]
        self.assertTrue(any('Health cover is shared for Arjun and Priya. Nothing is shared yet for Meera.' in t
                            and 'there is nothing to do' in t for t in texts))

    def test_no_note_when_everyone_is_covered_or_nobody_is(self):
        self.health_policy(covers='Arjun, Priya and Meera')
        self.assertEqual([n for n in notes.build_notes(self.data()) if n.key.startswith('health-gap')], [])

    def test_a_member_holding_a_policy_with_no_names_counts_as_covered(self):
        self.health_policy(covers='')
        keys = [n.key for n in notes.build_notes(self.data()) if n.key.startswith('health-gap')]
        self.assertEqual(len(keys), 1)       # Arjun holds it; Priya and Meera are not named

    def test_the_names_are_matched_as_whole_words(self):
        self.health_policy(covers='Priyanka, Arjun')
        text = [n.text for n in notes.build_notes(self.data()) if n.key.startswith('health-gap')][0]
        self.assertIn('Priya', text.split('Nothing is shared yet for')[1])

    def test_card_bills_and_premiums_due_soon_are_noted(self):
        self.health_policy(covers='Arjun', due_in=5)
        shares.set_share(self.owner, self.world.card_account, 'AMOUNTS')
        keys = {n.key.split(':')[0] for n in notes.build_notes(self.data())}
        self.assertIn('premiums', keys)

    def test_a_list_only_item_never_produces_a_note(self):
        self.health_policy(covers='Arjun')
        shares.set_share(self.owner, self.world.insurance_account, 'LIST')
        shares.set_share(self.owner, self.world.card_account, 'LIST')
        notes_ = notes.build_notes(self.data())
        self.assertFalse([n for n in notes_ if n.key.startswith('bills')])

    def test_notes_say_nothing_private(self):
        self.health_policy()
        for note in notes.build_notes(self.data()):
            self.world.assert_clean(note.text)
            self.assertNotIn('SECRET', note.text)

    def test_at_most_three_notes(self):
        self.health_policy(covers='Arjun', due_in=3)
        shares.set_share(self.owner, self.world.card_account, 'AMOUNTS')
        self.assertLessEqual(len(notes.build_notes(self.data())), notes.MAX_NOTES)


class NoteControlsTests(NotesFixture):
    def setUp(self):
        super().setUp()
        self.health_policy(covers='Arjun')
        self.client.force_login(self.partner)

    def test_the_note_appears_on_who_holds_what(self):
        page = self.client.get(reverse('family-inventory'))
        self.assertContains(page, 'Gentle notes')
        self.assertContains(page, 'there is nothing to do')

    def test_dismissing_hides_it_for_that_person_only(self):
        key = notes.build_notes(self.data())[0].key
        self.client.post(reverse('family-note-dismiss'), {'key': key})
        self.assertEqual(GentleNoteDismissal.objects.get().user, self.partner)
        self.assertNotContains(self.client.get(reverse('family-inventory')), 'there is nothing to do')
        self.client.force_login(self.parent)
        self.assertContains(self.client.get(reverse('family-inventory')), 'there is nothing to do')

    def test_a_made_up_key_is_ignored(self):
        self.client.post(reverse('family-note-dismiss'), {'key': 'whatever'})
        self.assertEqual(GentleNoteDismissal.objects.count(), 0)

    def test_a_changed_situation_brings_a_fresh_note(self):
        key = notes.build_notes(self.data())[0].key
        self.client.post(reverse('family-note-dismiss'), {'key': key})
        self.health_policy(covers='Priya', name='Second')            # a second policy changes who is covered
        fresh = [n for n in notes.notes_for(self.partner, self.household, self.data())[0]]
        self.assertTrue(fresh)

    def test_turning_notes_off_and_on(self):
        self.client.post(reverse('family-notes-toggle'), {'on': '0'})
        self.assertFalse(HouseholdMember.objects.get(user=self.partner).gentle_notes)
        page = self.client.get(reverse('family-inventory'))
        self.assertNotContains(page, 'there is nothing to do')
        self.assertContains(page, 'Gentle notes are off for you.')
        self.client.post(reverse('family-notes-toggle'), {'on': '1'})
        self.assertContains(self.client.get(reverse('family-inventory')), 'there is nothing to do')

    def test_the_controls_need_post(self):
        self.assertEqual(self.client.get(reverse('family-note-dismiss')).status_code, 405)
        self.assertEqual(self.client.get(reverse('family-notes-toggle')).status_code, 405)

    def test_no_notes_while_family_view_is_paused(self):
        end_pro(self.parent)
        page = self.client.get(reverse('family-inventory'))
        self.assertNotContains(page, 'there is nothing to do')

    def test_the_pages_render_in_hindi(self):
        self.addCleanup(translation.activate, 'en')
        profile = self.partner.profile
        profile.language = 'hi'
        profile.save()
        page = self.client.get(reverse('family-inventory'))
        self.assertContains(page, 'सौम्य सुझाव')
        self.assertNotContains(page, 'Small, optional observations')

    def test_the_page_never_leaks_private_data_with_notes_on(self):
        self.world.assert_clean(self.client.get(reverse('family-inventory')))
