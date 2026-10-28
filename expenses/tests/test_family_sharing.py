"""Phase 2 of family sharing: choosing what to share, the narrow door that reads it, and the preview."""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone, translation

from expenses.family import households, shares, summary
from expenses.models import (
    Account,
    FamilyShare,
    FamilyShareEvent,
    HouseholdMember,
    Loan,
    LoanScheduleInstallment,
    Notification,
)

from .family_base import FamilyTestCase, add_account, make_family, make_user
from .family_canary import MARKERS, CanaryWorld

D = Decimal


@override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True)
class SharingFixture(FamilyTestCase):
    def setUp(self):
        super().setUp()
        self.household = make_family(self.owner, self.partner, self.parent)
        self.world = CanaryWorld.seed(self.owner)


class ChoosingWhatToShareTests(SharingFixture):
    def test_nothing_is_shared_until_the_owner_shares_it(self):
        self.assertEqual(FamilyShare.objects.count(), 0)
        self.assertEqual(summary.shared_items(self.owner, self.household), [])

    def test_sharing_an_account_records_it_and_bumps_the_cache_version(self):
        before = self.household.cache_version
        share, created = shares.set_share(self.owner, self.world.bank_account, FamilyShare.AMOUNTS)
        self.assertTrue(created)
        self.assertEqual((share.owner, share.household, share.level), (self.owner, self.household, 'AMOUNTS'))
        self.household.refresh_from_db()
        self.assertGreater(self.household.cache_version, before)
        self.assertEqual(FamilyShareEvent.objects.get().kind, 'SHARED')

    def test_changing_the_level_or_the_nickname_is_logged_once(self):
        account = self.world.bank_account
        shares.set_share(self.owner, account, 'AMOUNTS')
        shares.set_share(self.owner, account, 'AMOUNTS')             # nothing changed
        shares.set_share(self.owner, account, 'LIST', alias='Joint')
        kinds = list(FamilyShareEvent.objects.order_by('id').values_list('kind', flat=True))
        self.assertEqual(kinds, ['SHARED', 'CHANGED'])
        share = FamilyShare.objects.get()
        self.assertEqual((share.level, share.alias), ('LIST', 'Joint'))

    def test_a_nickname_equal_to_the_account_name_is_not_stored(self):
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS', alias='  Our   joint ')
        self.assertEqual(FamilyShare.objects.get().alias, 'Our joint')
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS', alias='Joint Savings')
        self.assertEqual(FamilyShare.objects.get().alias, '')

    def test_stopping_removes_the_share_and_is_logged(self):
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        self.assertTrue(shares.stop_share(self.owner, self.world.bank_account))
        self.assertFalse(shares.stop_share(self.owner, self.world.bank_account))
        self.assertEqual(FamilyShare.objects.count(), 0)
        self.assertEqual(FamilyShareEvent.objects.first().kind, 'STOPPED')

    def test_only_your_own_active_accounts_can_be_shared(self):
        theirs = add_account(self.partner, 'Hers')
        with self.assertRaises(households.FamilyError):
            shares.set_share(self.owner, theirs, 'AMOUNTS')
        Account.objects.filter(pk=self.world.bank_account.pk).update(is_active=False)
        self.world.bank_account.refresh_from_db()
        with self.assertRaises(households.FamilyError):
            shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')

    def test_a_level_that_does_not_exist_is_refused(self):
        with self.assertRaises(households.FamilyError):
            shares.set_share(self.owner, self.world.bank_account, 'EVERYTHING')

    def test_someone_outside_a_family_cannot_share(self):
        loner = make_user('loner')
        with self.assertRaises(households.FamilyError):
            shares.set_share(loner, add_account(loner, 'Mine'), 'AMOUNTS')

    def test_who_a_policy_covers_is_saved_with_the_share(self):
        shares.set_share(self.owner, self.world.insurance_account, 'AMOUNTS', covers='  Arjun,   Priya ')
        self.world.policy.refresh_from_db()
        self.assertEqual(self.world.policy.covers, 'Arjun, Priya')

    def test_leaving_the_family_stops_everything_that_was_shared(self):
        shares.set_share(self.partner, add_account(self.partner, 'Kotak'), 'AMOUNTS')
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        households.leave(self.partner)
        self.assertEqual(list(FamilyShare.objects.values_list('owner_id', flat=True)), [self.owner.pk])

    def test_being_removed_stops_everything_that_was_shared(self):
        shares.set_share(self.partner, add_account(self.partner, 'Kotak'), 'AMOUNTS')
        households.remove_member(self.owner, HouseholdMember.objects.get(user=self.partner))
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_family_note_goes_out_only_if_the_sharer_asked_for_it(self):
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        self.assertEqual(Notification.objects.count(), 0)
        HouseholdMember.objects.filter(user=self.owner).update(notify_new_shares=True)
        shares.set_share(self.owner, self.world.card_account, 'AMOUNTS', alias='Everyday')
        notes = list(Notification.objects.order_by('user_id'))
        self.assertEqual({n.user_id for n in notes}, {self.partner.pk, self.parent.pk})
        self.world.assert_clean(*notes)
        self.assertIn('Everyday', notes[0].message)


