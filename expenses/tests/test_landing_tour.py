"""The landing page's product tour ("The whole product") replaced the old feature sections."""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from update_landing_tour_translations import find_strings, missing

TOUR_KEYS = ['dash', 'add', 'bud', 'nw', 'cal', 'loan', 'goal']


class LandingTourTests(TestCase):
    def setUp(self):
        cache.clear()
        translation.activate('en')
        self.html = self.client.get(reverse('landing')).content.decode()

    def tearDown(self):
        cache.clear()

    def test_the_tour_is_on_the_page_and_the_navbar_and_footer_anchors_still_land_somewhere(self):
        self.assertIn('id="tp-sim"', self.html)
        self.assertIn('The whole product', self.html)
        # the navbar and footer link to these two
        self.assertEqual(self.html.count('id="features"'), 1)
        self.assertEqual(self.html.count('id="networth"'), 1)

    def test_every_tour_screen_has_translatable_words(self):
        for key in TOUR_KEYS:
            self.assertRegex(self.html, r'<div data-k="%s" data-l="[^"]+" data-t="[^"]+" data-d="[^"]+">' % key)

    def test_the_script_plays_exactly_the_screens_the_page_describes(self):
        js = Path(finders.find('js/landing_tour.js')).read_text(encoding='utf-8')
        order = re.search(r"ORDER=\[([^\]]*)\]", js).group(1)
        self.assertEqual(re.findall(r"'(\w+)'", order), TOUR_KEYS)

    def test_the_old_feature_sections_are_gone_and_the_ones_we_kept_are_still_there(self):
        for gone in ('id="insights"', 'demo-input', 'networth-demo-data', 'class="pain-card', 'lang-strip',
                     'Salary credited.'):
            self.assertNotIn(gone, self.html)
        for kept in ('id="family"', 'id="pricing"', 'class="founder-section"', 'class="cta-section"', 'class="hero"'):
            self.assertIn(kept, self.html)

    def test_the_stylesheet_and_script_are_linked_and_exist(self):
        self.assertIn('css/landing_tour.css', self.html)
        self.assertIn('js/landing_tour.js', self.html)
        self.assertIsNotNone(finders.find('css/landing_tour.css'))
        self.assertIsNotNone(finders.find('js/landing_tour.js'))

    def test_the_stylesheet_only_styles_its_own_classes(self):
        css = re.sub(r'/\*.*?\*/', '', Path(finders.find('css/landing_tour.css')).read_text(encoding='utf-8'), flags=re.S)
        css = re.sub(r'@keyframes[^{]+\{(?:[^{}]|\{[^{}]*\})*\}', '', css)
        for selector in re.findall(r'(?:^|\})\s*([^{}@]+)\{', css):
            for part in selector.split(','):
                part = part.strip()
                if part:
                    self.assertTrue(part.startswith(('.tp-', '.tp-sec')), 'not namespaced: %s' % part)

    def test_it_stays_cheap(self):
        self.client.get(reverse('landing'))
        with self.assertNumQueries(2):  # the two subscription plan queries; the user count is cached
            self.client.get(reverse('landing'))


class LandingTourTranslationTests(TestCase):
    def test_every_tour_string_has_hindi_and_marathi(self):
        self.assertGreater(len(find_strings()), 30)
        self.assertEqual(missing(), [], 'run python update_landing_tour_translations.py and compilemessages')

    def test_the_tour_is_translated_on_the_page(self):
        for lang, word in (('hi', 'पूरा प्रोडक्ट'), ('mr', 'संपूर्ण प्रॉडक्ट')):
            cache.clear()
            html = self.client.get(reverse('landing'), HTTP_ACCEPT_LANGUAGE=lang).content.decode()
            self.assertIn(word, html, lang)
        translation.activate('en')
