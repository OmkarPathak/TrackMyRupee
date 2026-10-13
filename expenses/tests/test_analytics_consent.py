from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from expenses import posthog_utils
from expenses.middleware import AnalyticsConsentMiddleware
from expenses.models import UserProfile

ANALYTICS = dict(GOOGLE_ANALYTICS_ID='G-TEST123456', POSTHOG_API_KEY='phc_testkey', POSTHOG_HOST='https://us.i.posthog.com')


class ServerSideAnalyticsConsentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='privacyuser', email='private@example.com', password='pw12345!')
        UserProfile.objects.get_or_create(user=self.user)
        self.client_mock = MagicMock()

    def _run(self, consent):
        token = posthog_utils.set_analytics_consent(consent)
        try:
            with patch.object(posthog_utils, '_get_client', return_value=self.client_mock):
                posthog_utils.ph_capture(self.user, 'expense_updated', {'currency': 'INR', 'amount': '123.45', 'description': 'rent'})
                posthog_utils.ph_identify(self.user, {'signup_method': 'email', 'email': 'private@example.com'})
        finally:
            posthog_utils.reset_analytics_consent(token)

    def test_nothing_sent_without_consent(self):
        self._run(False)
        self.client_mock.capture.assert_not_called()
        self.client_mock.identify.assert_not_called()

    def test_default_is_no_consent(self):
        self.assertFalse(posthog_utils.analytics_consent_given())

    def test_events_are_pseudonymous_and_scrubbed_with_consent(self):
        self._run(True)
        distinct_id, = self.client_mock.capture.call_args.args
        kwargs = self.client_mock.capture.call_args.kwargs
        self.assertNotEqual(distinct_id, str(self.user.id))
        self.assertNotIn('private', distinct_id)
        self.assertEqual(kwargs['properties'], {'currency': 'INR'})

        identify_args = self.client_mock.identify.call_args
        props = identify_args.kwargs['properties']
        self.assertNotIn('email', props)
        self.assertNotIn('username', props)
        self.assertNotIn('date_joined', props)
        self.assertEqual(set(props), {'tier', 'signup_method'})
        self.assertEqual(identify_args.args[0], distinct_id)

    def test_middleware_reads_consent_cookie(self):
        seen = {}

        def view(request):
            seen['consent'] = posthog_utils.analytics_consent_given()
            return MagicMock()

        rf = RequestFactory()
        for cookie, expected in (('accepted', True), ('declined', False), (None, False)):
            request = rf.get('/')
            if cookie:
                request.COOKIES['tmr_consent'] = cookie
            AnalyticsConsentMiddleware(view)(request)
            self.assertEqual(seen['consent'], expected, cookie)
        self.assertFalse(posthog_utils.analytics_consent_given())  # reset after the request


@override_settings(**ANALYTICS)
class AnalyticsMarkupTests(TestCase):
    def setUp(self):
        cache.clear()  # the policy pages are cached with cache_page

    def test_no_unconditional_analytics_tags_in_page(self):
        html = self.client.get(reverse('privacy-policy')).content.decode()
        # The loader only injects scripts from JS after consent; there must be no static tag.
        self.assertNotIn('<script async src="https://www.googletagmanager.com', html)
        self.assertNotIn('src="https://us-assets.i.posthog.com', html)
        self.assertIn("if (choice !== 'accepted') { return; }", html)
        self.assertIn('autocapture: false', html)
        self.assertIn('disable_session_recording: true', html)

    def test_logged_in_page_does_not_embed_email_or_username(self):
        user = User.objects.create_user(username='visiblename', email='visible@example.com', password='pw12345!')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.consent_granted = True
        profile.save()
        client = Client()
        client.login(username='visiblename', password='pw12345!')
        html = client.get(reverse('privacy-policy')).content.decode()
        loader = html[html.index('var GA_ID'):html.index('var afterLoad')]
        self.assertNotIn('visible@example.com', loader)
        self.assertNotIn('visiblename', loader)
        self.assertIn(posthog_utils.posthog_distinct_id(user), loader)

    def test_cookie_banner_and_preferences_link_present(self):
        html = self.client.get(reverse('privacy-policy')).content.decode()
        self.assertIn('btn-decline-cookies', html)
        self.assertIn('tmrOpenCookiePreferences', html)
        self.assertIn('id="cookie-preferences"', html)
