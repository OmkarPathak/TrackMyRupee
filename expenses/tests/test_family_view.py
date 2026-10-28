"""Phase 3 of family sharing: the family summary, the Net worth and Who holds what pages, the Me / Family switch."""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from expenses.family import households, shares, summary
from expenses.family.presenters import chart
from expenses.models import Account, FamilyShare, NetWorthSnapshot

from .family_base import FamilyTestCase, add_account, end_pro, make_family, make_user
from .family_canary import MARKERS, CanaryWorld

D = Decimal
LEDGER = dict(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True,
              FAMILY_SUMMARY_CACHE_SECONDS=0)


@override_settings(**LEDGER)
class ViewFixture(FamilyTestCase):
    """Arjun shares a bank account, a card and a policy; Priya shares a savings account; Meera shares nothing."""

    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)
        self.world = CanaryWorld.seed(self.owner)
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        shares.set_share(self.owner, self.world.card_account, 'AMOUNTS')
        shares.set_share(self.owner, self.world.insurance_account, 'AMOUNTS')
        self.kotak = add_account(self.partner, 'Kotak Savings', balance='110000.00')
        shares.set_share(self.partner, self.kotak, 'AMOUNTS')
        self.household.refresh_from_db()


class FamilySummaryTests(ViewFixture):
    def test_the_family_net_worth_adds_up_only_what_is_shared_with_amounts(self):
        data = summary.family_summary(self.household, self.owner)
        # bank 50,000 + 650 (the canary's income less expense) + Kotak 110,000 less the card 4,000
        self.assertEqual(data.owns, D('160650.00'))
        self.assertEqual(data.owes, D('4000.00'))
        self.assertEqual(data.net_worth, D('156650.00'))
        self.assertEqual(data.counted_items, 4)

    def test_it_is_the_same_for_every_member(self):
        a = summary.family_summary(self.household, self.owner)
        b = summary.family_summary(self.household, self.partner)
        self.assertEqual((a.owns, a.owes, a.net_worth), (b.owns, b.owes, b.net_worth))

    def test_a_list_only_item_is_listed_but_not_counted(self):
        shares.set_share(self.owner, self.world.card_account, 'LIST')
        data = summary.family_summary(self.household, self.owner)
        self.assertEqual(data.owes, D('0.00'))
        self.assertEqual(data.listed_only, 1)
        self.assertIn('Everyday Card', [i.name for i in data.items])

    def test_an_item_in_another_currency_is_not_added_to_the_total(self):
        profile = self.partner.profile
        profile.currency = '$'
        profile.save()
        data = summary.family_summary(self.household, self.owner)
        self.assertEqual(data.owns, D('50650.00'))
        self.assertEqual(data.other_currency, 1)
        self.assertFalse([m for m in data.members if m.user_id == self.partner.pk][0].counted)

    def test_what_the_family_owns_is_grouped_and_the_percentages_make_sense(self):
        data = summary.family_summary(self.household, self.owner)
        self.assertEqual([row[0] for row in data.allocation], ['cash'])
        self.assertEqual(data.allocation[0][3], 100)

    def test_the_holdings_table_counts_per_member_and_leaves_out_empty_rows(self):
        data = summary.family_summary(self.household, self.owner)
        rows = {key: (per_member, total) for key, _h, per_member, total in data.counts}
        self.assertEqual(rows['bank'][1], 2)
        self.assertEqual(rows['card'][0][self.owner.pk], 1)
        self.assertEqual(rows['card'][0][self.partner.pk], 0)
        self.assertNotIn('loan', rows)

    def test_a_card_bill_and_a_premium_due_soon_are_listed(self):
        policy = self.world.policy
        policy.policy_start_date = timezone.localdate() + timedelta(days=5)
        policy.premium_frequency = 'ANNUAL'
        policy.save()
        data = summary.family_summary(self.household, self.owner)
        kinds = {u.kind for u in data.upcoming}
        self.assertEqual(kinds, {'premium'} | ({'bill'} if self.world.card_account.next_billing_date
                                               and self.world.card_account.next_billing_date
                                               - timezone.localdate() <= timedelta(days=30) else set()))

    def test_nothing_private_is_anywhere_in_the_summary(self):
        self.world.assert_clean(summary.family_summary(self.household, self.partner))

    def test_the_cache_is_dropped_when_something_is_unshared(self):
        with override_settings(FAMILY_SUMMARY_CACHE_SECONDS=60):
            cache.clear()
            before = summary.family_summary(self.household, self.owner)
            self.assertEqual(before.counted_items, 4)
            shares.stop_share(self.owner, self.world.card_account)
            self.household.refresh_from_db()
            after = summary.family_summary(self.household, self.owner)
            self.assertEqual(after.counted_items, 3)
            self.assertEqual(after.owes, D('0.00'))
            cache.clear()


