from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from expenses.public_features import feature_categories

DEVANAGARI = '\u0900-\u097f'


def english_categories():
    with translation.override('en'):
        return feature_categories()


class FeaturesPageTests(TestCase):
    def test_every_feature_is_on_the_page_and_in_features_md(self):
        html = self.client.get(reverse('features'), HTTP_ACCEPT_LANGUAGE='en').content.decode()
        md = open('FEATURES.md', encoding='utf-8').read()
        categories = english_categories()
        self.assertGreaterEqual(len(categories), 8)
        for category in categories:
            self.assertIn(f'id="{category["id"]}"', html)
            self.assertIn(f'href="#{category["id"]}"', html)
            self.assertIn(category['title'], md)
            self.assertTrue(category['items'])
            for item in category['items']:
                self.assertTrue(item['title'] and item['desc'])
                self.assertIn(item['title'].replace("'", '&#x27;'), html)
                self.assertIn(item['title'], md)

    def test_the_page_makes_no_claims_the_app_does_not_back(self):
        text = ' '.join(
            f"{i['title']} {i['desc']}" for c in english_categories() for i in c['items']
        ).lower()
        for banned in ('ai-powered', 'bank sync', 'auto-sync', 'sms parsing'):
            self.assertNotIn(banned, text)

    def test_it_is_translated(self):
        import re
        for lang in ('hi', 'mr'):
            html = self.client.get(reverse('features'), HTTP_ACCEPT_LANGUAGE=lang).content.decode()
            self.assertRegex(html, f'<h2>[{DEVANAGARI}]', lang)
            # No feature card is left in English
            english = [i['title'] for c in english_categories() for i in c['items']]
            self.assertFalse([t for t in english if t.replace("'", '&#x27;') in html], lang)
