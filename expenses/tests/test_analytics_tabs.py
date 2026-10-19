"""Analytics is one place with two tabs: "Spending & cashflow" (/analytics/) and "Net worth & trends" (/analytics/mom/).

The sidebar has a single Analytics entry that stays lit on both, and the tab strip is on both pages so a person can
always get to the other one, including on a phone where the old separate "Trends" link was not offered at all.
"""
import re
from unittest.mock import PropertyMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

ANALYTICS = reverse('analytics')
TRENDS = reverse('analytics-mom')


def _tab_strip(html):
    match = re.search(r'<ul class="nav nav-pills analytics-tabs.*?</ul>', html, re.S)
    assert match, 'tab strip not found'
    return match.group(0)


class AnalyticsTabsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='tabs', password='pw-tabs-123')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.tier = 'PLUS'
        profile.save()
        self.client.login(username='tabs', password='pw-tabs-123')

    def _active_tab_hrefs(self, html):
        strip = _tab_strip(html)
        return [m.group(1) for m in re.finditer(r'<a class="nav-link[^"]*\bactive\b[^"]*"\s+href="([^"]+)"', strip)]

    def test_both_pages_show_both_tabs(self):
        for url in (ANALYTICS, TRENDS):
            with self.subTest(url=url):
                strip = _tab_strip(self.client.get(url).content.decode())
                self.assertIn(f'href="{ANALYTICS}"', strip)
                self.assertIn(f'href="{TRENDS}"', strip)
                self.assertIn('Spending & cashflow', strip)
                self.assertIn('Net worth & trends', strip)

    def test_the_tab_for_the_current_page_is_the_active_one(self):
        self.assertEqual(self._active_tab_hrefs(self.client.get(ANALYTICS).content.decode()), [ANALYTICS])
        self.assertEqual(self._active_tab_hrefs(self.client.get(TRENDS).content.decode()), [TRENDS])

    def test_the_active_tab_is_marked_for_screen_readers(self):
        strip = _tab_strip(self.client.get(TRENDS).content.decode())
        self.assertEqual(strip.count('aria-current="page"'), 1)

    def test_the_locked_analytics_page_still_offers_the_tabs(self):
        # every plan has the insights switched on today; this is the page a plan without them would see
        with patch.object(type(self.user.profile), 'has_ai_access', new_callable=PropertyMock, return_value=False):
            response = self.client.get(ANALYTICS)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_locked'])
        self.assertIn(f'href="{TRENDS}"', _tab_strip(response.content.decode()))

    def test_the_tabs_come_after_the_title_on_every_version_of_both_pages(self):
        with patch.object(type(self.user.profile), 'has_ai_access', new_callable=PropertyMock, return_value=False):
            locked = self.client.get(ANALYTICS).content.decode()
        pages = {'analytics': self.client.get(ANALYTICS).content.decode(),
                 'trends': self.client.get(TRENDS).content.decode(), 'locked': locked}
        for name, html in pages.items():
            with self.subTest(page=name):
                title = html.index('data-analytics-title')
                self.assertLess(title, html.index('<ul class="nav nav-pills analytics-tabs'))

    def test_the_filters_sit_with_the_tabs_not_above_the_title(self):
        html = self.client.get(ANALYTICS).content.decode()
        title = html.index('data-analytics-title')
        tabs = html.index('<ul class="nav nav-pills analytics-tabs')
        self.assertLess(title, tabs)
        self.assertLess(tabs, html.index('id="analyticsYear"'))
        self.assertLess(tabs, html.index('id="includeCapitalEvents"'))
        # the year and capital-events filters belong to the spending tab only
        self.assertNotIn('id="analyticsYear"', self.client.get(TRENDS).content.decode())

    def test_title_tabs_and_sections_share_one_content_column(self):
        # A stray closing tag once ended the page's content column after the header, so on wide screens the
        # section titles sat further left than the title and tabs. Everything must live in the same column.
        from bs4 import BeautifulSoup
        for url, anchors in ((ANALYTICS, ['Financial Health', 'Spending Behavior', 'Cashflow']),
                             (TRENDS, [])):
            with self.subTest(url=url):
                soup = BeautifulSoup(self.client.get(url).content, 'html.parser')
                column = soup.select_one('main .main-content-container')
                self.assertIsNotNone(column)
                self.assertIsNotNone(column.select_one('[data-analytics-title]'))
                self.assertIsNotNone(column.select_one('.analytics-tabs'))
                for text in anchors:
                    heading = next((h for h in soup.find_all('h2') if h.get_text(strip=True) == text), None)
                    self.assertIsNotNone(heading, text)
                    self.assertIn(column, heading.parents, f'"{text}" is outside the content column')

    def test_the_trends_page_has_no_extra_centring_wrapper(self):
        # an inner .container would re-centre the content, so it jumped sideways when switching tabs
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(self.client.get(TRENDS).content, 'html.parser')
        column = soup.select_one('main .main-content-container')
        self.assertIsNone(column.select_one('.container'))

    def test_both_pages_are_titled_analytics(self):
        for url in (ANALYTICS, TRENDS):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertIn('<title>Analytics - TrackMyRupee</title>', html)
        self.assertNotIn('Analytics & Trends', self.client.get(ANALYTICS).content.decode())

    def test_the_sidebar_has_one_analytics_entry_that_is_lit_on_both_pages(self):
        for url in (ANALYTICS, TRENDS):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                nav = re.findall(r'<a href="/analytics/" class="nav-link d-flex align-items-center gap-3[^"]*"[^>]*>', html)
                self.assertEqual(len(nav), 1)
                self.assertIn(' active', nav[0])
                # no separate "Trends" item in the sidebar any more
                self.assertNotIn('<span class="nav-text">Trends</span>', html)

    def test_the_old_trends_address_still_works(self):
        self.assertEqual(TRENDS, '/analytics/mom/')
        self.assertEqual(self.client.get(TRENDS).status_code, 200)
