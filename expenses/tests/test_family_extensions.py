"""Family sharing extensions: notices when something changes, deleting with a name check, handing over ownership,
the emergency checklist, the Family step in guided setup, and family sharing on the plans pages."""
import re
from unittest.mock import patch

from django.core import mail
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.family import catalog, emergency, households
from expenses.models import (
    ConsentEvent,
    EmergencySheet,
    FinancialAuditLog,
    FinancialFlow,
    Household,
    HouseholdMember,
    Notification,
)

from .family_base import FamilyTestCase, add_account, make_family, make_user


def titles(user):
    return list(Notification.objects.filter(user=user).values_list('title', flat=True))


class NoticeTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)
        mail.outbox.clear()

    def test_deleting_the_family_tells_every_other_member_and_not_the_owner(self):
        households.delete_household(self.owner, self.household.name)
        for member in (self.partner, self.parent):
            self.assertEqual(titles(member), ['arjun deleted the family'])
            notice = Notification.objects.get(user=member)
            self.assertIn('no longer exists', notice.message)
            self.assertEqual(notice.link, reverse('family-home'))
        self.assertEqual(titles(self.owner), [])
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ['meera@example.com', 'priya@example.com'])

    def test_a_failed_delete_tells_nobody(self):
        with self.assertRaises(households.FamilyError):
            households.delete_household(self.owner, 'wrong name')
        with self.assertRaises(households.FamilyError):
            households.delete_household(self.partner, self.household.name)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_removing_a_member_tells_that_member_only(self):
        households.remove_member(self.owner, HouseholdMember.objects.get(user=self.partner))
        self.assertEqual(titles(self.partner), ['You were removed from the family'])
        self.assertIn('arjun removed you', Notification.objects.get(user=self.partner).message)
        self.assertEqual(titles(self.parent), [])
        self.assertEqual(titles(self.owner), [])
        self.assertEqual([m.to[0] for m in mail.outbox], ['priya@example.com'])

    def test_handing_over_ownership_tells_the_new_owner_only(self):
        households.transfer_ownership(self.owner, HouseholdMember.objects.get(user=self.partner))
        self.assertEqual(titles(self.partner), ['You are now the owner of the family'])
        self.assertEqual(titles(self.parent), [])
        self.assertEqual(titles(self.owner), [])

    def test_the_owner_leaving_tells_the_member_who_takes_over(self):
        households.leave(self.owner)
        self.assertEqual(titles(self.partner), ['You are now the owner of the family'])
        self.assertIn('arjun left the family', Notification.objects.get(user=self.partner).message)
        self.assertEqual(titles(self.parent), [])

    def test_an_ordinary_member_leaving_tells_nobody(self):
        households.leave(self.parent)
        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_the_notice_is_in_the_readers_language(self):
        profile = self.partner.profile
        profile.language = 'hi'
        profile.save()
        households.remove_member(self.owner, HouseholdMember.objects.get(user=self.partner))
        self.assertNotEqual(titles(self.partner), ['You were removed from the family'])
        self.assertIn('परिवार', titles(self.partner)[0])

    def test_a_broken_mail_server_never_undoes_the_change(self):
        with patch('expenses.family.notices.send_mail', side_effect=OSError('smtp down')):
            households.delete_household(self.owner, self.household.name)
        self.assertFalse(Household.objects.filter(pk=self.household.pk).exists())
        self.assertEqual(titles(self.partner), ['arjun deleted the family'])

    def test_a_member_without_an_email_still_gets_the_in_app_notice(self):
        self.partner.email = ''
        self.partner.save()
        households.remove_member(self.owner, HouseholdMember.objects.get(user=self.partner))
        self.assertEqual(len(titles(self.partner)), 1)
        self.assertEqual(mail.outbox, [])

    def test_the_notice_never_names_an_account(self):
        add_account(self.partner, 'Priya Secret Savings')
        households.delete_household(self.owner, self.household.name)
        text = ' '.join(n.message + n.title for n in Notification.objects.all())
        self.assertNotIn('Secret', text)


class DeleteWithNameCheckTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner)
        self.household.name = 'The Sharma Family'
        self.household.save()
        self.client.force_login(self.owner)

    def delete(self, name):
        return self.client.post(reverse('family-delete'), {'confirm_name': name}, follow=True)

    def test_the_wrong_name_keeps_the_family(self):
        response = self.delete('Sharma')
        self.assertTrue(Household.objects.filter(pk=self.household.pk).exists())
        self.assertContains(response, 'Type the family name exactly as shown')
        self.assertEqual(HouseholdMember.objects.count(), 2)
        self.assertEqual(Notification.objects.count(), 0)

    def test_no_name_at_all_keeps_the_family(self):
        response = self.client.post(reverse('family-delete'), follow=True)
        self.assertTrue(Household.objects.filter(pk=self.household.pk).exists())
        self.assertContains(response, 'Type the family name exactly as shown')

    def test_the_right_name_deletes_it_ignoring_case_and_extra_spaces(self):
        self.delete('  the   SHARMA family ')
        self.assertFalse(Household.objects.filter(pk=self.household.pk).exists())
        self.assertFalse(HouseholdMember.objects.exists())

    def test_deleting_withdraws_consent_for_everyone_and_is_audited(self):
        self.delete('The Sharma Family')
        for user in (self.owner, self.partner):
            self.assertTrue(ConsentEvent.objects.filter(user=user, action='WITHDRAWN').exists())
        log = FinancialAuditLog.objects.get(model_name='Household', action='DELETE')
        self.assertEqual(log.object_id, self.household.id)
        self.assertEqual(sorted(log.diff['members']), sorted([self.owner.id, self.partner.id]))

    def test_deleting_removes_everything_shared_and_emergency_links(self):
        from expenses.family import shares
        from expenses.models import FamilyShare
        account = add_account(self.partner, 'Priya Savings')
        shares.set_share(self.partner, account, 'AMOUNTS')
        self.assertEqual(FamilyShare.objects.count(), 1)
        emergency.save_sheet(self.partner, EmergencySheet.REQUEST, {account.pk: 'Arjun'}, [self.owner.pk], '', '')
        self.delete('The Sharma Family')
        self.assertFalse(self.partner.emergency_allowed.exists())
        self.assertFalse(self.owner.emergency_allowed.exists())
        self.assertFalse(Household.objects.exists())
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_delete_form_is_only_shown_to_the_owner_and_works_without_js(self):
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, reverse('family-delete'))
        self.assertContains(page, 'data-expected="The Sharma Family"')
        self.assertNotRegex(page.content.decode(), r'data-fam-delete-go\s+disabled')   # JS disables it on load
        self.client.force_login(self.partner)
        self.assertNotContains(self.client.get(reverse('family-home')), reverse('family-delete'))

    def test_a_name_with_html_is_escaped_in_the_page(self):
        self.household.name = '<script>alert(1)</script>'
        self.household.save()
        page = self.client.get(reverse('family-home'))
        self.assertNotContains(page, '<script>alert(1)</script>')

    def test_a_member_cannot_delete_even_with_the_right_name(self):
        self.client.force_login(self.partner)
        self.assertEqual(self.client.post(reverse('family-delete'), {'confirm_name': 'The Sharma Family'}).status_code, 404)
        self.assertTrue(Household.objects.filter(pk=self.household.pk).exists())

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse('family-delete')).status_code, 405)


class TransferOwnershipTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)
        self.partner_membership = HouseholdMember.objects.get(user=self.partner)
        self.client.force_login(self.owner)

    def make_owner(self, membership):
        return self.client.post(reverse('family-member-make-owner', args=[membership.pk]), follow=True)

    def test_the_owner_hands_the_family_to_a_chosen_member(self):
        response = self.make_owner(HouseholdMember.objects.get(user=self.parent))     # not the longest-standing
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.parent)
        self.assertContains(response, 'meera is now the owner of the family')
        self.assertTrue(HouseholdMember.objects.filter(user=self.owner, household=self.household).exists())

    def test_nothing_else_changes_for_anyone(self):
        before = HouseholdMember.objects.count()
        self.make_owner(self.partner_membership)
        self.assertEqual(HouseholdMember.objects.count(), before)
        self.assertEqual(Household.objects.count(), 1)

    def test_the_old_owner_loses_the_owner_powers_and_the_new_owner_gains_them(self):
        self.make_owner(self.partner_membership)
        self.assertEqual(self.client.post(reverse('family-invite')).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-delete'), {'confirm_name': self.household.name}).status_code, 404)
        self.assertEqual(self.client.post(reverse('family-member-make-owner', args=[self.partner_membership.pk])).status_code, 404)
        self.client.force_login(self.partner)
        self.assertEqual(self.client.post(reverse('family-invite')).status_code, 302)
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, reverse('family-delete'))

    def test_the_old_owner_can_now_leave_without_the_family_changing_hands_again(self):
        self.make_owner(self.partner_membership)
        households.leave(self.owner)
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.partner)

    def test_a_member_cannot_hand_over_the_family(self):
        self.client.force_login(self.partner)
        parent_membership = HouseholdMember.objects.get(user=self.parent)
        self.assertEqual(self.client.post(reverse('family-member-make-owner', args=[parent_membership.pk])).status_code, 404)
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.owner)

    def test_the_owner_cannot_pick_themselves(self):
        own = HouseholdMember.objects.get(user=self.owner)
        response = self.make_owner(own)
        self.assertContains(response, 'You are already the owner')
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.owner)

    def test_a_member_of_another_family_cannot_be_picked(self):
        stranger = make_user('stranger')
        other = make_family(stranger)
        membership = HouseholdMember.objects.get(user=stranger, household=other)
        self.assertEqual(self.client.post(reverse('family-member-make-owner', args=[membership.pk])).status_code, 404)
        self.household.refresh_from_db()
        self.assertEqual(self.household.owner, self.owner)

    def test_a_stale_request_after_ownership_moved_is_refused(self):
        stale = HouseholdMember.objects.select_related('household').get(user=self.parent)
        households.transfer_ownership(self.owner, self.partner_membership)
        with self.assertRaises(households.FamilyError) as raised:
            households.transfer_ownership(self.owner, stale)
        self.assertEqual(raised.exception.code, 'not_owner')

    def test_a_member_who_left_meanwhile_cannot_be_picked(self):
        stale = HouseholdMember.objects.select_related('household').get(user=self.parent)
        households.leave(self.parent)
        with self.assertRaises(households.FamilyError) as raised:
            households.transfer_ownership(self.owner, stale)
        self.assertEqual(raised.exception.code, 'not_member')

    def test_it_bumps_the_cache_and_is_audited(self):
        before = Household.objects.get(pk=self.household.pk).cache_version
        self.make_owner(self.partner_membership)
        self.assertGreater(Household.objects.get(pk=self.household.pk).cache_version, before)
        log = FinancialAuditLog.objects.get(model_name='Household', action='UPDATE')
        self.assertEqual(log.user, self.owner)
        self.assertEqual(log.diff['owner'], {'from': self.owner.id, 'to': self.partner.id})

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse('family-member-make-owner', args=[self.partner_membership.pk])).status_code, 405)

    def test_the_button_is_shown_to_the_owner_for_other_members_only(self):
        page = self.client.get(reverse('family-home'))
        self.assertContains(page, reverse('family-member-make-owner', args=[self.partner_membership.pk]))
        own = HouseholdMember.objects.get(user=self.owner)
        self.assertNotContains(page, reverse('family-member-make-owner', args=[own.pk]))
        self.client.force_login(self.partner)
        self.assertNotContains(self.client.get(reverse('family-home')), '/make-owner/')

    def test_a_name_with_a_quote_cannot_break_the_confirmation(self):
        self.partner.first_name = "O'Neil"
        self.partner.save()
        page = self.client.get(reverse('family-home')).content.decode()
        self.assertNotIn("Make O'Neil", page)
        self.assertIn('Make O\\u0027Neil', page)


class ChecklistTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner)

    def check(self, sheet=None):
        return {i['key']: i for i in emergency.checklist(self.owner, sheet or emergency.sheet_of(self.owner))['items']}

    def test_insurance_accounts_are_classed_as_insurance(self):
        self.assertEqual(catalog.kind_of('LIFE_INSURANCE'), catalog.INSURANCE)
        self.assertEqual(catalog.kind_of('HEALTH_INSURANCE'), catalog.INSURANCE)

    def test_a_new_user_with_nothing_has_the_three_basic_checks_open(self):
        result = emergency.checklist(self.owner, None)
        self.assertEqual([i['key'] for i in result['items']], ['documents', 'contacts', 'allowed'])
        self.assertEqual((result['done'], result['total']), (0, 3))

    def test_a_policy_missing_from_the_sheet_is_flagged_by_name(self):
        add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        items = self.check(None)
        self.assertFalse(items['policies']['done'])
        self.assertEqual(items['policies']['names'], ['Term Plan'])
        self.assertFalse(items['nominees']['done'])          # nothing on the sheet yet

    def test_accounts_missing_from_the_sheet_are_flagged_separately_from_policies(self):
        add_account(self.owner, 'Salary', 'SALARY_ACCOUNT')
        add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {Account_pk(self.owner, 'Term Plan'): 'Priya'}, [], '', '')
        items = self.check()
        self.assertTrue(items['policies']['done'])
        self.assertEqual(items['accounts']['names'], ['Salary'])

    def test_an_account_on_the_sheet_without_a_nominee_is_flagged(self):
        bank = add_account(self.owner, 'Salary', 'SALARY_ACCOUNT')
        policy = add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {bank.pk: '', policy.pk: 'Priya'}, [], '', '')
        items = self.check()
        self.assertFalse(items['nominees']['done'])
        self.assertEqual(items['nominees']['names'], ['Salary'])
        self.assertTrue(items['accounts']['done'])

    def test_a_nominee_of_only_spaces_counts_as_none(self):
        bank = add_account(self.owner, 'Salary', 'SALARY_ACCOUNT')
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {bank.pk: '   '}, [], '', '')
        self.assertEqual(self.check()['nominees']['names'], ['Salary'])

    def test_cards_and_loans_never_need_a_nominee(self):
        add_account(self.owner, 'Visa', 'CREDIT_CARD', balance='0')
        add_account(self.owner, 'Home loan', 'HOME_LOAN', balance='0')
        items = self.check(None)
        self.assertNotIn('policies', items)
        self.assertNotIn('accounts', items)
        self.assertNotIn('nominees', items)

    def test_closed_accounts_are_ignored(self):
        add_account(self.owner, 'Old policy', 'LIFE_INSURANCE', is_active=False)
        self.assertNotIn('policies', self.check(None))

    def test_a_fully_complete_sheet_is_all_done(self):
        bank = add_account(self.owner, 'Salary', 'SALARY_ACCOUNT')
        policy = add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {bank.pk: 'Priya', policy.pk: 'Priya'},
                             [self.partner.pk], 'Blue file in the cupboard', 'Our CA, Mr Rao')
        result = emergency.checklist(self.owner, emergency.sheet_of(self.owner))
        self.assertEqual(result['done'], result['total'])
        self.assertTrue(all(i['done'] and not i['detail'] and not i['names'] for i in result['items']))

    def test_documents_contacts_and_people_each_clear_on_their_own(self):
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {}, [], 'In the locker', '')
        items = self.check()
        self.assertTrue(items['documents']['done'])
        self.assertFalse(items['contacts']['done'])
        self.assertFalse(items['allowed']['done'])
        emergency.save_sheet(self.owner, EmergencySheet.REQUEST, {}, [self.partner.pk], 'In the locker', '   ')
        items = self.check()
        self.assertFalse(items['contacts']['done'])               # only spaces
        self.assertTrue(items['allowed']['done'])

    def test_someone_elses_accounts_never_appear_in_my_checklist(self):
        add_account(self.partner, 'Priya Term Plan', 'LIFE_INSURANCE')
        self.assertNotIn('policies', self.check(None))

    def test_the_page_shows_the_checklist_with_counts_and_names(self):
        add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, 'Is your sheet ready?')
        self.assertContains(page, 'data-check="policies"')
        self.assertContains(page, 'Some insurance policies are not on the sheet')
        self.assertContains(page, '<div class="fam-check-names">Term Plan</div>', html=True)
        self.assertContains(page, '0 of 5 done')

    def test_saving_the_sheet_updates_the_checklist_on_the_page(self):
        policy = add_account(self.owner, 'Term Plan', 'LIFE_INSURANCE')
        self.client.force_login(self.owner)
        self.client.post(reverse('family-emergency-save'), {
            'mode': 'REQUEST', f'include_{policy.uuid}': 'on', f'nominee_{policy.uuid}': 'Priya',
            'documents': 'Locker', 'contacts': 'CA', f'ask_{HouseholdMember.objects.get(user=self.partner).pk}': 'on'})
        page = self.client.get(reverse('family-emergency'))
        self.assertContains(page, '5 of 5 done')
        self.assertContains(page, 'Every insurance policy is on the sheet')
        self.assertNotContains(page, 'Some insurance policies are not on the sheet')

    def test_the_checklist_does_not_depend_on_a_plan(self):
        from .family_base import end_pro
        end_pro(self.owner)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('family-emergency')), 'Is your sheet ready?')

    def test_the_checklist_is_in_hindi_for_a_hindi_reader(self):
        profile = self.owner.profile
        profile.language = 'hi'
        profile.save()
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-emergency'))
        self.assertNotContains(page, 'Is your sheet ready?')
        self.assertContains(page, 'क्या आपकी शीट तैयार है?')


