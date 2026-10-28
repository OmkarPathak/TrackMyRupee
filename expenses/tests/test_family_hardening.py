"""Races, the history rule and query counts for family sharing."""
from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from expenses.family import households, shares, summary
from expenses.models import FamilyShare, Loan, LoanScheduleInstallment, NetWorthSnapshot

from .family_base import FamilyTestCase, add_account, make_family, make_user

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
