"""/demo/?next=<path> lands the visitor on that page, but only for a safe path on this site."""
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


@mock.patch('expenses.views.auth.call_command')  # the demo data is not needed; only the login and the redirect
class DemoNextTests(TestCase):
    def setUp(self):
        User.objects.create_user('demo', password='x')
        self.url = reverse('demo_login')

    def test_safe_path_is_followed(self, _):
        r = self.client.get(self.url, {'next': '/budgets/?month=1'})
        self.assertRedirects(r, '/budgets/?month=1', fetch_redirect_response=False)
        self.assertIn('_auth_user_id', self.client.session)

    def test_unsafe_values_fall_back_to_home(self, _):
        for bad in ['//evil.com', 'https://evil.com', '/\\evil.com', 'javascript:alert(1)', 'evil.com', '/\nx', '']:
            with self.subTest(bad=bad):
                self.client.logout()
                r = self.client.get(self.url, {'next': bad})
                self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_missing_next_goes_home(self, _):
        self.assertRedirects(self.client.get(self.url), reverse('home'), fetch_redirect_response=False)

    def test_already_logged_in_as_demo_still_works(self, _):
        self.client.get(self.url)
        r = self.client.get(self.url, {'next': '/budgets/'})
        self.assertRedirects(r, '/budgets/', fetch_redirect_response=False)

    def test_welcome_message_is_kept(self, _):
        r = self.client.get(self.url, {'next': '/budgets/'}, follow=False)
        msgs = [str(m) for m in r.wsgi_request._messages]
        self.assertTrue(any('Welcome to Demo Mode' in m for m in msgs))
