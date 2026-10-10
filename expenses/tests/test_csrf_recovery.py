"""An expired login form recovers gracefully, and the reason is logged."""

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(CSRF_FAILURE_VIEW='finance_tracker.csrf.csrf_failure')
class TestCsrfRecovery(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        User.objects.create_user(username='csrf-user', password='pass')

    def test_expired_login_form_goes_back_to_a_fresh_page_with_a_message(self):
        with self.assertLogs('finance_tracker.csrf', level='WARNING') as logs:
            response = self.client.post(reverse('account_login'), {'login': 'csrf-user', 'password': 'pass'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('account_login'))
        self.assertIn('reason=', logs.output[0])
        self.assertNotIn('pass', logs.output[0].replace('password', ''))   # no form data in the log
        page = self.client.get(response.url)
        self.assertContains(page, 'expired. Please try again.')

    def test_valid_token_still_logs_in(self):
        page = self.client.get(reverse('account_login'))
        token = page.cookies['csrftoken'].value
        response = self.client.post(reverse('account_login'), {'login': 'csrf-user', 'password': 'pass',
                                                              'csrfmiddlewaretoken': token})
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse('account_login'))

    def test_other_pages_keep_the_standard_403(self):
        self.client.force_login(User.objects.get(username='csrf-user'))
        response = self.client.post(reverse('expense-composer-save'), data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_login_page_refreshes_itself_when_restored_or_stale(self):
        page = self.client.get(reverse('account_login'))
        self.assertContains(page, "e.persisted")
        self.assertContains(page, 'visibilitychange')