class WhatFamilySeesTests(SharingFixture):
    """The narrow door returns a fixed set of plain values."""

    def share_all(self, level='AMOUNTS'):
        for account in (self.world.bank_account, self.world.card_account, self.world.insurance_account):
            shares.set_share(self.owner, account, level)

    def test_an_unshared_account_is_not_in_the_result_at_all(self):
        self.share_all()
        names = [i.name for i in summary.shared_items(self.owner, self.household)]
        self.assertNotIn(MARKERS['private_account'], names)
        self.assertEqual(sorted(names), ['Everyday Card', 'Joint Savings', 'Term Cover'])

    def test_amounts_are_the_owners_own_valuation_and_a_card_shows_its_limit(self):
        self.share_all()
        by_name = {i.name: i for i in summary.shared_items(self.owner, self.household)}
        self.assertEqual(by_name['Joint Savings'].amount, D('50650.00'))
        card = by_name['Everyday Card']
        self.assertEqual((card.amount, card.limit, card.available), (D('-4000.00'), D('100000.00'), D('96000.00')))
        self.assertIsNotNone(card.next_bill)

    def test_an_insurance_policy_shows_cover_premium_due_date_and_who_it_covers(self):
        policy = self.world.policy
        policy.covers, policy.policy_start_date, policy.premium_frequency = 'Arjun, Priya', date(2020, 3, 10), 'ANNUAL'
        policy.save()
        shares.set_share(self.owner, self.world.insurance_account, 'AMOUNTS')
        item = summary.shared_items(self.owner, self.household)[0]
        self.assertEqual((item.cover, item.premium, item.premium_per), (D('5000000.00'), D('12000.00'), 'a year'))
        self.assertEqual(item.covers, 'Arjun, Priya')
        self.assertGreaterEqual(item.next_due, timezone.localdate())
        self.assertEqual((item.next_due.month, item.next_due.day), (3, 10))

    def test_a_loan_shows_the_next_emi(self):
        loan = Loan.objects.create(user=self.owner, name='Home', loan_type='HOME', initial_principal=D('100000.00'),
                                   duration_months=12, start_date=date(2026, 1, 1))
        LoanScheduleInstallment.objects.create(
            loan=loan, installment_no=1, due_date=date(2026, 2, 1), scheduled_principal=D('8000.00'),
            scheduled_interest=D('500.00'), scheduled_balance=D('92000.00'), is_paid=True)
        LoanScheduleInstallment.objects.create(
            loan=loan, installment_no=2, due_date=date(2026, 3, 1), scheduled_principal=D('8100.00'),
            scheduled_interest=D('400.00'), scheduled_balance=D('83900.00'))
        account = Account.objects.create(user=self.owner, name='Home loan', account_type='HOME_LOAN',
                                         balance=D('-83900.00'), currency='₹', linked_loan=loan)
        shares.set_share(self.owner, account, 'AMOUNTS')
        item = summary.shared_items(self.owner, self.household)[0]
        self.assertEqual(item.emi, D('8500.00'))
        self.assertLess(item.amount, 0)
        self.assertEqual(item.kind, 'loan')

    def test_list_only_shows_the_name_and_type_and_nothing_else(self):
        self.share_all(level='LIST')
        for item in summary.shared_items(self.owner, self.household):
            self.assertFalse(item.show_amounts)
            for field in ('amount', 'limit', 'available', 'next_bill', 'cover', 'premium', 'next_due', 'emi'):
                self.assertIsNone(getattr(item, field), field)
            self.assertEqual(item.covers, '')

    def test_a_nickname_replaces_the_account_name(self):
        shares.set_share(self.owner, self.world.card_account, 'AMOUNTS', alias='Household card')
        self.assertEqual(summary.shared_items(self.owner, self.household)[0].name, 'Household card')

    def test_an_inactive_account_drops_out(self):
        self.share_all()
        Account.objects.filter(pk=self.world.bank_account.pk).update(is_active=False)
        names = [i.name for i in summary.shared_items(self.owner, self.household)]
        self.assertNotIn('Joint Savings', names)

    def test_nothing_private_is_anywhere_in_what_the_door_returns(self):
        self.share_all()
        self.world.assert_clean(summary.shared_items(self.owner, self.household))

    def test_next_premium_due_steps_by_the_payment_frequency(self):
        today = date(2026, 10, 28)
        self.assertEqual(summary.next_premium_due(date(2020, 3, 10), 'ANNUAL', today), date(2027, 3, 10))
        self.assertEqual(summary.next_premium_due(date(2026, 8, 31), 'QUARTERLY', today), date(2026, 11, 30))
        self.assertEqual(summary.next_premium_due(date(2026, 12, 1), 'MONTHLY', today), date(2026, 12, 1))
        self.assertIsNone(summary.next_premium_due(None, 'ANNUAL', today))
        self.assertIsNone(summary.next_premium_due(date(2020, 1, 1), None, today))


