"""Audit fixes for family sharing: share notices end with the sharing, remove_member re-checks under the lock, the
delete form works without JavaScript, and deleting a flow record never deletes the family."""
from django.urls import reverse

from expenses.family import households, shares
from expenses.models import ConsentEvent, FinancialFlow, Household, HouseholdMember, Notification
from expenses.tests.family_base import FamilyTestCase, add_account, make_family, make_user


class ShareNoticeTeardownTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)
        HouseholdMember.objects.filter(household=self.household).update(notify_new_shares=True)
        self.owner_acc = add_account(self.owner, 'Arjun Savings')
        self.partner_acc = add_account(self.partner, 'Priya Savings')
        shares.set_share(self.owner, self.owner_acc, 'AMOUNTS')
        shares.set_share(self.partner, self.partner_acc, 'AMOUNTS')

    def share_notices(self, user):
        return Notification.objects.filter(user=user, message__contains='is now shared')

    def test_notices_exist_before_teardown(self):
        self.assertEqual(self.share_notices(self.partner).count(), 1)
        self.assertEqual(self.share_notices(self.owner).count(), 1)
        self.assertEqual(self.share_notices(self.parent).count(), 2)

    def test_leaving_removes_notices_received_and_about_own_shares(self):
        households.leave(self.partner)
        self.assertEqual(self.share_notices(self.partner).count(), 0)
        # the partner's own share notices are gone for the others; the owner's notice to the others stays
        self.assertEqual(self.share_notices(self.owner).count(), 0)
        self.assertEqual(self.share_notices(self.parent).count(), 1)
        self.assertIn('Arjun Savings', self.share_notices(self.parent).get().message)

    def test_removing_a_member_does_the_same_and_keeps_the_removed_notice(self):
        membership = HouseholdMember.objects.get(user=self.partner)
        households.remove_member(self.owner, membership)
        self.assertEqual(self.share_notices(self.partner).count(), 0)
        self.assertEqual(self.share_notices(self.owner).count(), 0)
        self.assertEqual(self.share_notices(self.parent).count(), 1)
        self.assertTrue(Notification.objects.filter(user=self.partner, title='You were removed from the family').exists())

    def test_deleting_the_family_removes_every_share_notice_but_keeps_the_deleted_notice(self):
        households.delete_household(self.owner, self.household.name)
        for user in (self.owner, self.partner, self.parent):
            self.assertEqual(self.share_notices(user).count(), 0)
        self.assertTrue(Notification.objects.filter(user=self.partner, title__contains='deleted the family').exists())
        self.assertTrue(Notification.objects.filter(user=self.parent, title__contains='deleted the family').exists())

    def test_other_notifications_are_left_alone(self):
        other = Notification.objects.create(user=self.partner, title='Hi', message='is now shared', link='/x/')
        households.leave(self.partner)
        self.assertTrue(Notification.objects.filter(pk=other.pk).exists())

    def test_ownership_notice_survives_the_owner_leaving(self):
        households.leave(self.owner)
        self.assertTrue(Notification.objects.filter(user=self.partner, title='You are now the owner of the family').exists())


class RemoveMemberRecheckTests(FamilyTestCase):
    def test_old_owner_cannot_remove_after_handing_over(self):
        household = make_family(self.owner, self.partner, self.parent)
        stale = HouseholdMember.objects.select_related('household').get(user=self.parent)
        households.transfer_ownership(self.owner, HouseholdMember.objects.get(user=self.partner))
        with self.assertRaises(households.FamilyError) as ctx:
            households.remove_member(self.owner, stale)
        self.assertEqual(ctx.exception.code, 'not_owner')
        self.assertTrue(HouseholdMember.objects.filter(user=self.parent, household=household).exists())

    def test_removing_someone_who_already_left_is_refused_without_duplicates(self):
        make_family(self.owner, self.partner)
        stale = HouseholdMember.objects.select_related('household').get(user=self.partner)
        households.leave(self.partner)
        withdrawn = ConsentEvent.objects.filter(user=self.partner, action='WITHDRAWN').count()
        notices = Notification.objects.filter(user=self.partner).count()
        with self.assertRaises(households.FamilyError) as ctx:
            households.remove_member(self.owner, stale)
        self.assertEqual(ctx.exception.code, 'not_member')
        self.assertEqual(ConsentEvent.objects.filter(user=self.partner, action='WITHDRAWN').count(), withdrawn)
        self.assertEqual(Notification.objects.filter(user=self.partner).count(), notices)


class DeleteFormWithoutJavascriptTests(FamilyTestCase):
    def test_delete_button_is_enabled_in_the_markup(self):
        make_family(self.owner, self.partner)
        self.client.force_login(self.owner)
        html = self.client.get(reverse('family-home')).content.decode()
        tag = html.split('data-fam-delete-go', 1)[1].split('>', 1)[0]
        self.assertNotIn('disabled', tag)


class FamilyFlowDeletionTests(FamilyTestCase):
    def test_deleting_the_flow_record_leaves_the_family(self):
        user = make_user('flowuser')
        self.client.force_login(user)
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'family'})).content.decode()
        import re
        key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)
        self.client.post(reverse('flow-commit', kwargs={'key': 'family'}),
                         {'name': 'Keep Me', 'idempotency_key': key}, HTTP_HX_REQUEST='true')
        household = households.household_for(user)
        self.assertIsNotNone(household)
        flow = FinancialFlow.objects.get(user=user, flow_key='family')
        flow.delete()
        self.assertTrue(Household.objects.filter(pk=household.pk).exists())
        self.assertTrue(HouseholdMember.objects.filter(user=user).exists())