def Account_pk(user, name):
    return user.accounts.get(name=name).pk


@override_settings(FAMILY_ENABLED=True)
class FamilyFlowTests(TestCase):
    def setUp(self):
        self.user = make_user('flow_user')
        self.client.force_login(self.user)

    def idem(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'family'})).content.decode()
        return re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

    def commit(self, name='', key=None):
        return self.client.post(reverse('flow-commit', kwargs={'key': 'family'}),
                                {'name': name, 'idempotency_key': key or self.idem()}, HTTP_HX_REQUEST='true')

    def test_it_is_offered_on_the_landing_page_with_its_own_category(self):
        response = self.client.get(reverse('flow-landing'))
        self.assertIn('family', [f['key'] for f in response.context['flows']])
        self.assertIn('family', [c['key'] for c in response.context['categories']])
        self.assertEqual(response.context['total_count'], 12)
        self.assertContains(response, 'I want to share the big picture with my family')

    def test_it_creates_a_family_and_sends_you_to_the_family_page(self):
        response = self.commit('The Rao Family')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Redirect'], reverse('family-home'))
        household = households.household_for(self.user)
        self.assertEqual((household.name, household.owner), ('The Rao Family', self.user))
        self.assertEqual(HouseholdMember.objects.filter(household=household).count(), 1)
        self.assertEqual(FinancialFlow.objects.filter(user=self.user, flow_key='family').count(), 1)
        self.assertEqual(FinancialAuditLog.objects.filter(model_name='Household', action='CREATE').count(), 1)

    def test_a_blank_name_becomes_our_family(self):
        self.commit('   ')
        self.assertEqual(households.household_for(self.user).name, 'Our family')

    def test_a_plain_form_post_redirects_to_the_family_page(self):
        response = self.client.post(reverse('flow-commit', kwargs={'key': 'family'}),
                                    {'name': '', 'idempotency_key': self.idem()})
        self.assertRedirects(response, reverse('family-home'), fetch_redirect_response=False)

    def test_a_double_submit_creates_one_family(self):
        key = self.idem()
        self.assertEqual(self.commit('A', key).status_code, 204)
        self.assertEqual(self.commit('B', key).status_code, 204)
        self.assertEqual(Household.objects.count(), 1)
        self.assertEqual(households.household_for(self.user).name, 'A')
        self.assertEqual(FinancialFlow.objects.filter(flow_key='family').count(), 1)

    def test_somebody_already_in_a_family_cannot_start_another(self):
        make_family(self.user)
        response = self.commit('Second')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'You are already in a family.')
        self.assertEqual(Household.objects.count(), 1)

    def test_without_pro_nothing_is_created_and_the_review_says_why(self):
        free = make_user('free_flow_user', pro=False)
        self.client.force_login(free)
        response = self.commit('Free family')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'needs a Pro plan')
        self.assertContains(response, reverse('pricing'))
        self.assertFalse(Household.objects.exists())
        self.assertFalse(FinancialFlow.objects.filter(flow_key='family').exists())

    def test_the_review_shows_the_family_name_not_a_currency_amount(self):
        response = self.client.post(reverse('flow-preview', kwargs={'key': 'family'}), {'name': 'The Rao Family'})
        self.assertContains(response, 'The Rao Family')
        self.assertContains(response, 'Nothing is shared until you choose it')
        self.assertNotContains(response, 'data-count-up')

    def test_it_counts_as_set_up_once_you_are_in_a_family(self):
        before = self.client.get(reverse('flow-landing'))
        self.assertNotIn('family', [f['key'] for f in before.context['configured_flows']])
        self.commit('Our family')
        after = self.client.get(reverse('flow-landing'))
        self.assertIn('family', [f['key'] for f in after.context['configured_flows']])
        self.assertEqual(after.context['configured_count'], 1)

    def test_the_form_rejects_an_over_long_name(self):
        response = self.commit('x' * 101)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Household.objects.exists())

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('flow-detail', kwargs={'key': 'family'})).status_code, 302)

    @override_settings(FAMILY_ENABLED=False, FAMILY_USER_IDS=())
    def test_while_family_sharing_is_off_the_flow_does_not_exist(self):
        landing = self.client.get(reverse('flow-landing'))
        self.assertNotIn('family', [f['key'] for f in landing.context['flows']])
        self.assertNotIn('family', [c['key'] for c in landing.context['categories']])
        self.assertEqual(landing.context['total_count'], 11)
        self.assertEqual(self.client.get(reverse('flow-detail', kwargs={'key': 'family'})).status_code, 404)
        self.assertEqual(self.client.post(reverse('flow-preview', kwargs={'key': 'family'}), {}).status_code, 404)
        self.assertEqual(self.client.post(reverse('flow-commit', kwargs={'key': 'family'}),
                                          {'name': 'x', 'idempotency_key': '00000000-0000-0000-0000-000000000000'}).status_code, 404)
        self.assertFalse(Household.objects.exists())

    @override_settings(FAMILY_ENABLED=False)
    def test_a_trial_cohort_user_sees_it(self):
        with override_settings(FAMILY_USER_IDS={self.user.pk}):
            self.assertIn('family', [f['key'] for f in self.client.get(reverse('flow-landing')).context['flows']])

    def test_other_flows_still_go_home_after_committing(self):
        from expenses.flows.registry import FlowRegistry
        self.assertEqual(FlowRegistry.get('salary').get_success_url(None), reverse('home'))


@override_settings(FAMILY_ENABLED=True)
class PlansPagesTests(TestCase):
    TEXT = 'Family sharing for up to 5 people'

    def test_the_pricing_page_lists_family_sharing_under_pro_only(self):
        page = self.client.get(reverse('pricing')).content.decode()
        self.assertEqual(page.count(self.TEXT), 1)
        pro = page.index(self.TEXT)
        self.assertGreater(pro, page.index('Weekly insights email'))

    def test_the_landing_page_lists_it_under_pro(self):
        page = self.client.get(reverse('landing')).content.decode()
        self.assertIn(self.TEXT, page)

    def test_the_compare_plans_popup_lists_it(self):
        user = make_user('modal_user', pro=False)
        html = render_to_string('components/compare_plans_modal.html', {'user': user})
        self.assertIn(self.TEXT, html)

    def test_the_pricing_page_is_translated(self):
        user = make_user('hindi_pricing')
        profile = user.profile
        profile.language = 'hi'
        profile.save()
        self.client.force_login(user)
        page = self.client.get(reverse('pricing')).content.decode()
        self.assertNotIn(self.TEXT, page)
        self.assertIn('5 लोगों तक के लिए परिवार साझाकरण', page)
