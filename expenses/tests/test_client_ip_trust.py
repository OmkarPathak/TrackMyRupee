"""The client address used for rate limits and audit logs cannot be chosen by the caller.

Behind a proxy the caller can start X-Forwarded-For with anything; only the entries our own proxies appended count.
"""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from expenses.adapters import CustomAccountAdapter
from expenses.family import households
from expenses.views.misc import ContactView
from finance_tracker.client_ip import client_ip


def request_from(remote='10.0.0.5', forwarded=None):
    extra = {'REMOTE_ADDR': remote}
    if forwarded is not None:
        extra['HTTP_X_FORWARDED_FOR'] = forwarded
    return RequestFactory().get('/', **extra)


class ClientIpHelperTests(SimpleTestCase):
    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_one_proxy_the_last_entry_is_the_client_and_what_came_before_is_ignored(self):
        self.assertEqual(client_ip(request_from(forwarded='1.1.1.1, 2.2.2.2, 203.0.113.9')), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_a_made_up_first_entry_does_not_change_the_answer(self):
        real = '203.0.113.9'
        answers = {client_ip(request_from(forwarded=f'7.7.7.{n}, {real}')) for n in range(20)}
        self.assertEqual(answers, {real})

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_two_proxies_the_second_from_last_is_the_client(self):
        self.assertEqual(client_ip(request_from(forwarded='9.9.9.9, 203.0.113.9, 198.51.100.4')), '203.0.113.9')

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_no_proxy_only_the_connection_address_counts(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8', forwarded='203.0.113.9')), '192.0.2.8')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_no_header_falls_back_to_the_connection_address(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8')), '192.0.2.8')

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_fewer_entries_than_trusted_proxies_means_the_header_was_not_ours(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8', forwarded='203.0.113.9')), '192.0.2.8')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_text_that_is_not_an_address_is_never_returned(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8', forwarded='1.1.1.1, not-an-ip')), '192.0.2.8')

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_an_ipv6_client_is_normalised(self):
        self.assertEqual(client_ip(request_from(forwarded='2001:DB8:0:0:0:0:0:1')), '2001:db8::1')


@override_settings(TRUSTED_PROXY_COUNT=1)
class EveryCallerUsesTheTrustedAddressTests(SimpleTestCase):
    def setUp(self):
        self.request = request_from(forwarded='6.6.6.6, 203.0.113.9')

    def test_allauth_rate_limits_key_on_it(self):
        self.assertEqual(CustomAccountAdapter().get_client_ip(self.request), '203.0.113.9')

    def test_the_contact_form_limit_keys_on_it(self):
        self.assertEqual(ContactView()._get_client_ip(self.request), '203.0.113.9')

    def test_the_family_invitation_log_records_it(self):
        self.assertEqual(households._client_ip(self.request), '203.0.113.9')
        self.assertIsNone(households._client_ip(None))


@override_settings(TRUSTED_PROXY_COUNT=1)
class LoginThrottleCannotBeDodgedTests(TestCase):
    """The same checks as the probe that showed a changing X-Forwarded-For defeated the failed-login limit."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('throttle-me', 'throttle@example.com', 'a-long-and-right-password')

    def attempt(self, forwarded, login='throttle-me'):
        response = Client().post(reverse('account_login'), {'login': login, 'password': 'wrong-password'},
                                 HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR='10.0.0.5')
        return 'too many' in response.content.decode().lower()

    def test_changing_the_first_entry_each_time_still_runs_into_the_address_limit(self):
        blocked = [self.attempt(f'7.7.7.{n}, 203.0.113.9') for n in range(1, 8)]
        self.assertEqual(blocked, [False] * 5 + [True] * 2)

    def test_an_account_is_limited_even_when_every_guess_comes_from_a_new_address(self):
        blocked = [self.attempt(f'203.0.113.{n}') for n in range(1, 8)]
        self.assertEqual(blocked, [False] * 5 + [True] * 2)

    def test_other_accounts_are_not_locked_by_guesses_at_this_one(self):
        User.objects.create_user('someone-else', 'else@example.com', 'another-long-password')
        for n in range(1, 7):
            self.attempt(f'203.0.113.{n}')
        self.assertFalse(self.attempt('198.51.100.77', login='someone-else'))

    def test_the_rule_names_both_an_address_and_an_account(self):
        from django.conf import settings
        rule = settings.ACCOUNT_RATE_LIMITS['login_failed']
        self.assertIn('/ip', rule)
        self.assertIn('/key', rule)
