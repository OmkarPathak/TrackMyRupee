"""Phase 1 of family sharing: households, invitations, the Pro rule and the family pages."""
import re
from datetime import timedelta
from pathlib import Path

from django.core import mail
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses.family import family_enabled_for, households
from expenses.family import plan as plan_rules
from expenses.models import (
    ConsentEvent,
    Household,
    HouseholdInvitation,
    HouseholdMember,
)

from .family_base import FamilyTestCase, end_pro, make_family, make_user
from .family_canary import CanaryWorld


@override_settings(FAMILY_ENABLED=False)      # the developer's .env may switch it on; these tests start from "off"
class FlagAndAccessTests(TestCase):
    def setUp(self):
        self.user = make_user('flag_user')
        self.client.force_login(self.user)

    def test_family_is_off_by_default_and_its_pages_do_not_exist(self):
        self.assertFalse(family_enabled_for(self.user))
        self.assertEqual(self.client.get(reverse('family-home')).status_code, 404)
        self.assertEqual(self.client.get(reverse('family-join', args=['anything'])).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-create')).status_code, 404)

    def test_the_menu_item_is_absent_while_it_is_off(self):
        response = self.client.get(reverse('home'))
        self.assertNotContains(response, reverse('family-home'))

    @override_settings(FAMILY_ENABLED=True)
    def test_switched_on_for_everyone(self):
        self.assertTrue(family_enabled_for(self.user))
        self.assertEqual(self.client.get(reverse('family-home')).status_code, 200)
        self.assertContains(self.client.get(reverse('home')), reverse('family-home'))

    def test_switched_on_for_a_cohort_only(self):
        other = make_user('not_in_cohort')
        with override_settings(FAMILY_USER_IDS={self.user.pk}):
            self.assertTrue(family_enabled_for(self.user))
            self.assertFalse(family_enabled_for(other))

    @override_settings(FAMILY_ENABLED=True)
    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        for name in ('family-home',):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('login', response.url)
        response = self.client.get(reverse('family-join', args=['tok']))
        self.assertEqual(response.status_code, 302)
        self.assertIn('next=', response.url)


class CreatingAFamilyTests(FamilyTestCase):
    def test_a_pro_user_creates_a_family_and_is_its_owner_and_first_member(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('family-create'))
        self.assertRedirects(response, reverse('family-home'))
        household = Household.objects.get()
        self.assertEqual(household.owner, self.owner)
        self.assertEqual(list(household.members.values_list('user_id', flat=True)), [self.owner.pk])

    def test_a_user_who_is_not_on_pro_cannot_create_one(self):
        free = make_user('free_user', pro=False)
        self.client.force_login(free)
        self.client.post(reverse('family-create'))
        self.assertFalse(Household.objects.exists())
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, 'startTrial()')
        self.assertContains(page, reverse('pricing'))

    def test_a_second_family_is_refused(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('family-create'))
        self.client.post(reverse('family-create'))
        self.assertEqual(Household.objects.count(), 1)


class InvitationTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = households.create_household(self.owner)
        self.client.force_login(self.owner)

    def test_inviting_by_email_sends_the_email_and_shows_the_link_once(self):
        response = self.client.post(reverse('family-invite'), {'email': 'Ravi@Example.com'}, follow=True)
        invitation = HouseholdInvitation.objects.get()
        self.assertEqual(invitation.email, 'ravi@example.com')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ravi@example.com'])
        link = re.search(r'https?://\S+/family/join/[\w-]+/', mail.outbox[0].body).group(0)
        self.assertContains(response, link)                       # shown once, on the page after sending
        again = self.client.get(reverse('family-home'))
        self.assertNotContains(again, link)

    def test_an_invitation_without_an_email_gives_a_link_and_sends_nothing(self):
        response = self.client.post(reverse('family-invite'), {'email': ''}, follow=True)
        self.assertEqual(len(mail.outbox), 0)
        self.assertContains(response, '/family/join/')
        self.assertEqual(HouseholdInvitation.objects.get().email, '')

    def test_only_a_hash_of_the_token_is_stored(self):
        invitation, token = households.invite(self.household, self.owner)
        stored = ' '.join(str(v) for v in HouseholdInvitation.objects.filter(pk=invitation.pk).values()[0].values())
        self.assertNotIn(token, stored)
        self.assertEqual(invitation.token_hash, households.hash_token(token))

    def test_a_bad_email_is_refused(self):
        self.client.post(reverse('family-invite'), {'email': 'not-an-email'})
        self.assertFalse(HouseholdInvitation.objects.exists())

    @override_settings(FAMILY_MAX_MEMBERS=2)
    def test_invitations_count_against_the_seats(self):
        self.client.post(reverse('family-invite'), {'email': ''})
        self.client.post(reverse('family-invite'), {'email': ''})
        self.assertEqual(HouseholdInvitation.objects.count(), 1)

    def test_only_the_owner_can_invite_cancel_or_renew(self):
        invitation, _token = households.invite(self.household, self.owner)
        households.accept_invitation(households.invite(self.household, self.owner)[0], self.partner)
        self.client.force_login(self.partner)
        self.assertEqual(self.client.post(reverse('family-invite'), {'email': ''}).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-invite-cancel', args=[invitation.uuid])).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-invite-renew', args=[invitation.uuid])).status_code, 404)

    def test_cancelling_makes_the_link_useless(self):
        invitation, token = households.invite(self.household, self.owner)
        self.client.post(reverse('family-invite-cancel', args=[invitation.uuid]))
        self.assertEqual(households.invitation_problem(households.find_invitation(token)), 'cancelled')

    def test_renewing_replaces_the_link(self):
        invitation, old_token = households.invite(self.household, self.owner, 'ravi@example.com')
        self.client.post(reverse('family-invite-renew', args=[invitation.uuid]))
        self.assertEqual(households.invitation_problem(households.find_invitation(old_token)), 'cancelled')
        self.assertEqual(HouseholdInvitation.objects.filter(cancelled_at__isnull=True).count(), 1)

    def test_another_familys_invitation_cannot_be_touched(self):
        stranger = make_user('stranger')
        other = households.create_household(stranger)
        theirs, _t = households.invite(other, stranger)
        self.assertEqual(self.client.post(reverse('family-invite-cancel', args=[theirs.uuid])).status_code, 404)


class JoiningTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = households.create_household(self.owner)
        self.invitation, self.token = households.invite(self.household, self.owner)
        self.url = reverse('family-join', args=[self.token])

    def test_a_pro_user_joins_with_consent_and_it_is_recorded(self):
        self.client.force_login(self.partner)
        page = self.client.get(self.url)
        self.assertContains(page, 'name="consent"')
        response = self.client.post(self.url, {'consent': 'on'})
        self.assertRedirects(response, reverse('family-home'))
        self.assertTrue(HouseholdMember.objects.filter(household=self.household, user=self.partner).exists())
        self.assertTrue(ConsentEvent.objects.filter(user=self.partner, action='GRANTED', purpose='family_sharing').exists())
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.accepted_by, self.partner)

    def test_joining_needs_the_consent_box(self):
        self.client.force_login(self.partner)
        self.client.post(self.url, {})
        self.assertFalse(HouseholdMember.objects.filter(user=self.partner).exists())

    def test_a_link_works_once(self):
        self.client.force_login(self.partner)
        self.client.post(self.url, {'consent': 'on'})
        self.client.force_login(self.parent)
        page = self.client.get(self.url)
        self.assertContains(page, 'already been used')
        self.client.post(self.url, {'consent': 'on'})
        self.assertFalse(HouseholdMember.objects.filter(user=self.parent).exists())

    def test_someone_not_on_pro_is_offered_the_trial_and_cannot_join(self):
        free = make_user('free_invitee', pro=False)
        self.client.force_login(free)
        page = self.client.get(self.url)
        self.assertContains(page, 'startTrial()')
        self.assertNotContains(page, 'name="consent"')
        self.client.post(self.url, {'consent': 'on'})
        self.assertFalse(HouseholdMember.objects.filter(user=free).exists())

    def test_someone_already_in_a_family_cannot_join_another(self):
        stranger = make_user('stranger')
        households.create_household(stranger)
        self.client.force_login(stranger)
        self.assertContains(self.client.get(self.url), 'already in a family')
        self.client.post(self.url, {'consent': 'on'})
        self.assertFalse(HouseholdMember.objects.filter(household=self.household, user=stranger).exists())

    def test_an_expired_invitation_cannot_be_used(self):
        HouseholdInvitation.objects.filter(pk=self.invitation.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.partner)
        self.assertContains(self.client.get(self.url), 'has expired')
        self.client.post(self.url, {'consent': 'on'})
        self.assertFalse(HouseholdMember.objects.filter(user=self.partner).exists())

    def test_an_unknown_link_says_so_without_naming_anyone(self):
        self.client.force_login(self.partner)
        page = self.client.get(reverse('family-join', args=['not-a-real-token']))
        self.assertContains(page, 'not valid')
        self.assertNotContains(page, 'arjun')

    @override_settings(FAMILY_MAX_MEMBERS=3)
    def test_a_family_that_filled_up_in_the_meantime_refuses_the_join(self):
        # owner + one open invitation (setUp) = 2 seats; one more member fills the third
        households.accept_invitation(households.invite(self.household, self.owner)[0], self.parent)
        with override_settings(FAMILY_MAX_MEMBERS=2):              # the limit is lowered before the link is used
            self.client.force_login(self.partner)
            self.client.post(self.url, {'consent': 'on'})
        self.assertFalse(HouseholdMember.objects.filter(user=self.partner).exists())

    def test_the_owner_opening_their_own_link_is_told_they_are_in(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url), 'already in this family')


class LeavingAndRemovingTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)

    def test_a_member_leaves_and_withdraws_consent(self):
        self.client.force_login(self.partner)
        self.client.post(reverse('family-leave'))
        self.assertFalse(HouseholdMember.objects.filter(user=self.partner).exists())
        self.assertTrue(ConsentEvent.objects.filter(user=self.partner, action='WITHDRAWN').exists())
        self.assertEqual(self.household.members.count(), 2)

    def test_the_owner_leaving_hands_the_family_to_the_longest_standing_member(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('family-leave'))
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.partner)

    def test_the_last_person_leaving_ends_the_family(self):
        solo = make_user('solo')
        household = households.create_household(solo)
        households.leave(solo)
        self.assertFalse(Household.objects.filter(pk=household.pk).exists())

    def test_the_owner_removes_a_member(self):
        membership = HouseholdMember.objects.get(user=self.partner)
        self.client.force_login(self.owner)
        self.client.post(reverse('family-member-remove', args=[membership.pk]))
        self.assertFalse(HouseholdMember.objects.filter(user=self.partner).exists())

    def test_a_member_cannot_remove_anyone(self):
        membership = HouseholdMember.objects.get(user=self.parent)
        self.client.force_login(self.partner)
        self.assertEqual(self.client.post(reverse('family-member-remove', args=[membership.pk])).status_code, 404)
        self.assertTrue(HouseholdMember.objects.filter(user=self.parent).exists())

    def test_the_owner_cannot_remove_themselves(self):
        membership = HouseholdMember.objects.get(user=self.owner)
        self.client.force_login(self.owner)
        self.client.post(reverse('family-member-remove', args=[membership.pk]))
        self.assertTrue(HouseholdMember.objects.filter(user=self.owner).exists())

    def test_leaving_bumps_the_cache_version(self):
        before = Household.objects.get(pk=self.household.pk).cache_version
        households.leave(self.partner)
        self.assertGreater(Household.objects.get(pk=self.household.pk).cache_version, before)


class ProRuleTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner)
        self.client.force_login(self.owner)

    def test_everyone_on_pro_is_on(self):
        self.assertEqual(plan_rules.household_plan_state(self.household).state, plan_rules.ON)
        self.assertNotContains(self.client.get(reverse('family-home')), 'Family view is on')     # nothing to say when all is well

    def test_a_plan_ending_within_the_window_warns(self):
        end_pro(self.partner, days_from_now=9)
        state = plan_rules.household_plan_state(self.household)
        self.assertEqual((state.state, state.active), (plan_rules.WARNING, True))
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, 'ends in 8 days')
        self.assertContains(page, 'priya')

    def test_the_warning_for_your_own_plan_says_so(self):
        end_pro(self.owner, days_from_now=3)
        self.assertContains(self.client.get(reverse('family-home')), 'Your Pro plan ends in')

    def test_a_plan_ending_later_than_the_window_does_not_warn(self):
        end_pro(self.partner, days_from_now=60)
        self.assertEqual(plan_rules.household_plan_state(self.household).state, plan_rules.ON)

    def test_a_lifetime_plan_never_warns(self):
        lifetime = make_user('forever', lifetime=True)
        household = make_family(lifetime)
        self.assertEqual(plan_rules.household_plan_state(household).state, plan_rules.ON)

    def test_an_ended_plan_pauses_family_view_for_everyone_and_deletes_nothing(self):
        end_pro(self.partner, days_from_now=-1)
        state = plan_rules.household_plan_state(self.household)
        self.assertEqual((state.state, state.active), (plan_rules.PAUSED, False))
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, 'Family view is paused')
        self.assertContains(page, 'Plan ended')
        self.assertEqual(self.household.members.count(), 2)

    def test_it_resumes_when_the_plan_is_renewed(self):
        end_pro(self.partner, days_from_now=-1)
        end_pro(self.partner, days_from_now=365)
        self.assertEqual(plan_rules.household_plan_state(self.household).state, plan_rules.ON)

    def test_the_paused_banner_for_your_own_lapse_says_so(self):
        end_pro(self.owner, days_from_now=-1)
        self.assertContains(self.client.get(reverse('family-home')), 'because your Pro plan has ended')