class HistoryTests(ViewFixture):
    def snapshot(self, user, when, values):
        NetWorthSnapshot.objects.create(
            user=user, as_of_date=when, total_net_worth=D('999999.00'), total_assets=D('999999.00'),
            total_liabilities=D('0.00'), breakdown={str(k): str(v) for k, v in values.items()})

    def test_history_uses_month_end_snapshots_of_shared_accounts_only_from_the_share_date(self):
        today = timezone.localdate()
        first = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        end = today.replace(day=1) - timedelta(days=1)
        when = timezone.make_aware(timezone.datetime(first.year, first.month, 2))
        FamilyShare.objects.update(created_at=when, amounts_since=when)
        self.snapshot(self.owner, end - timedelta(days=3), {self.world.bank_account.pk: 40000})   # not month end
        self.snapshot(self.owner, end, {self.world.bank_account.pk: 42000,
                                        self.world.card_account.pk: -3000,
                                        self.world.private_account.pk: 888888})
        self.snapshot(self.partner, end, {self.kotak.pk: 100000})
        data = summary.family_summary(self.household, self.owner)
        self.assertEqual(len(data.history), 1)
        when, value = data.history[0]
        self.assertEqual(when, end)
        self.assertEqual(value, D('139000.00'))               # 42000 - 3000 + 100000; never the private 888,888 or the totals

    def test_an_item_shared_later_is_left_out_of_earlier_months(self):
        today = timezone.localdate()
        end = today.replace(day=1) - timedelta(days=1)
        long_ago = timezone.make_aware(timezone.datetime(end.year, end.month, 1)) - timedelta(days=40)
        FamilyShare.objects.update(created_at=long_ago, amounts_since=long_ago)
        FamilyShare.objects.filter(account=self.kotak).update(created_at=timezone.now(), amounts_since=timezone.now())
        self.snapshot(self.owner, end, {self.world.bank_account.pk: 1000})
        self.snapshot(self.partner, end, {self.kotak.pk: 5000})
        data = summary.family_summary(self.household, self.owner)
        self.assertEqual(data.history[-1][1], D('1000.00'))

    def test_without_a_snapshot_there_is_no_history_point(self):
        FamilyShare.objects.update(created_at=timezone.now() - timedelta(days=90),
                                   amounts_since=timezone.now() - timedelta(days=90))
        self.assertEqual(summary.family_summary(self.household, self.owner).history, ())

    def test_the_chart_needs_two_points_and_ends_with_now(self):
        today = date(2026, 10, 28)
        self.assertIsNone(chart((), D('10'), today))
        graph = chart(((date(2026, 9, 30), D('100')),), D('150'), today)
        self.assertEqual(len(graph['dots']), 2)
        self.assertTrue(graph['dots'][-1]['now'])
        self.assertLess(graph['dots'][1]['y'], graph['dots'][0]['y'])          # higher value, higher on the page
        self.assertEqual(len(chart(((date(2026, m, 28), D(m)) for m in range(1, 10)), D('1'), today, months=3)['dots']), 4)


