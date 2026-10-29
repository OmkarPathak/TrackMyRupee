"""Phase 5 of family sharing: the emergency sheet and who can open it."""
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone, translation

from expenses.family import emergency, households
from expenses.models import (
    EmergencyAllowed,
    EmergencyRequest,
    EmergencySheet,
    FinancialAuditLog,
    HouseholdMember,
    Notification,
)

from .family_base import FamilyTestCase, add_account, end_pro, make_family, make_user
from .family_canary import MARKERS, CanaryWorld

D = Decimal


@override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True,
                   FAMILY_SUMMARY_CACHE_SECONDS=0)
class EmergencyFixture(FamilyTestCase):
    def setUp(self):
        super().setUp()
        for user, first in ((self.owner, 'Arjun'), (self.partner, 'Priya'), (self.parent, 'Meera')):
            user.first_name = first
            user.save()
        self.household = make_family(self.owner, self.partner, self.parent)
        self.world = CanaryWorld.seed(self.owner)
        mail.outbox.clear()

    def save(self, mode='REQUEST', allowed=None, nominee='Priya Mehta', documents='Blue folder, second shelf.',
             contacts='CA: Rohit Shah.', accounts=None):
        accounts = accounts or [self.world.bank_account, self.world.insurance_account]
        allowed = [self.partner.pk] if allowed is None else allowed
        return emergency.save_sheet(self.owner, mode, {a.pk: nominee for a in accounts}, allowed, documents, contacts)

    def sheet(self):
        return EmergencySheet.objects.get(owner=self.owner)

    def state(self, user):
        return emergency.state_for(user, EmergencySheet.objects.filter(owner=self.owner).first())


class SavingTheSheetTests(EmergencyFixture):
    def test_the_sheet_holds_only_the_accounts_the_owner_switched_on_with_their_nominee(self):
        sheet = self.save()
        self.assertEqual(sheet.items.count(), 2)
        self.assertEqual(set(sheet.items.values_list('nominee', flat=True)), {'Priya Mehta'})
        self.assertFalse(sheet.items.filter(account=self.world.private_account).exists())

    def test_saving_again_replaces_what_was_on_it(self):
        self.save()
        self.save(accounts=[self.world.card_account])
        self.assertEqual(list(self.sheet().items.values_list('account_id', flat=True)), [self.world.card_account.pk])

    def test_someone_elses_account_cannot_be_put_on_the_sheet(self):
        theirs = add_account(self.partner, 'Hers')
        emergency.save_sheet(self.owner, 'REQUEST', {theirs.pk: ''}, [], '', '')
        self.assertEqual(self.sheet().items.count(), 0)

    def test_only_people_in_the_family_can_be_allowed(self):
        outsider = make_user('outsider')
        self.save(allowed=[self.partner.pk, outsider.pk])
        self.assertEqual(list(self.sheet().allowed.values_list('user_id', flat=True)), [self.partner.pk])

    def test_something_that_looks_like_an_account_number_is_refused(self):
        for text in ('Account 123456789012 at HDFC', 'card 4111 1111 1111 1111', 'a/c 1234-5678-9012'):
            with self.assertRaises(households.FamilyError):
                self.save(documents=text)
        self.assertEqual(EmergencySheet.objects.count(), 0)
        self.save(documents='Policy copies are in the fireproof box. Phone 98 765.')       # short runs are fine

    def test_an_overlong_note_is_refused(self):
        with self.assertRaises(households.FamilyError):
            self.save(documents='x' * 1001)

    def test_a_bad_mode_is_refused(self):
        with self.assertRaises(households.FamilyError):
            self.save(mode='EVERYONE')

    def test_saving_is_audited(self):
        self.save()
        self.assertTrue(FinancialAuditLog.objects.filter(model_name='EmergencySheet', user=self.owner).exists())

    def test_taking_someone_off_the_list_ends_their_access_at_once(self):
        self.save()
        emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, EmergencyRequest.objects.get(), approve=True)
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        self.save(allowed=[])
        self.assertEqual(self.state(self.partner)[0], emergency.NONE)
        self.assertEqual(EmergencyRequest.objects.get().status, 'DENIED')