class HomePageTests(FamilyTestCase):
    def test_the_empty_state_for_someone_with_no_family(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, 'Share the big picture with your family')
        self.assertContains(page, reverse('family-create'))

    def test_a_member_sees_the_members_but_no_owner_controls(self):
        household = make_family(self.owner, self.partner)
        households.invite(household, self.owner, 'pending@example.com')
        self.client.force_login(self.partner)
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, 'arjun')
        self.assertContains(page, 'priya')
        self.assertNotContains(page, reverse('family-invite'))
        self.assertNotContains(page, 'pending@example.com')       # only the owner sees who has been invited
        self.assertNotContains(page, '/remove/')

    def test_the_owner_sees_the_controls_and_pending_invitations(self):
        household = make_family(self.owner, self.partner)
        households.invite(household, self.owner, 'pending@example.com')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, reverse('family-invite'))
        self.assertContains(page, 'pending@example.com')
        self.assertContains(page, '/remove/')

    def test_a_family_page_never_shows_another_familys_members(self):
        make_family(self.owner, self.partner)
        stranger = make_user('stranger')
        make_family(stranger)
        self.client.force_login(stranger)
        page = self.client.get(reverse('family-home'))
        self.assertNotContains(page, 'arjun')
        self.assertNotContains(page, 'priya')

    def test_the_page_stays_within_a_small_query_budget(self):
        make_family(self.owner, self.partner, self.parent)
        self.client.force_login(self.owner)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(self.client.get(reverse('family-home')).status_code, 200)
        self.assertLess(len(queries), 45, [q['sql'][:80] for q in queries])


class NothingPrivateReachesTheFamilyTests(FamilyTestCase):
    """Nothing is shared in Phase 1, so no marker from the owner's data may appear anywhere a member can look."""

    def test_the_markers_never_appear_on_the_pages_or_in_the_email(self):
        world = CanaryWorld.seed(self.owner)
        household = make_family(self.owner, self.partner)
        households.invite(household, self.owner, 'ravi@example.com')
        invitation, token = households.invite(household, self.owner)
        from expenses.family.emails import send_invitation_email
        send_invitation_email(invitation, 'http://testserver/family/join/' + token + '/')

        self.client.force_login(self.partner)
        responses = [self.client.get(reverse('family-home')),
                     self.client.get(reverse('family-join', args=[token]))]
        world.assert_clean(*responses, *mail.outbox)


class TranslationAndStyleTests(FamilyTestCase):
    def test_every_family_string_has_hindi_and_marathi(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        import update_family_translations
        self.assertEqual(update_family_translations.missing(), [])

    def test_the_pages_render_in_hindi_and_marathi(self):
        make_family(self.owner, self.partner)
        self.client.force_login(self.owner)
        for code, word in (('hi', 'परिवार'), ('mr', 'कुटुंब')):
            profile = self.owner.profile
            profile.language = code
            profile.save()
            page = self.client.get(reverse('family-home'))
            self.assertContains(page, word)
            self.assertNotContains(page, 'What family can and cannot see')
            self.assertContains(page, 'lang="%s"' % code)

    def test_the_templates_use_theme_variables_not_fixed_colours(self):
        """Dark mode works because colours come from variables. A colour literal is only allowed as the fallback
        inside var(--name, #hex), the way the Accounts page writes it."""
        root = Path(__file__).resolve().parents[2] / 'templates' / 'expenses' / 'family'
        for path in root.glob('*.html'):
            text = re.sub(r'var\(--[\w-]+,\s*#[0-9a-fA-F]{3,8}\)', '', path.read_text(encoding='utf-8'))
            self.assertEqual(re.findall(r'#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b(?![\w-])', text), [], path.name)
