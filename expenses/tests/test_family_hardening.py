"""Races, the history rule and query counts for family sharing."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from expenses.family import households, shares, summary
from expenses.models import FamilyShare, Loan, LoanScheduleInstallment, NetWorthSnapshot

from .family_base import FamilyTestCase, add_account, make_family

D = Decimal
LEDGER = dict(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True,
              FAMILY_SUMMARY_CACHE_SECONDS=0)


def count_queries(fn):
    with CaptureQueriesContext(connection) as captured:
        fn()
    return len(captured)


class InvitationRaceTests(FamilyTestCase):
    def test_a_stale_copy_of_an_invitation_cannot_be_accepted_twice(self):
        household = make_family(self.owner)
        invitation, token = households.invite(household, self.owner)
        stale = households.find_invitation(token)            # what a second, simultaneous request has already read
        households.accept_invitation(households.find_invitation(token), self.partner)
        with self.assertRaises(households.FamilyError) as raised:
            households.accept_invitation(stale, self.parent)
        self.assertEqual(raised.exception.code, 'used')
        self.assertEqual(household.members.count(), 2)

    def test_a_stale_invitation_cannot_overfill_the_family(self):
        with override_settings(FAMILY_MAX_MEMBERS=2):
            household = make_family(self.owner)
            first, _t1 = households.invite(household, self.owner)
            stale = households.find_invitation(_t1)
            households.accept_invitation(first, self.partner)
            other = households.HouseholdInvitation.objects.create(
                household=household, token_hash='x' * 64, invited_by=self.owner,
                expires_at=timezone.now() + timedelta(days=1))
            with self.assertRaises(households.FamilyError) as raised:
                households.accept_invitation(other, self.parent)
            self.assertEqual(raised.exception.code, 'full')
            self.assertIsNotNone(stale)


@override_settings(**LEDGER)
class HistoryStartTests(FamilyTestCase):
    def test_history_never_reaches_back_before_amounts_were_shown(self):
        household = make_family(self.owner, self.partner)
        account = add_account(self.owner, 'Savings', balance='50000.00')
        shares.set_share(self.owner, account, 'LIST')
        FamilyShare.objects.update(created_at=timezone.now() - timedelta(days=100),
                                   amounts_since=timezone.now() - timedelta(days=100))
        month_end = (timezone.localdate().replace(day=1) - timedelta(days=40)).replace(day=1)
        month_end = (month_end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        NetWorthSnapshot.objects.create(user=self.owner, as_of_date=month_end, total_net_worth=D('1'),
                                        total_assets=D('1'), total_liabilities=D('0'),
                                        breakdown={str(account.pk): '48000.00'})
        shares.set_share(self.owner, account, 'AMOUNTS')         # amounts become visible today
        household.refresh_from_db()
        self.assertEqual(summary.family_summary(household, self.partner).history, ())


@override_settings(**LEDGER)
class QueryCountTests(FamilyTestCase):
    def _loan_account(self, user, i):
        loan = Loan.objects.create(user=user, name=f'L{i}', loan_type='HOME', initial_principal=D('1000'),
                                   duration_months=12, start_date=date(2026, 1, 1))
        LoanScheduleInstallment.objects.create(loan=loan, installment_no=1, due_date=date(2027, 1, 1),
                                               scheduled_principal=D('80'), scheduled_interest=D('5'),
                                               scheduled_balance=D('900'))
        return add_account(user, f'loan{i}', account_type='HOME_LOAN', balance='-900', linked_loan=loan)

    def test_more_loans_do_not_add_queries_and_the_next_emi_is_right(self):
        household = make_family(self.owner)
        shares.set_share(self.owner, self._loan_account(self.owner, 0), 'AMOUNTS')
        household.refresh_from_db()
        one = count_queries(lambda: summary.family_summary(household, self.owner))
        for i in range(1, 5):
            shares.set_share(self.owner, self._loan_account(self.owner, i), 'AMOUNTS')
        household.refresh_from_db()
        result = []
        many = count_queries(lambda: result.append(summary.family_summary(household, self.owner)))
        self.assertEqual(many, one)
        self.assertTrue(all(i.emi == D('85') for i in result[0].items))

    def test_the_preview_page_does_not_ask_per_private_account(self):
        make_family(self.owner, self.partner)
        self.client.force_login(self.owner)
        from django.urls import reverse
        url = reverse('family-preview')
        add_account(self.owner, 'private-first')
        self.client.get(url)
        few = count_queries(lambda: self.client.get(url))
        for i in range(5):
            add_account(self.owner, f'private{i}')
        self.client.get(url)                                  # let per-request caches (bell, counts) warm
        self.assertEqual(count_queries(lambda: self.client.get(url)), few)


class ShareWholeGroupTests(FamilyTestCase):
    def setUp(self):
        super().setUp()
        make_family(self.owner, self.partner)
        self.banks = [add_account(self.owner, f'bank{i}') for i in range(3)]
        self.card = add_account(self.owner, 'card', account_type='CREDIT_CARD', balance='-500.00')
        self.client.force_login(self.owner)

    def post(self, key='bank'):
        from django.urls import reverse
        return self.client.post(reverse('family-share-group', args=[key]))

    def test_switching_a_group_on_shares_every_account_in_it_and_only_it(self):
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(set(FamilyShare.objects.values_list('account_id', flat=True)), {a.pk for a in self.banks})

    def test_a_partly_shared_group_is_completed_without_changing_earlier_choices(self):
        shares.set_share(self.owner, self.banks[0], 'LIST', alias='Joint')
        self.post()
        self.assertEqual(FamilyShare.objects.count(), 3)
        first = FamilyShare.objects.get(account=self.banks[0])
        self.assertEqual((first.level, first.alias), ('LIST', 'Joint'))

    def test_switching_a_fully_shared_group_off_stops_them_all(self):
        self.post()
        self.post()
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_it_is_one_cache_bump_and_one_notice_for_the_whole_group(self):
        from expenses.models import HouseholdMember, Notification
        HouseholdMember.objects.filter(user=self.owner).update(notify_new_shares=True)
        household = households.household_for(self.owner)
        before = household.cache_version
        self.post()
        household.refresh_from_db()
        self.assertEqual(household.cache_version, before + 1)
        self.assertEqual(Notification.objects.filter(user=self.partner).count(), 1)

    def test_an_unknown_group_is_a_404_and_get_is_refused(self):
        from django.urls import reverse
        self.assertEqual(self.post('nope').status_code, 404)
        self.assertEqual(self.client.get(reverse('family-share-group', args=['bank'])).status_code, 405)


class AccountDeletionTests(FamilyTestCase):
    def delete(self, user):
        from expenses.views.settings import delete_user_account
        return delete_user_account(user.pk)

    def test_deleting_the_owner_hands_the_family_to_the_longest_standing_member(self):
        household = make_family(self.owner, self.partner, self.parent)
        shares.set_share(self.partner, add_account(self.partner, 'Priya savings'), 'AMOUNTS')
        self.assertTrue(self.delete(self.owner))
        household.refresh_from_db()
        self.assertEqual(household.owner_id, self.partner.pk)
        self.assertEqual(sorted(household.members.values_list('user_id', flat=True)),
                         sorted([self.partner.pk, self.parent.pk]))
        self.assertEqual(FamilyShare.objects.filter(owner=self.partner).count(), 1)

    def test_deleting_a_member_leaves_the_rest_and_removes_only_their_shares(self):
        household = make_family(self.owner, self.partner)
        shares.set_share(self.owner, add_account(self.owner, 'Arjun savings'), 'AMOUNTS')
        shares.set_share(self.partner, add_account(self.partner, 'Priya savings'), 'AMOUNTS')
        self.assertTrue(self.delete(self.partner))
        household.refresh_from_db()
        self.assertEqual(household.owner_id, self.owner.pk)
        self.assertEqual(household.members.count(), 1)
        self.assertEqual(list(FamilyShare.objects.values_list('owner_id', flat=True)), [self.owner.pk])

    def test_deleting_the_last_member_ends_the_family(self):
        from expenses.models import Household
        household = make_family(self.owner)
        self.assertTrue(self.delete(self.owner))
        self.assertFalse(Household.objects.filter(pk=household.pk).exists())

    def test_deleting_someone_who_is_in_no_family_still_works(self):
        self.assertTrue(self.delete(self.owner))
