"""The SIP setup's Funds step reads in Hindi and Marathi, and the guide describes it in all three languages."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import UserProfile

ROOT = Path(settings.BASE_DIR)
ENGLISH = [
    'Optional. Choose the funds each instalment buys',
    'Search for a fund and enter how much of each instalment buys it',
    'more than the instalment',
]


class FundsStepTranslationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('funds-lang', password='pw')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)
        self.client.force_login(self.user)

    def page(self, language):
        UserProfile.objects.filter(user=self.user).update(language=language)
        return self.client.get(reverse('flow-detail', kwargs={'key': 'sip'})).content.decode()

    def test_the_english_page_has_the_english_text(self):
        html = self.page('en')
        for text in ENGLISH:
            self.assertIn(text, html)

    def test_hindi_and_marathi_pages_have_none_of_the_english_sentences(self):
        for language in ('hi', 'mr'):
            html = self.page(language)
            for text in ENGLISH:
                self.assertNotIn(text, html, f'{language}: {text}')

    def test_the_step_title_is_translated(self):
        for language, word in (('hi', 'फंड'), ('mr', 'फंड')):
            self.assertIn(f'<span class="text-truncate">{word}</span>', self.page(language))


class GuideTests(TestCase):
    def read(self, suffix):
        return (ROOT / 'guide-src' / '13-tmr-flows' / f'savings-and-investments{suffix}.md').read_text(encoding='utf-8')

    def test_the_funds_step_is_in_the_guide_in_every_language(self):
        for suffix, step in (('', 'Step 2: Funds'), ('.hi', 'चरण 2: Funds'), ('.mr', 'पायरी 2: Funds')):
            text = self.read(suffix)
            self.assertIn(step, text, suffix)
            self.assertIn('**Add fund**', text, suffix)
            self.assertIn('ICICI Prudential Large and midcap fund direct growth', text, suffix)

    def test_the_funds_step_comes_before_the_recurring_deposit_step(self):
        for suffix, funds, rd in (('', 'Step 2: Funds', 'Step 2: Recurring'),
                                  ('.hi', 'चरण 2: Funds', 'चरण 2: Recurring'),
                                  ('.mr', 'पायरी 2: Funds', 'पायरी 2: Recurring')):
            text = self.read(suffix)
            self.assertLess(text.index(funds), text.index(rd), suffix)

    def test_what_gets_created_mentions_the_funds_in_every_language(self):
        self.assertIn('If you chose funds', self.read(''))
        self.assertIn('अगर आपने फंड चुने हैं', self.read('.hi'))
        self.assertIn('तुम्ही फंड निवडले असतील', self.read('.mr'))