class FamilyPagesTests(ViewFixture):
    PAGES = ('family-networth', 'family-inventory')

    def test_a_member_sees_the_combined_figures_and_the_inventory(self):
        self.client.force_login(self.partner)
        page = self.client.get(reverse('family-networth'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '₹1,56,650')
        self.assertContains(page, 'Family net worth')
        self.assertContains(page, 'By member')
        inv = self.client.get(reverse('family-inventory'))
        for text in ('Joint Savings', 'Everyday Card', 'Term Cover', 'Kotak Savings', 'Who holds what'):
            self.assertContains(inv, text)
        self.assertContains(inv, 'None')

    def test_no_family_page_ever_leaks_private_data(self):
        for user in (self.partner, self.parent, self.owner):
            self.client.force_login(user)
            for name in self.PAGES:
                self.world.assert_clean(self.client.get(reverse(name)))

    def test_a_private_account_is_not_listed_or_counted_and_its_existence_is_not_hinted(self):
        self.client.force_login(self.partner)
        for name in self.PAGES:
            page = self.client.get(reverse(name))
            self.assertNotContains(page, MARKERS['private_account'])
            self.assertNotContains(page, '11,111')

    def test_the_pages_need_a_family(self):
        loner = make_user('loner3')
        self.client.force_login(loner)
        for name in self.PAGES:
            self.assertRedirects(self.client.get(reverse(name)), reverse('family-home'))

    def test_another_household_cannot_see_these_figures(self):
        outsider, friend = make_user('outsider'), make_user('friend')
        make_family(outsider, friend)
        add_account(outsider, 'Outsider Account', balance='5.00')
        self.client.force_login(friend)
        for name in self.PAGES:
            page = self.client.get(reverse(name))
            self.assertNotContains(page, 'Joint Savings')
            self.assertNotContains(page, 'Kotak')

    def test_anonymous_visitors_are_sent_to_sign_in_and_the_flag_hides_the_pages(self):
        for name in self.PAGES:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)
        self.client.force_login(self.owner)
        with override_settings(FAMILY_ENABLED=False):
            for name in self.PAGES:
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)

    def test_the_pages_are_read_only(self):
        self.client.force_login(self.owner)
        for name in self.PAGES:
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)

    def test_while_a_plan_has_ended_the_family_pages_show_no_figures(self):
        end_pro(self.partner)
        self.client.force_login(self.owner)
        for name in self.PAGES:
            page = self.client.get(reverse(name))
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, 'Family view is paused')
            for text in ('Joint Savings', '₹1,56,650', 'Kotak'):
                self.assertNotContains(page, text)
        end_pro(self.partner, days_from_now=30)
        self.assertContains(self.client.get(reverse('family-networth')), '₹1,56,650')

    def test_the_net_worth_page_explains_what_is_not_counted(self):
        shares.set_share(self.owner, self.world.card_account, 'LIST')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse('family-networth')),
                            '1 item is listed without amounts, so it is not counted')

    def test_the_range_chip_is_remembered_in_the_link(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('family-networth') + '?range=3m').status_code, 200)

    def test_the_family_pages_stay_within_a_query_budget(self):
        self.client.force_login(self.partner)
        for name in self.PAGES:
            self.client.get(reverse(name))
            with CaptureQueriesContext(connection) as queries:
                self.client.get(reverse(name))
            self.assertLessEqual(len(queries), 60, name)


class MeFamilyToggleTests(ViewFixture):
    def test_members_get_the_switch_on_accounts_and_net_worth(self):
        self.client.force_login(self.partner)
        accounts = self.client.get(reverse('account-list'))
        self.assertContains(accounts, 'fam-seg')
        self.assertContains(accounts, reverse('family-inventory'))
        networth = self.client.get(reverse('analytics-networth'))
        self.assertContains(networth, reverse('family-networth'))

    def test_the_family_pages_link_back_to_the_personal_pages(self):
        self.client.force_login(self.partner)
        self.assertContains(self.client.get(reverse('family-networth')), reverse('analytics-networth'))
        self.assertContains(self.client.get(reverse('family-inventory')), reverse('account-list'))

    def test_people_outside_a_family_see_no_switch(self):
        loner = make_user('loner4')
        self.client.force_login(loner)
        self.assertNotContains(self.client.get(reverse('account-list')), 'fam-seg')

    def test_no_switch_while_the_flag_is_off(self):
        self.client.force_login(self.partner)
        with override_settings(FAMILY_ENABLED=False):
            self.assertNotContains(self.client.get(reverse('account-list')), 'fam-seg')

    def test_the_family_side_is_disabled_while_family_view_is_paused(self):
        end_pro(self.parent)
        self.client.force_login(self.partner)
        page = self.client.get(reverse('account-list'))
        self.assertContains(page, 'is-off')


class TranslationOfFamilyPagesTests(ViewFixture):
    def test_the_pages_render_in_hindi_and_marathi(self):
        self.addCleanup(translation.activate, 'en')
        for language in ('hi', 'mr'):
            profile = self.partner.profile
            profile.language = language
            profile.save()
            self.client.force_login(self.partner)
            for name in ('family-networth', 'family-inventory'):
                page = self.client.get(reverse(name))
                self.assertEqual(page.status_code, 200)
                for english in ('Based on what is shared.', 'Family view: the combined net worth',
                                'Family view: who in the family holds'):
                    self.assertNotContains(page, english)


class RemovedMemberTests(ViewFixture):
    def test_what_a_removed_member_shared_leaves_the_totals_at_once(self):
        households.remove_member(self.owner, self.partner.household_membership)
        self.household.refresh_from_db()
        data = summary.family_summary(self.household, self.owner)
        self.assertNotIn('Kotak Savings', [i.name for i in data.items])
        self.assertEqual(data.owns, D('50650.00'))

    def test_accounts_deleted_by_their_owner_disappear(self):
        Account.objects.filter(pk=self.world.card_account.pk).delete()
        data = summary.family_summary(self.household, self.owner)
        self.assertNotIn('Everyday Card', [i.name for i in data.items])