class SharingPagesTests(SharingFixture):
    def test_the_what_i_share_page_lists_every_account_with_its_state(self):
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-share'))
        self.assertEqual(page.status_code, 200)
        for text in ('Joint Savings', 'Everyday Card', 'Term Cover', 'Amount counts', 'Sharing activity', '1 of '):
            self.assertContains(page, text)
        self.assertNotContains(page, 'class="fam-private"')     # an unshared row is just an off switch, not a repeated label
        self.assertContains(page, 'Sharing 1 of your 4 items.')

    def test_the_switch_shares_and_unshares(self):
        self.client.force_login(self.owner)
        url = reverse('family-share-toggle', args=[self.world.bank_account.uuid])
        self.assertRedirects(self.client.post(url), reverse('family-share'))
        self.assertEqual(FamilyShare.objects.get().level, 'AMOUNTS')
        self.client.post(url)
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_dialog_saves_the_level_nickname_and_who_a_policy_covers(self):
        self.client.force_login(self.owner)
        url = reverse('family-share-edit', args=[self.world.insurance_account.uuid])
        self.assertContains(self.client.get(url), 'Who does this policy cover?')
        self.client.post(url, {'level': 'AMOUNTS', 'alias': 'Term', 'covers': 'Arjun'})
        share = FamilyShare.objects.get()
        self.assertEqual((share.level, share.alias), ('AMOUNTS', 'Term'))
        self.world.policy.refresh_from_db()
        self.assertEqual(self.world.policy.covers, 'Arjun')

    def test_the_dialog_for_a_bank_account_has_no_covers_field(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-share-edit', args=[self.world.bank_account.uuid]))
        self.assertNotContains(page, 'Who does this policy cover?')

    def test_stop_sharing_from_the_dialog(self):
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        self.client.force_login(self.owner)
        self.client.post(reverse('family-share-stop', args=[self.world.bank_account.uuid]))
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_notification_switch_flips(self):
        self.client.force_login(self.owner)
        self.client.post(reverse('family-share-notify'))
        self.assertTrue(HouseholdMember.objects.get(user=self.owner).notify_new_shares)
        self.client.post(reverse('family-share-notify'))
        self.assertFalse(HouseholdMember.objects.get(user=self.owner).notify_new_shares)

    def test_a_bad_level_is_refused_with_a_message_not_an_error(self):
        self.client.force_login(self.owner)
        page = self.client.post(reverse('family-share-edit', args=[self.world.bank_account.uuid]),
                                {'level': 'x'})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_share_page_stays_within_a_query_budget(self):
        for account in (self.world.bank_account, self.world.card_account, self.world.insurance_account):
            shares.set_share(self.owner, account, 'AMOUNTS')
        self.client.force_login(self.owner)
        self.client.get(reverse('family-share'))          # warm caches
        with CaptureQueriesContext(connection) as queries:
            self.client.get(reverse('family-share'))
        self.assertLessEqual(len(queries), 40)


class PreviewTests(SharingFixture):
    def setUp(self):
        super().setUp()
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        shares.set_share(self.owner, self.world.card_account, 'LIST')

    def test_the_preview_shows_what_each_member_sees_and_what_stays_hidden(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-preview'))
        self.assertContains(page, 'Previewing as priya')
        self.assertContains(page, 'Joint Savings')
        self.assertContains(page, '₹50,650')
        self.assertContains(page, 'Amounts not shared')
        self.assertContains(page, 'Amounts are hidden on 1 listed item.')
        self.assertContains(page, 'Everyday Card')
        self.assertContains(page, 'Only you see this note')

    def test_the_preview_can_switch_between_members(self):
        self.client.force_login(self.owner)
        meera = HouseholdMember.objects.get(user=self.parent)
        page = self.client.get(reverse('family-preview-member', args=[meera.pk]))
        self.assertContains(page, 'Previewing as meera')
        self.assertEqual(self.client.get(reverse('family-preview-member', args=[999999])).status_code, 404)

    def test_the_preview_never_contains_a_transaction_a_category_a_note_or_a_number(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse('family-preview'))
        # The owner's own note lists the private account by name; nothing else private may appear
        self.world.assert_clean(page, allow=['private_account'])

    def test_a_member_previews_only_their_own_items_never_the_owners(self):
        self.client.force_login(self.partner)
        page = self.client.get(reverse('family-preview'))
        self.assertNotContains(page, 'Joint Savings')
        self.world.assert_clean(page)


class AccessTests(SharingFixture):
    URLS = ('family-share', 'family-preview', 'family-share-notify')

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        for name in self.URLS:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302)

    def test_someone_outside_a_family_is_sent_to_the_family_page(self):
        loner = make_user('loner2')
        self.client.force_login(loner)
        self.assertRedirects(self.client.get(reverse('family-share')), reverse('family-home'))
        self.assertRedirects(self.client.get(reverse('family-preview')), reverse('family-home'))

    def test_nobody_can_open_the_share_page_of_someone_elses_account(self):
        self.client.force_login(self.partner)
        for name in ('family-share-edit',):
            self.assertEqual(self.client.get(reverse(name, args=[self.world.bank_account.uuid])).status_code, 404)
        for name in ('family-share-toggle', 'family-share-stop'):
            self.assertEqual(self.client.post(reverse(name, args=[self.world.bank_account.uuid])).status_code, 404)
        self.assertEqual(FamilyShare.objects.count(), 0)

    def test_the_pages_do_not_exist_while_the_flag_is_off(self):
        self.client.force_login(self.owner)
        with override_settings(FAMILY_ENABLED=False):
            for name in self.URLS:
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
            self.assertEqual(
                self.client.get(reverse('family-share-edit', args=[self.world.bank_account.uuid])).status_code, 404)

    def test_the_sharing_pages_need_post_to_change_anything(self):
        self.client.force_login(self.owner)
        for name in ('family-share-toggle', 'family-share-stop'):
            self.assertEqual(self.client.get(reverse(name, args=[self.world.bank_account.uuid])).status_code, 405)
        self.assertEqual(self.client.get(reverse('family-share-notify')).status_code, 405)

    def test_sharing_continues_to_be_editable_while_family_view_is_paused(self):
        from .family_base import end_pro
        end_pro(self.partner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('family-share')).status_code, 200)


class TranslationTests(SharingFixture):
    def test_the_pages_render_in_hindi_and_marathi(self):
        self.addCleanup(translation.activate, 'en')
        shares.set_share(self.owner, self.world.bank_account, 'AMOUNTS')
        for language, heading in (('hi', 'मैं क्या साझा करता हूँ'), ('mr', 'मी काय शेअर करतो')):
            profile = self.owner.profile
            profile.language = language
            profile.save()
            self.client.force_login(self.owner)
            for url in (reverse('family-share'), reverse('family-preview'),
                        reverse('family-share-edit', args=[self.world.bank_account.uuid])):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, 'Switch on the accounts')
            self.assertContains(self.client.get(reverse('family-share')), heading)
