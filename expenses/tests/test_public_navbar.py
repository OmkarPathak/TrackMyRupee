"""The public navbar (landing page, features page, EMI calculator): the link to the user guide."""
import re

from django.test import TestCase
from django.urls import reverse

GUIDE = 'https://docs.trackmyrupee.com/'


def header(html):
    return html[html.index('<header class="landing-nav">'):html.index('</header>')]


def mobile_menu(html):
    start = html.index('<nav class="mobile-menu"')
    return html[start:html.index('</nav>', start)]


class GuideLinkTests(TestCase):
    def page(self, name='landing', **headers):
        return self.client.get(reverse(name), **headers).content.decode()

    def guide_links(self, fragment):
        """The guide addresses in a fragment, without the tracking parameters every link carries (they are checked below)."""
        return [link.split('?')[0] for link in re.findall(r'<a [^>]*href="(https://docs\.trackmyrupee\.com/[^"]*)"[^>]*>', fragment)]

    def test_the_links_say_they_come_from_the_public_navbar(self):
        html = self.page()
        for fragment in (header(html), mobile_menu(html)):
            link = re.search(r'href="(https://docs\.trackmyrupee\.com/[^"]*)"', fragment).group(1)
            self.assertIn('utm_source=trackmyrupee-app', link)
            self.assertIn('utm_content=public-navbar', link)

    def test_the_landing_page_navbar_links_to_the_guide(self):
        html = self.page()
        self.assertEqual(self.guide_links(header(html)), [GUIDE])
        self.assertIn('>Guide<', header(html))

    def test_the_phone_menu_links_to_the_guide_too(self):
        self.assertEqual(self.guide_links(mobile_menu(self.page())), [GUIDE])

    def test_the_guide_opens_in_a_new_tab_without_leaking_the_page(self):
        html = self.page()
        for fragment in (header(html), mobile_menu(html)):
            tag = re.search(r'<a [^>]*docs\.trackmyrupee\.com[^>]*>', fragment).group(0)
            self.assertIn('target="_blank"', tag)
            self.assertIn('rel="noopener"', tag)

    def test_it_sits_before_the_blog_and_after_the_demo(self):
        nav = header(self.page())
        self.assertLess(nav.index('Live Demo'), nav.index('docs.trackmyrupee.com'))
        self.assertLess(nav.index('docs.trackmyrupee.com'), nav.index('>Blog<'))

    def test_the_other_public_pages_share_the_link(self):
        for name in ('features', 'loan-emi-calculator'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertEqual(self.guide_links(header(html)), [GUIDE], name)

    def test_hindi_and_marathi_readers_get_the_guide_in_their_language(self):
        for code, expected in (('hi', GUIDE + 'hi/'), ('mr', GUIDE + 'mr/'), ('en', GUIDE)):
            html = self.page(HTTP_ACCEPT_LANGUAGE=code)
            self.assertEqual(self.guide_links(header(html)), [expected], code)
            self.assertEqual(self.guide_links(mobile_menu(html)), [expected], code)

    def test_the_label_is_translated(self):
        html = self.page(HTTP_ACCEPT_LANGUAGE='hi')
        self.assertNotIn('>Guide<', header(html))
        self.assertIn('>गाइड<', header(html))
        html = self.page(HTTP_ACCEPT_LANGUAGE='mr')
        self.assertIn('>मार्गदर्शिका<', header(html))
