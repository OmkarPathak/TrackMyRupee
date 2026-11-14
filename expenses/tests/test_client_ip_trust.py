"""The client address used for rate limits and audit logs cannot be chosen by the caller.

Behind a proxy the caller can start X-Forwarded-For with anything; only the entries our own proxies appended count.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from expenses.adapters import CustomAccountAdapter
from expenses.family import households
from expenses.views.misc import ContactView
from finance_tracker.client_ip import client_ip


CLIENT = '203.0.113.9'
NGINX = '172.18.0.1'            # the Docker network address nginx connects from
CLOUDFLARE = '172.67.10.20'     # an address inside Cloudflare's 172.64.0.0/13


def request_from(remote=NGINX, forwarded=None):
    extra = {'REMOTE_ADDR': remote}
    if forwarded is not None:
        extra['HTTP_X_FORWARDED_FOR'] = forwarded
    return RequestFactory().get('/', **extra)


class ClientIpHelperTests(SimpleTestCase):
    def test_nginx_only_the_visitor_is_the_last_entry_nginx_wrote(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=CLIENT)), CLIENT)

    def test_cloudflare_then_nginx_the_edge_address_is_skipped(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{CLIENT}, {CLOUDFLARE}')), CLIENT)

    def test_cloudflare_ipv6_edge_is_skipped_too(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{CLIENT}, 2606:4700::1234')), CLIENT)

    def test_a_made_up_front_of_the_list_never_changes_the_answer(self):
        for front in ('7.7.7.7', '7.7.7.7, 8.8.8.8', '10.0.0.1', '172.67.1.1', '1.1.1.1, 127.0.0.1'):
            self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{front}, {CLIENT}, {CLOUDFLARE}')), CLIENT, front)
            self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{front}, {CLIENT}')), CLIENT, front)

    def test_reaching_the_origin_directly_gives_the_callers_own_address_not_the_forged_one(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8', forwarded='9.9.9.9, 6.6.6.6')), '192.0.2.8')

    def test_a_forged_header_from_a_direct_caller_cannot_claim_a_cloudflare_address(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8', forwarded=CLOUDFLARE)), '192.0.2.8')

    def test_no_header_uses_the_connection_address(self):
        self.assertEqual(client_ip(request_from(remote='192.0.2.8')), '192.0.2.8')
        self.assertEqual(client_ip(request_from(remote='127.0.0.1')), '127.0.0.1')

    def test_text_that_is_not_an_address_is_never_returned(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{CLIENT}, not-an-ip')), NGINX)
        self.assertEqual(client_ip(request_from(remote='not-an-ip', forwarded=CLIENT)), None)

    def test_an_ipv6_client_is_normalised(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded='2001:DB8:0:0:0:0:0:1')), '2001:db8::1')

    def test_an_unknown_new_cdn_range_is_attributed_to_the_cdn_not_to_a_forged_address(self):
        """A proxy we do not know about is the first non-trusted hop: safe (shared bucket), never spoofable."""
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'9.9.9.9, {CLIENT}, 45.60.0.1')), '45.60.0.1')

    @override_settings(TRUSTED_PROXY_RANGES=('172.18.0.0/16', '45.60.0.0/16'))
    def test_extra_ranges_from_settings_are_trusted(self):
        self.assertEqual(client_ip(request_from(remote=NGINX, forwarded=f'{CLIENT}, 45.60.0.1')), CLIENT)

    def test_the_settings_include_the_defaults_and_accept_more_from_the_environment(self):
        from finance_tracker.client_ip import DEFAULT_TRUSTED_PROXY_RANGES
        self.assertTrue(set(DEFAULT_TRUSTED_PROXY_RANGES) <= set(settings.TRUSTED_PROXY_RANGES))

    def test_every_listed_range_is_a_valid_network(self):
        import ipaddress

        from finance_tracker.client_ip import DEFAULT_TRUSTED_PROXY_RANGES
        for cidr in DEFAULT_TRUSTED_PROXY_RANGES:
            ipaddress.ip_network(cidr)


class EveryCallerUsesTheTrustedAddressTests(SimpleTestCase):
    def setUp(self):
        self.request = request_from(remote=NGINX, forwarded='6.6.6.6, 203.0.113.9')

    def test_allauth_rate_limits_key_on_it(self):
        self.assertEqual(CustomAccountAdapter().get_client_ip(self.request), '203.0.113.9')

    def test_the_contact_form_limit_keys_on_it(self):
        self.assertEqual(ContactView()._get_client_ip(self.request), '203.0.113.9')

    def test_the_family_invitation_log_records_it(self):
        self.assertEqual(households._client_ip(self.request), '203.0.113.9')
        self.assertIsNone(households._client_ip(None))


class LoginThrottleCannotBeDodgedTests(TestCase):
    """The same checks as the probe that showed a changing X-Forwarded-For defeated the failed-login limit."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('throttle-me', 'throttle@example.com', 'a-long-and-right-password')

    def attempt(self, forwarded, login='throttle-me'):
        response = Client().post(reverse('account_login'), {'login': login, 'password': 'wrong-password'},
                                 HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR=NGINX)
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
        rule = settings.ACCOUNT_RATE_LIMITS['login_failed']
        self.assertIn('/ip', rule)
        self.assertIn('/key', rule)
