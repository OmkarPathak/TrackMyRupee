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
                title = html.index('fw-bold mb-1">Analytics<')
                self.assertLess(title, html.index('analytics-tabs mb-3'))

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
