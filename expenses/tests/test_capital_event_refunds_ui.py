"""Capital events show what has been paid back against them, and "Add refund" opens the income form linked to the
event. The refund numbers are the same ones the savings maths uses (income linked to the event)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from expenses.models import Account, CapitalEvent, Income, UserProfile
from expenses.tests.test_translations import compile_catalogues

D = Decimal


class CapitalEventRefundUiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cer', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=0, currency='₹')
        self.event = CapitalEvent.objects.create(user=self.user, date=date.today(), amount=D('20000'), subtype='other',
                                                 note='Goa trip stay', currency='₹', account=self.account)

    def refund(self, amount, **extra):
        return Income.objects.create(user=self.user, date=date.today(), amount=D(amount), source_type='Refund / Reimbursement',
                                     currency='₹', account=self.account, refund_for_capital_event=self.event, **extra)

    def listing(self):
        return self.client.get(reverse('capital-event-list'), {'time_period': 'all'})

    def test_list_shows_refunded_and_net(self):
        self.refund('10000')
        self.refund('5000')
        page = self.listing()
        row = page.context['events'][0]
        self.assertEqual((row.refunded_total, row.net_total), (D('15000'), D('5000')))
        self.assertContains(page, 'refunded')
        self.assertContains(page, 'Net')

    def test_an_event_with_no_refunds_looks_as_before(self):
        page = self.listing()
        row = page.context['events'][0]
        self.assertEqual((row.refunded_total, row.net_total), (D('0'), D('20000')))
        self.assertNotContains(page, 'refunded')

    def test_deleted_refunds_do_not_count(self):
        r = self.refund('15000')
        Income.objects.filter(pk=r.pk).update(is_deleted=True)
        self.assertEqual(self.listing().context['events'][0].refunded_total, D('0'))

    def test_list_total_is_not_multiplied_by_the_number_of_refunds(self):
        for _ in range(3):
            self.refund('1000')
        self.assertEqual(self.listing().context['filtered_amount'], D('20000'))

    def test_add_refund_action_is_offered_once_in_each_layout(self):
        # one in the desktop table's menu and one in the mobile card's menu
        self.assertContains(self.listing(), f'refund_for={self.event.uuid}', count=2)

    def test_income_form_opens_linked_to_the_event(self):
        page = self.client.get(reverse('income-create'), {'refund_for': str(self.event.uuid)})
        form = page.context['form']
        self.assertEqual(form.initial['refund_for_capital_event'], self.event.pk)
        self.assertEqual(form.initial['source_type'], 'Refund / Reimbursement')
        self.assertEqual(form.initial['account'], self.account.pk)

    def test_refund_for_ignores_other_users_events_and_junk(self):
        other = User.objects.create_user('cer2', password='x')
        UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        foreign = CapitalEvent.objects.create(user=other, date=date.today(), amount=D('50'), subtype='other', currency='₹')
        for raw in (str(foreign.uuid), 'not-a-uuid', '99999999'):
            page = self.client.get(reverse('income-create'), {'refund_for': raw})
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('refund_for_capital_event', page.context['form'].initial)

    def test_edit_page_lists_the_refunds(self):
        self.refund('15000')
        page = self.client.get(reverse('capital-event-edit', args=[self.event.uuid]))
        self.assertEqual(len(page.context['refunds']), 1)
        self.assertEqual((page.context['refunded_total'], page.context['net_total']), (D('15000'), D('5000')))
        self.assertContains(page, 'Refunded so far')

    def test_edit_page_with_no_refunds_invites_one(self):
        page = self.client.get(reverse('capital-event-edit', args=[self.event.uuid]))
        self.assertContains(page, 'Nothing has been paid back')


class RefundStringsAreTranslatedTests(TestCase):
    """The words of the refund feature read in Hindi and Marathi, not English."""

    STRINGS = [
        'Refund for', 'Not linked: counts as income', 'Refunds', 'Add refund', 'Refunded so far', 'Net cost',
        'Paid back to you against this purchase', 'refunded', 'Net', 'Pays back',
        'Pays back a big purchase: reduces it instead of counting as income',
        'Pick the big purchase this pays back, so it reduces that purchase instead of adding to your income.',
        'Nothing has been paid back against this purchase yet. If someone repays you for part of it, add it as a '
        'refund and only your own share stays counted.',
    ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        compile_catalogues()

    def test_every_string_is_translated_into_a_devanagari_text(self):
        import re
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                for msg in self.STRINGS:
                    shown = translation.gettext(msg)
                    self.assertNotEqual(shown, msg, (lang, msg))
                    self.assertTrue(re.search('[\u0900-\u097F]', shown), (lang, msg))
