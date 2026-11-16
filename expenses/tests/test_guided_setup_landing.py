"""The Guided Setup page: the setups still to do come first, in the order that pays off soonest, and on a phone each
one is a single tappable row."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils.html import escape
from django.urls import reverse

from expenses.models import Account, UserProfile

CURATED = ['salary', 'loan', 'sip', 'creditcard', 'rentbill']


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('landing-user', password='pw')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True, tier='PRO')
        self.client.force_login(self.user)

    def get(self):
        return self.client.get(reverse('tmr-flows'))


class OrderTests(Base):
    def test_to_do_setups_start_with_the_ones_most_people_want_first(self):
        keys = [f['key'] for f in self.get().context['flows']]
        self.assertEqual(keys[:5], CURATED)

    def test_the_first_three_still_to_do_are_marked_as_the_place_to_start(self):
        flows = self.get().context['flows']
        self.assertEqual([f['key'] for f in flows if f['is_suggested']], ['salary', 'loan', 'sip'])

    def test_a_setup_that_is_done_leaves_the_front_and_the_next_one_takes_its_mark(self):
        Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        flows = self.get().context['flows']
        keys = [f['key'] for f in flows]
        sip = next(f for f in flows if f['key'] == 'sip')
        self.assertTrue(sip['is_configured'])
        self.assertEqual(keys[-1], 'sip')                                   # done ones follow the ones still to do
        self.assertEqual([f['key'] for f in flows if f['is_suggested']], ['salary', 'loan', 'creditcard'])

    def test_every_flow_is_still_listed_exactly_once(self):
        from expenses.flows.registry import FlowRegistry
        keys = [f['key'] for f in self.get().context['flows']]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), {f.key for f in FlowRegistry.all().values() if f.is_available(self.user)})

    def test_the_first_card_is_the_one_selected_on_a_large_screen(self):
        self.assertEqual(self.get().context['initial_flow_key'], 'salary')


class PhoneMarkupTests(Base):
    def test_each_to_do_setup_has_a_phone_only_tap_link_covering_its_card(self):
        response = self.get()
        html = response.content.decode()
        todo = [f for f in response.context['flows'] if not f['is_configured']]
        self.assertEqual(html.count('class="gcard-tap stretched-link d-md-none"'), len(todo))
        for flow in todo:
            self.assertIn(f'href="{flow["setup_url"]}" class="gcard-tap', html)
            self.assertIn(f'aria-label="{escape(flow["title"])}"', html)

    def test_the_open_setup_button_stays_for_larger_screens(self):
        response = self.get()
        todo = [f for f in response.context['flows'] if not f['is_configured']]
        self.assertEqual(response.content.decode().count('class="gbtn">Open setup'), len(todo))

    def test_start_here_shows_on_three_cards_and_only_on_phones(self):
        html = self.get().content.decode()
        self.assertEqual(html.count('class="gstart d-md-none"'), 3)

    def test_the_progress_strip_says_how_many_are_set_up(self):
        response = self.get()
        total = response.context['total_count']
        self.assertContains(response, f'0 of {total} set up')
        Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        self.assertContains(self.get(), f'1 of {total} set up')

    def test_the_side_panels_are_hidden_on_a_phone_and_kept_for_larger_screens(self):
        self.assertContains(self.get(), 'class="tmr-flow-side side d-none d-md-block"')

    def test_the_request_card_becomes_a_line_of_text_on_a_phone(self):
        html = self.get().content.decode()
        self.assertIn('class="reqcard d-none d-md-flex"', html)
        self.assertIn('d-md-none text-center small text-muted mb-0', html)

    def test_done_setups_fold_into_one_row_on_a_phone(self):
        self.assertNotContains(self.get(), 'tmr-flow-done-toggle')              # nothing done yet: no row
        Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=0, currency='₹')
        response = self.get()
        self.assertContains(response, 'tmr-flow-done-toggle')
        self.assertContains(response, 'gcard gcard--done')
        self.assertContains(response, 'Edit existing')                          # the done setup keeps both links

    def test_the_new_text_is_translated(self):
        for language in ('hi', 'mr'):
            UserProfile.objects.filter(user=self.user).update(language=language)
            html = self.get().content.decode()
            self.assertNotIn('d-md-none">Start here</span>', html, language)
            self.assertNotIn('set up</div>', html, language)
