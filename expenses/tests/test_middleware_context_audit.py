import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.cache_utils import dashboard_invalidation_suppressed, suppress_dashboard_cache_invalidation
from expenses.models import Account

User = get_user_model()


class SuppressInvalidationTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('a1', 'a1@example.com', 'pw12345!x')

    def test_signal_receivers_stay_connected(self):
        from django.db.models.signals import post_save
        before = len(post_save.receivers)
        with suppress_dashboard_cache_invalidation():
            self.assertEqual(len(post_save.receivers), before)
        self.assertFalse(dashboard_invalidation_suppressed())

    def test_other_threads_still_invalidate(self):
        key = f'home_default_data_{self.user.id}'
        cache.set(key, {'x': 1}, 60)
        seen = {}

        def other_thread():
            seen['suppressed'] = dashboard_invalidation_suppressed()

        with suppress_dashboard_cache_invalidation(invalidate_on_exit=False):
            self.assertTrue(dashboard_invalidation_suppressed())
            t = threading.Thread(target=other_thread)
            t.start()
            t.join()
        self.assertFalse(seen['suppressed'])

    def test_suppressed_block_defers_then_invalidates_once(self):
        key = f'home_default_data_{self.user.id}'
        cache.set(key, {'x': 1}, 60)
        with suppress_dashboard_cache_invalidation(user_id=self.user.id):
            Account.objects.create(user=self.user, name='A', account_type='BANK', balance=0)
            self.assertIsNotNone(cache.get(key))
        self.assertIsNone(cache.get(key))

    def test_nested_exit_still_invalidates(self):
        key = f'home_default_data_{self.user.id}'
        cache.set(key, {'x': 1}, 60)
        with suppress_dashboard_cache_invalidation(invalidate_on_exit=False):
            with suppress_dashboard_cache_invalidation(user_id=self.user.id):
                pass
        self.assertIsNone(cache.get(key))
        self.assertFalse(dashboard_invalidation_suppressed())


class TimezoneMiddlewareTest(TestCase):
    def test_invalid_cookie_does_not_keep_previous_zone(self):
        import zoneinfo
        timezone.activate(zoneinfo.ZoneInfo('Asia/Tokyo'))
        self.client.cookies['django_timezone'] = 'Not/AZone'
        self.client.get(reverse('landing'))
        self.assertEqual(timezone.get_current_timezone_name(), timezone.get_default_timezone_name())
        timezone.deactivate()


class DemoMiddlewareTest(TestCase):
    def setUp(self):
        self.demo = User.objects.create_user('demo', 'demo@example.com', 'pw12345!x')
        self.client.force_login(self.demo)

    def test_post_is_blocked_and_referer_is_sanitised(self):
        response = self.client.post(reverse('account-list'), HTTP_REFERER='https://evil.example/x')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/')

    def test_same_site_referer_is_kept(self):
        response = self.client.post(reverse('account-list'), HTTP_REFERER='http://testserver/accounts/')
        self.assertEqual(response['Location'], 'http://testserver/accounts/')

    def test_username_demo_cannot_be_registered(self):
        from allauth.account.adapter import get_adapter
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            get_adapter().clean_username('DEMO')


class ConsentExemptPathsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('c1', 'c1@example.com', 'pw12345!x')
        profile = self.user.profile
        profile.consent_granted = False
        profile.save()
        self.client.force_login(self.user)

    def test_pwa_and_public_files_not_redirected(self):
        for path in ('/manifest.json', '/service-worker.js', '/offline/', '/robots.txt', '/llms.txt'):
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 302, path)

    def test_app_page_still_redirected(self):
        response = self.client.get(reverse('account-list'))
        self.assertRedirects(response, reverse('dpdp_consent'), fetch_redirect_response=False)


class MarkdownVaryTest(TestCase):
    def test_html_responses_vary_on_accept(self):
        response = self.client.get(reverse('landing'))
        self.assertIn('Accept', response.get('Vary', ''))

    def test_markdown_negotiation_works(self):
        response = self.client.get(reverse('landing'), HTTP_ACCEPT='text/markdown')
        self.assertEqual(response['Content-Type'], 'text/markdown')
        self.assertIn('Accept', response['Vary'])


class MaturedDepositProcessingTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('m1', 'm1@example.com', 'pw12345!x')
        self.client.force_login(self.user)

    def test_failure_is_logged_and_not_retried_every_request(self):
        with patch('expenses.account_valuation.process_matured_deposit_incomes', side_effect=RuntimeError('boom')) as proc:
            with self.assertLogs('expenses.context_processors', level='ERROR'):
                self.client.get(reverse('account-list'))
            self.client.get(reverse('account-list'))
        self.assertEqual(proc.call_count, 1)

    def test_runs_once_per_cooldown(self):
        with patch('expenses.account_valuation.process_matured_deposit_incomes') as proc:
            self.client.get(reverse('account-list'))
            self.client.get(reverse('account-list'))
        self.assertEqual(proc.call_count, 1)
