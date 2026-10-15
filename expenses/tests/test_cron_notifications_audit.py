from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.models import Account, Holding
from expenses.views.notifications import _get_int_param, _get_threshold_param

User = get_user_model()


@override_settings(CRON_SECRET='s3cret', CRON_ALLOW_QUERY_SECRET=False)
class CronAuthTest(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('cron-ledger-retry-failures')

    @patch('expenses.views.notifications.call_command')
    def test_query_secret_rejected_when_not_allowed(self, _cmd):
        response = self.client.post(self.url + '?secret=s3cret')
        self.assertEqual(response.status_code, 403)

    @patch('expenses.views.notifications.call_command')
    def test_header_and_bearer_accepted(self, _cmd):
        self.assertEqual(self.client.post(self.url, HTTP_X_CRON_SECRET='s3cret').status_code, 200)
        cache.clear()
        self.assertEqual(self.client.post(self.url, HTTP_AUTHORIZATION='Bearer s3cret').status_code, 200)

    def test_non_bearer_authorization_rejected(self):
        response = self.client.post(self.url, HTTP_AUTHORIZATION='Basic s3cret')
        self.assertEqual(response.status_code, 403)

    @override_settings(CRON_SECRET='')
    def test_unset_secret_rejects_everything(self):
        self.assertEqual(self.client.post(self.url, HTTP_X_CRON_SECRET='').status_code, 403)

    @override_settings(CRON_ALLOW_QUERY_SECRET=True)
    @patch('expenses.views.notifications.call_command')
    def test_query_secret_accepted_when_opted_in(self, _cmd):
        self.assertEqual(self.client.post(self.url + '?secret=s3cret').status_code, 200)

    def test_nav_crons_reject_other_methods(self):
        for name in ('cron-sync-nav', 'cron-sync-funds'):
            response = self.client.put(reverse(name), HTTP_X_CRON_SECRET='s3cret')
            self.assertEqual(response.status_code, 405)


@override_settings(CRON_SECRET='s3cret')
class CronSyncModeTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_sync_error_does_not_leak_exception_text(self):
        with patch('expenses.nav_provider.NAVFetchService.sync_amfi_scheme_list',
                   side_effect=RuntimeError('db password=hunter2')):
            response = self.client.post(reverse('cron-sync-funds') + '?sync=1', HTTP_X_CRON_SECRET='s3cret')
        self.assertEqual(response.status_code, 500)
        self.assertNotIn('hunter2', response.content.decode())
        self.assertIsNone(cache.get('cron_lock_sync_amfi_schemes'))

    def test_sync_mode_respects_running_lock(self):
        cache.set('cron_lock_sync_nav_feed', 1, 60)
        response = self.client.post(reverse('cron-sync-nav') + '?sync=1', HTTP_X_CRON_SECRET='s3cret')
        self.assertEqual(response.status_code, 409)

    def test_sync_mode_releases_lock(self):
        with patch('expenses.nav_provider.NAVFetchService.sync_active_holdings_navs', return_value={'ok': 1}):
            response = self.client.post(reverse('cron-sync-nav') + '?sync=1', HTTP_X_CRON_SECRET='s3cret')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(cache.get('cron_lock_sync_nav_feed'))


class CronParamTest(TestCase):
    def req(self, **params):
        return self.client.get('/', params).wsgi_request

    def test_threshold_rejects_nan_inf_negative(self):
        for bad in ('nan', 'inf', '-1', 'abc'):
            self.assertEqual(_get_threshold_param(self.req(threshold=bad), '0.01'), '0.01')
        self.assertEqual(_get_threshold_param(self.req(threshold='0.5'), '0.01'), '0.5')

    def test_limit_is_clamped(self):
        self.assertEqual(_get_int_param(self.req(limit='-5'), 'limit', 200), 1)
        self.assertEqual(_get_int_param(self.req(limit='999999999'), 'limit', 200), 100000)
        self.assertEqual(_get_int_param(self.req(limit='x'), 'limit', 200), 200)


class StateChangingLinksArePostTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('u1', 'u1@example.com', 'pw12345!x')
        self.client.force_login(self.user)

    def test_restore_rejects_get(self):
        acc = Account.objects.create(user=self.user, name='Old', account_type='BANK', balance=0, is_active=False)
        response = self.client.get(reverse('account-restore', args=[acc.pk]))
        self.assertEqual(response.status_code, 405)
        acc.refresh_from_db()
        self.assertFalse(acc.is_active)
        self.client.post(reverse('account-restore', args=[acc.pk]))
        acc.refresh_from_db()
        self.assertTrue(acc.is_active)

    def test_refresh_nav_rejects_get(self):
        acc = Account.objects.create(user=self.user, name='MF', account_type='INVESTMENT', balance=0)
        holding = Holding.objects.create(account=acc, instrument_name='F', scheme_code='123', units=1)
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme') as fetch:
            response = self.client.get(reverse('refresh-holding-nav', args=[holding.pk]))
        self.assertEqual(response.status_code, 405)
        fetch.assert_not_called()

    def test_restore_template_uses_post_form(self):
        Account.objects.create(user=self.user, name='Old', account_type='BANK', balance=0, is_active=False)
        html = self.client.get(reverse('account-list') + '?status=inactive').content.decode()
        self.assertIn('method="post"', html)
        self.assertNotRegex(html, r'<a[^>]*href="[^"]*/restore/"')
