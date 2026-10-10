"""Guards for the PWA launch-time work: the service worker, and a dashboard that is not weighed
down by content nobody has asked for yet."""

import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import UserProfile


class TestServiceWorker(TestCase):
    def setUp(self):
        self.js = self.client.get(reverse('service-worker')).content.decode()

    def test_navigation_preload_is_enabled_and_used(self):
        self.assertIn('navigationPreload.enable()', self.js)
        self.assertIn('event.preloadResponse', self.js)

    def test_the_signed_in_dashboard_is_never_precached(self):
        precache = re.search(r'const ASSETS_TO_CACHE = \[(.*?)\];', self.js, re.S).group(1)
        entries = re.findall(r"'([^']*)'", precache)
        self.assertNotIn('/', entries)
        self.assertIn('OFFLINE_URL', precache)

    def test_precached_assets_are_the_urls_the_pages_request(self):
        """No hand-written '/static/x.css' entries that can never match a hashed file name."""
        precache = re.search(r'const ASSETS_TO_CACHE = \[(.*?)\];', self.js, re.S).group(1)
        for entry in re.findall(r"'([^']*)'", precache):
            self.assertTrue(entry.startswith('/static/'), entry)

    def test_one_missing_asset_does_not_abort_the_install(self):
        self.assertNotIn('cache.addAll', self.js)


class TestDashboardWeight(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='pwa-perf', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.has_seen_tutorial = True
        profile.save()
        self.client.force_login(self.user)

    def give_data(self):
        from datetime import date
        from expenses.models import Account, Expense
        cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=100,
                                      currency='₹')
        Expense.objects.create(user=self.user, date=date.today(), amount=5, category='Food', currency='₹',
                               account=cash, description='x')

    def test_how_it_works_guide_is_fetched_on_demand(self):
        self.give_data()
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('id="savingsMethodContent"', html)
        self.assertNotIn('id="guideTab"', html)           # the ~70 KB guide itself is not inlined
        partial = self.client.get(reverse('savings-guide-partial'))
        self.assertEqual(partial.status_code, 200)
        self.assertIn('guideTab', partial.content.decode())

    def test_guide_partial_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('savings-guide-partial')).status_code, 302)

    def test_blocking_script_is_deferred(self):
        self.give_data()
        html = self.client.get(reverse('home')).content.decode()
        tag = re.search(r'<script[^>]*insights_carousel[^>]*>', html).group(0)
        self.assertIn('defer', tag)


class TestLaunchPath(TestCase):
    def test_manifest_starts_on_the_dashboard_without_a_redirect_hop(self):
        import json
        manifest = json.loads(self.client.get(reverse('manifest')).content.decode())
        self.assertEqual(manifest['start_url'], reverse('home'))

    def test_analytics_wait_for_the_page_to_load(self):
        from django.test import override_settings
        from django.template.loader import render_to_string
        with override_settings():
            html = render_to_string('base.html', {
                'GOOGLE_ANALYTICS_ID': 'G-TEST', 'POSTHOG_API_KEY': 'phc_test', 'POSTHOG_HOST': 'https://us.i.posthog.com',
            })
        self.assertIn('window.tmrAfterLoad', html)
        # no async third-party script tag in the document head, and no preconnect to analytics hosts
        self.assertNotRegex(html, r'<script[^>]*async[^>]*googletagmanager')
        self.assertNotIn('rel="preconnect"', html)
        # the posthog init (which downloads array.js) and the gtag injection both sit inside tmrAfterLoad
        init_at = html.index("posthog.init('phc_test'")
        self.assertLess(html.rindex('tmrAfterLoad(function', 0, init_at), init_at)
        gtag_at = html.index('googletagmanager.com/gtag/js')
        self.assertLess(html.rindex('tmrAfterLoad(function', 0, gtag_at), gtag_at)
