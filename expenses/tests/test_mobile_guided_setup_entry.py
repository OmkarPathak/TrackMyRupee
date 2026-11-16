"""On a phone, Guided Setup is offered where people add things: a row in the Add sheet, and the tile in More."""
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import UserProfile


class MobileGuidedSetupEntryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('phone-user', password='pw')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)
        self.client.force_login(self.user)

    def page(self):
        return self.client.get(reverse('account-list')).content.decode()

    def sheet(self, html, sheet_id):
        start = html.index(f'id="{sheet_id}"')
        end = html.index('<!-- Mobile', start) if '<!-- Mobile' in html[start:] else len(html)
        return html[start:end]

    def test_the_add_sheet_has_a_guided_setup_row_that_says_what_it_sets_up(self):
        add = self.sheet(self.page(), 'addActionsSheet')
        self.assertIn(reverse('tmr-flows'), add)
        self.assertIn('Set up a loan, SIP or salary', add)
        self.assertIn('Step by step, in about a minute', add)

    def test_the_row_comes_after_the_everyday_actions(self):
        add = self.sheet(self.page(), 'addActionsSheet')
        self.assertLess(add.index(reverse('income-create')), add.index(reverse('tmr-flows')))

    def test_the_more_sheet_keeps_its_guided_setup_tile(self):
        more = self.sheet(self.page(), 'moreMenuSheet')
        self.assertRegex(more, re.compile(r'href="%s".*?Guided Setup' % re.escape(reverse('tmr-flows')), re.S))

    def test_the_new_text_is_translated(self):
        for language in ('hi', 'mr'):
            UserProfile.objects.filter(user=self.user).update(language=language)     # the page follows the profile
            add = self.sheet(self.page(), 'addActionsSheet')
            self.assertNotIn('Set up a loan, SIP or salary', add, language)
            self.assertNotIn('Step by step, in about a minute', add, language)