class RequestFlowTests(EmergencyFixture):
    def setUp(self):
        super().setUp()
        self.save()

    def test_a_person_who_may_ask_can_ask_and_the_owner_is_told_without_any_private_detail(self):
        self.assertEqual(self.state(self.partner)[0], emergency.ASK)
        request = emergency.ask(self.partner, self.sheet())
        self.assertEqual(self.state(self.partner)[0], emergency.PENDING)
        self.assertEqual(request.release_at.date(), (timezone.now() + timedelta(days=7)).date())
        note = Notification.objects.get(user=self.owner)
        self.assertIn('Priya', note.title)
        self.world.assert_clean(note, *mail.outbox)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.owner.email])

    def test_someone_not_allowed_cannot_ask_and_learns_nothing(self):
        self.assertEqual(self.state(self.parent)[0], emergency.NONE)
        with self.assertRaises(households.FamilyError):
            emergency.ask(self.parent, self.sheet())

    def test_the_owner_cannot_ask_for_their_own_sheet(self):
        self.assertEqual(self.state(self.owner)[0], emergency.NONE)

    def test_a_second_request_while_one_is_open_is_refused(self):
        emergency.ask(self.partner, self.sheet())
        with self.assertRaises(households.FamilyError):
            emergency.ask(self.partner, self.sheet())

    def test_the_delay_runs_out_and_the_requester_gets_the_sheet_and_the_owner_is_told_once(self):
        request = emergency.ask(self.partner, self.sheet())
        EmergencyRequest.objects.filter(pk=request.pk).update(release_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        self.assertEqual(Notification.objects.filter(user=self.owner, title__contains='can now open').count(), 0,
                         "reading a sheet must not write")
        self.assertEqual(emergency.notify_due_releases(), 1)
        self.assertEqual(emergency.notify_due_releases(), 0)
        notes = Notification.objects.filter(user=self.owner, title__contains='can now open')
        self.assertEqual(notes.count(), 1)

    def test_the_owner_can_approve_at_once(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        self.assertTrue(Notification.objects.filter(user=self.partner, title__contains='approved').exists())

    def test_a_denial_closes_the_sheet_and_the_requester_must_wait_before_asking_again(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=False)
        self.assertEqual(self.state(self.partner)[0], emergency.DENIED)
        EmergencyRequest.objects.filter(pk=request.pk).update(decided_at=timezone.now() - timedelta(days=8))
        self.assertEqual(self.state(self.partner)[0], emergency.ASK)

    def test_the_owner_can_withdraw_access_that_was_given(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        emergency.decide(self.owner, EmergencyRequest.objects.get(), approve=False)
        self.assertEqual(self.state(self.partner)[0], emergency.DENIED)

    def test_the_owner_can_withdraw_access_that_came_from_the_delay_running_out(self):
        request = emergency.ask(self.partner, self.sheet())
        EmergencyRequest.objects.filter(pk=request.pk).update(release_at=timezone.now() - timedelta(minutes=1))
        emergency.decide(self.owner, EmergencyRequest.objects.get(), approve=False)
        self.assertEqual(self.state(self.partner)[0], emergency.DENIED)

    def test_only_the_owner_can_answer_and_only_the_requester_can_cancel(self):
        request = emergency.ask(self.partner, self.sheet())
        with self.assertRaises(households.FamilyError):
            emergency.decide(self.partner, request, approve=True)
        with self.assertRaises(households.FamilyError):
            emergency.cancel(self.parent, request)
        emergency.cancel(self.partner, request)
        self.assertEqual(self.state(self.partner)[0], emergency.ASK)

    def test_an_answered_request_cannot_be_answered_again(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=False)
        with self.assertRaises(households.FamilyError):
            emergency.decide(self.owner, EmergencyRequest.objects.get(), approve=True)

    def test_always_visible_needs_no_request(self):
        self.save(mode='ALWAYS')
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        self.assertEqual(self.state(self.parent)[0], emergency.NONE)
        with self.assertRaises(households.FamilyError):
            emergency.ask(self.partner, self.sheet())

    def test_it_still_opens_when_a_plan_has_lapsed(self):
        end_pro(self.owner)
        end_pro(self.partner)
        self.save(mode='ALWAYS')
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)

    def test_leaving_the_family_ends_access_in_both_directions(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        households.leave(self.partner)
        self.assertEqual(self.state(self.partner)[0], emergency.NONE)
        self.assertEqual(EmergencyAllowed.objects.count(), 0)
        self.assertEqual(EmergencyRequest.objects.count(), 0)

    def test_a_removed_member_loses_access_and_rejoining_does_not_bring_it_back(self):
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        households.remove_member(self.owner, HouseholdMember.objects.get(user=self.partner))
        invitation, token = households.invite(self.household, self.owner)
        households.accept_invitation(invitation, self.partner)
        self.assertEqual(self.state(self.partner)[0], emergency.NONE)

    def test_a_member_of_another_family_learns_nothing(self):
        stranger, friend = make_user('stranger'), make_user('friend2')
        make_family(stranger, friend)
        self.assertEqual(self.state(friend)[0], emergency.NONE)


class SheetContentTests(EmergencyFixture):
    def test_the_sheet_says_what_exists_and_nothing_private(self):
        self.save()
        content = emergency.sheet_content(self.sheet())
        names = [r['item'].name for _k, _l, rows in content['groups'] for r in rows]
        self.assertEqual(sorted(names), ['Joint Savings', 'Term Cover'])
        self.world.assert_clean(content, content['groups'])

    def test_a_policy_number_is_never_on_the_sheet(self):
        self.save()
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency-sheet', args=[self.owner.household_membership.pk]))
        self.assertNotContains(page, MARKERS['policy_number'])


class EmergencyPagesTests(EmergencyFixture):
    def membership(self, user):
        return HouseholdMember.objects.get(user=user)

    def test_the_owner_edits_and_previews_the_sheet(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, 'If something happens to me')
        self.assertContains(page, 'Joint Savings')
        self.assertContains(page, 'Never put account numbers, passwords or PINs here.')
        post = {'mode': 'REQUEST', f'include_{self.world.bank_account.uuid}': 'on',
                f'nominee_{self.world.bank_account.uuid}': 'Priya Mehta',
                f'ask_{self.membership(self.partner).pk}': 'on', 'documents': 'Blue folder', 'contacts': 'CA'}
        self.client.post(reverse('family-emergency-save'), post)
        sheet = self.sheet()
        self.assertEqual(sheet.items.get().nominee, 'Priya Mehta')
        self.assertEqual(sheet.allowed.get().user, self.partner)
        preview = self.client.get(reverse('family-emergency-sheet', args=[self.membership(self.owner).pk]))
        self.assertContains(preview, 'Blue folder')
        self.assertContains(preview, 'nominee Priya Mehta')

    def test_a_save_with_a_number_in_a_note_shows_a_message_and_saves_nothing(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('family-emergency-save'),
                                    {'mode': 'REQUEST', 'documents': 'acct 123456789012'}, follow=True)
        self.assertContains(response, 'looks like an account or card number')
        self.assertEqual(EmergencySheet.objects.count(), 0)

    def test_the_requester_asks_waits_and_then_reads_the_sheet(self):
        self.save()
        owner_pk = self.membership(self.owner).pk
        self.client.force_login(self.partner)
        self.assertContains(self.client.get(reverse('family-emergency-sheet', args=[owner_pk])), 'Ask for the sheet')
        self.client.post(reverse('family-emergency-ask', args=[owner_pk]))
        page = self.client.get(reverse('family-emergency-sheet', args=[owner_pk]))
        self.assertContains(page, 'Waiting for Arjun')
        self.assertNotContains(page, 'Joint Savings')
        EmergencyRequest.objects.update(release_at=timezone.now() - timedelta(minutes=1))
        sheet_page = self.client.get(reverse('family-emergency-sheet', args=[owner_pk]))
        for text in ('Joint Savings', 'Term Cover', 'Blue folder', 'nominee Priya Mehta'):
            self.assertContains(sheet_page, text)
        self.world.assert_clean(sheet_page)

    def test_the_owner_sees_the_request_and_can_deny_it(self):
        self.save()
        emergency.ask(self.partner, self.sheet())
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, 'Priya asked for your emergency sheet')
        self.assertContains(page, '6 days 23 hours')
        request = EmergencyRequest.objects.get()
        self.client.post(reverse('family-emergency-deny', args=[request.uuid]))
        self.assertEqual(EmergencyRequest.objects.get().status, 'DENIED')

    def test_nobody_but_the_owner_can_answer_a_request(self):
        self.save()
        request = emergency.ask(self.partner, self.sheet())
        for user in (self.partner, self.parent):
            self.client.force_login(user)
            for name in ('family-emergency-approve', 'family-emergency-deny'):
                self.assertEqual(self.client.post(reverse(name, args=[request.uuid])).status_code, 404)
        self.assertEqual(EmergencyRequest.objects.get().status, 'PENDING')

    def test_someone_not_allowed_gets_a_not_found_so_the_sheet_is_not_even_hinted_at(self):
        self.save()
        self.client.force_login(self.parent)
        pk = self.membership(self.owner).pk
        self.assertEqual(self.client.get(reverse('family-emergency-sheet', args=[pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-emergency-ask', args=[pk])).status_code, 404)

    def test_a_person_with_no_sheet_to_open_gets_not_found(self):
        self.client.force_login(self.partner)
        self.assertEqual(
            self.client.get(reverse('family-emergency-sheet', args=[self.membership(self.owner).pk])).status_code, 404)

    def test_another_family_cannot_reach_the_sheet(self):
        self.save()
        stranger, friend = make_user('stranger2'), make_user('friend3')
        make_family(stranger, friend)
        self.client.force_login(friend)
        pk = self.membership(self.owner).pk
        self.assertEqual(self.client.get(reverse('family-emergency-sheet', args=[pk])).status_code, 404)

    def test_the_requester_can_cancel_a_request(self):
        self.save()
        request = emergency.ask(self.partner, self.sheet())
        self.client.force_login(self.partner)
        self.client.post(reverse('family-emergency-cancel', args=[request.uuid]))
        self.assertEqual(EmergencyRequest.objects.get().status, 'CANCELLED')

    def test_anonymous_visitors_are_sent_to_sign_in_and_the_flag_hides_the_pages(self):
        self.assertEqual(self.client.get(reverse('family-emergency')).status_code, 302)
        self.client.force_login(self.owner)
        with override_settings(FAMILY_ENABLED=False):
            self.assertEqual(self.client.get(reverse('family-emergency')).status_code, 404)

    def test_the_actions_need_post(self):
        self.save()
        request = emergency.ask(self.partner, self.sheet())
        self.client.force_login(self.owner)
        for url in (reverse('family-emergency-save'), reverse('family-emergency-deny', args=[request.uuid]),
                    reverse('family-emergency-approve', args=[request.uuid])):
            self.assertEqual(self.client.get(url).status_code, 405)

    def test_the_sheets_shared_with_you_are_listed_on_the_emergency_page(self):
        self.save()
        self.client.force_login(self.partner)
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, 'Sheets shared with you')
        self.assertContains(page, 'You can ask for it')

    def test_the_pages_render_in_hindi_and_marathi(self):
        self.addCleanup(translation.activate, 'en')
        self.save()
        for language, heading in (('hi', 'अगर मुझे कुछ हो जाए'), ('mr', 'मला काही झाले तर')):
            profile = self.owner.profile
            profile.language = language
            profile.save()
            self.client.force_login(self.owner)
            page = self.client.get(reverse('family-emergency'))
            self.assertContains(page, heading)
            self.assertNotContains(page, 'Notes for the people you trust')


class AccessLimitTests(EmergencyFixture):
    """Access given on request can have an end date, which the owner is warned about and can extend."""

    def limited(self, days=30, **kw):
        sheet = emergency.save_sheet(
            self.owner, 'REQUEST', {self.world.bank_account.pk: 'Priya Mehta'}, [self.partner.pk],
            'Blue folder', 'CA', access_days=days, **kw)
        return sheet

    def test_access_days_must_be_one_of_the_choices_or_empty(self):
        for bad in (7, '45', 'x', -1):
            with self.assertRaises(households.FamilyError):
                self.limited(days=bad)
        self.assertIsNone(self.limited(days='').access_days)
        self.assertEqual(self.limited(days='90').access_days, 90)

    def test_the_default_is_until_withdrawn(self):
        self.save()
        self.assertIsNone(self.sheet().access_days)
        request = emergency.ask(self.partner, self.sheet())
        self.assertIsNone(request.expires_at)

    def test_approved_access_ends_after_the_chosen_period_and_the_person_must_ask_again(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        request.refresh_from_db()
        self.assertEqual((request.expires_at - request.decided_at).days, 30)
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)
        EmergencyRequest.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.assertEqual(self.state(self.partner)[0], emergency.EXPIRED)
        again = emergency.ask(self.partner, self.sheet())
        self.assertNotEqual(again.pk, request.pk)
        self.assertEqual(self.state(self.partner)[0], emergency.PENDING)

    def test_access_from_the_delay_running_out_also_ends(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        EmergencyRequest.objects.update(release_at=timezone.now() - timedelta(days=40),
                                        expires_at=timezone.now() - timedelta(days=10))
        self.assertEqual(self.state(self.partner)[0], emergency.EXPIRED)
        self.assertEqual(emergency.people_with_access(self.sheet()), [])
        self.assertIsNotNone(request)

    def test_always_visible_has_no_end_date(self):
        emergency.save_sheet(self.owner, 'ALWAYS', {}, [self.partner.pk], '', '', access_days=30)
        self.assertEqual(self.state(self.partner)[0], emergency.GRANTED)

    def test_the_owner_is_warned_once_a_week_before_and_can_extend(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        Notification.objects.all().delete()
        EmergencyRequest.objects.update(expires_at=timezone.now() + timedelta(days=3))
        self.assertEqual(emergency.notify_expiring_access(), 1)
        self.assertEqual(emergency.notify_expiring_access(), 0)
        note = Notification.objects.get(user=self.owner)
        self.assertIn('ends soon', note.title)
        self.world.assert_clean(note)
        emergency.extend(self.owner, EmergencyRequest.objects.get())
        extended = EmergencyRequest.objects.get()
        self.assertGreater((extended.expires_at - timezone.now()).days, 28)
        self.assertFalse(extended.expiry_noted)

    def test_no_warning_for_access_far_from_ending_or_not_yet_given(self):
        self.limited(30)
        emergency.ask(self.partner, self.sheet())         # still inside the delay: nothing has been given
        EmergencyRequest.objects.update(expires_at=timezone.now() + timedelta(days=3))
        self.assertEqual(emergency.notify_expiring_access(), 0)

    def test_only_the_owner_can_extend_and_only_when_there_is_an_end_date(self):
        self.save()                                           # until withdrawn
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        with self.assertRaises(households.FamilyError):
            emergency.extend(self.owner, EmergencyRequest.objects.get())
        with self.assertRaises(households.FamilyError):
            emergency.extend(self.partner, EmergencyRequest.objects.get())

    def test_the_pages_show_the_end_date_and_the_extend_button(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, 'Access until')
        self.assertContains(page, reverse('family-emergency-extend', args=[request.uuid]))
        self.client.post(reverse('family-emergency-extend', args=[request.uuid]))
        self.assertEqual(self.client.get(reverse('family-emergency-extend', args=[request.uuid])).status_code, 405)

    def test_nobody_else_can_extend(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        self.client.force_login(self.partner)
        self.assertEqual(self.client.post(reverse('family-emergency-extend', args=[request.uuid])).status_code, 404)

    def test_an_expired_requester_sees_that_access_ended_and_can_ask_again(self):
        self.limited(30)
        request = emergency.ask(self.partner, self.sheet())
        emergency.decide(self.owner, request, approve=True)
        EmergencyRequest.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
        self.client.force_login(self.partner)
        pk = HouseholdMember.objects.get(user=self.owner).pk
        page = self.client.get(reverse('family-emergency-sheet', args=[pk]))
        self.assertContains(page, 'Your access has ended')
        self.assertContains(page, 'Ask for the sheet')
        self.assertNotContains(page, 'Joint Savings')

    def test_the_editor_saves_the_period_and_nickname_choice(self):
        self.client.force_login(self.owner)
        post = {'mode': 'REQUEST', 'access_days': '90', 'use_nicknames': 'on',
                f'include_{self.world.bank_account.uuid}': 'on'}
        self.client.post(reverse('family-emergency-save'), post)
        sheet = self.sheet()
        self.assertEqual((sheet.access_days, sheet.use_nicknames), (90, True))
        self.assertContains(self.client.get(reverse('family-emergency')), '90 days, then they must ask again')


class SheetNicknameTests(EmergencyFixture):
    def names(self, sheet):
        return [r['item'].name for _k, _l, rows in emergency.sheet_content(sheet)['groups'] for r in rows]

    def test_real_names_by_default_and_nicknames_when_chosen(self):
        from expenses.family import shares
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS', alias='Joint pot')
        sheet = self.save()
        self.assertIn('Joint Savings', self.names(sheet))
        emergency.save_sheet(self.owner, 'REQUEST', {self.world.bank_account.pk: ''}, [self.partner.pk], '', '',
                             use_nicknames=True)
        self.assertEqual(self.names(self.sheet()), ['Joint pot'])

    def test_an_account_without_a_nickname_keeps_its_real_name(self):
        emergency.save_sheet(self.owner, 'REQUEST', {self.world.bank_account.pk: ''}, [self.partner.pk], '', '',
                             use_nicknames=True)
        self.assertEqual(self.names(self.sheet()), ['Joint Savings'])
        self.world.assert_clean(emergency.sheet_content(self.sheet()))
