"""Every response carries a baseline Content-Security-Policy and a Permissions-Policy; nginx does not announce its version."""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from finance_tracker import security_headers
from finance_tracker.security_headers import SecurityHeadersMiddleware

ROOT = Path(settings.BASE_DIR)


def run(response=None):
    middleware = SecurityHeadersMiddleware(lambda request: response or HttpResponse('ok'))
    return middleware(RequestFactory().get('/'))


class MiddlewareTests(SimpleTestCase):
    def test_the_baseline_policy_has_the_restrictions_that_cost_nothing(self):
        csp = run().headers['Content-Security-Policy']
        for directive in ("object-src 'none'", "base-uri 'self'", "frame-ancestors 'self'"):
            self.assertIn(directive, csp)

    def test_it_does_not_block_scripts_styles_images_or_form_posts(self):
        """A full policy would need the pages' inline scripts moved out first; the baseline must not break any page."""
        csp = run().headers['Content-Security-Policy']
        for directive in ('default-src', 'script-src', 'style-src', 'img-src', 'connect-src', 'form-action'):
            self.assertNotIn(directive, csp)

    def test_unused_browser_features_are_switched_off_and_the_microphone_stays_on_for_voice_input(self):
        policy = run().headers['Permissions-Policy']
        for feature in ('camera=()', 'geolocation=()', 'usb=()'):
            self.assertIn(feature, policy)
        self.assertIn('microphone=(self)', policy)

    def test_a_view_that_sets_its_own_policy_keeps_it(self):
        response = HttpResponse('ok')
        response.headers['Content-Security-Policy'] = "default-src 'none'"
        self.assertEqual(run(response).headers['Content-Security-Policy'], "default-src 'none'")

    @override_settings(CONTENT_SECURITY_POLICY="default-src 'self'")
    def test_settings_can_replace_the_policy(self):
        self.assertEqual(run().headers['Content-Security-Policy'], "default-src 'self'")

    @override_settings(CONTENT_SECURITY_POLICY='', PERMISSIONS_POLICY='')
    def test_an_empty_setting_sends_none(self):
        headers = run().headers
        self.assertNotIn('Content-Security-Policy', headers)
        self.assertNotIn('Permissions-Policy', headers)

    def test_it_is_installed_just_after_django_security_middleware(self):
        stack = list(settings.MIDDLEWARE)
        self.assertEqual(stack.index('finance_tracker.security_headers.SecurityHeadersMiddleware'),
                         stack.index('django.middleware.security.SecurityMiddleware') + 1)

    def test_the_defaults_are_the_documented_ones(self):
        self.assertEqual(security_headers.DEFAULT_CSP, "object-src 'none'; base-uri 'self'; frame-ancestors 'self'")


class RealResponsesTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_public_page_carries_both_headers(self):
        response = self.client.get(reverse('landing'))
        self.assertIn("object-src 'none'", response.headers['Content-Security-Policy'])
        self.assertIn('Permissions-Policy', response.headers)

    def test_a_signed_in_page_and_a_json_endpoint_carry_them_too(self):
        user = User.objects.create_user('headers-user', password='pw')
        profile = user.profile
        profile.has_seen_tutorial, profile.consent_granted = True, True
        profile.save()
        self.client.force_login(user)
        for name in ('settings-home', 'health_check'):
            response = self.client.get(reverse(name))
            self.assertIn('Content-Security-Policy', response.headers, name)
            self.assertIn('Permissions-Policy', response.headers, name)

    def test_an_error_page_carries_them_too(self):
        response = self.client.get('/no-such-page/')
        self.assertEqual(response.status_code, 404)
        self.assertIn('Content-Security-Policy', response.headers)


class NginxTests(SimpleTestCase):
    def test_nginx_does_not_announce_its_version(self):
        config = (ROOT / 'nginx.conf').read_text(encoding='utf-8')
        self.assertEqual(config.count('server_tokens off;'), 2)       # the redirect block and the https block


class BaselineDoesNotBreakTheTemplatesTests(SimpleTestCase):
    def test_no_template_uses_object_embed_or_base_tags_that_the_baseline_would_block(self):
        offenders = []
        for path in (ROOT / 'templates').rglob('*.html'):
            text = path.read_text(encoding='utf-8', errors='ignore')
            if any(tag in text for tag in ('<object', '<embed', '<base ')):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
