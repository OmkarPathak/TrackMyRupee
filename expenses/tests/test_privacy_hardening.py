"""Privacy hardening from the PR #100 audit: Sentry locals, analytics URLs, the provider list."""
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

BASE = Path(settings.BASE_DIR)


class SentryTests(SimpleTestCase):
    def test_local_variables_are_not_attached_to_error_reports(self):
        source = (BASE / 'finance_tracker/settings.py').read_text()
        init = source[source.index('sentry_sdk.init('):]
        init = init[:init.index('\n    )')]
        self.assertIn('include_local_variables=False', init)
        self.assertNotIn('send_default_pii=True', init)


class SettingsCleanupTests(SimpleTestCase):
    def test_no_leftover_hosting_provider_in_the_default_trusted_origins(self):
        source = (BASE / 'finance_tracker/settings.py').read_text()
        self.assertNotIn('onrender.com', source)


@override_settings(GOOGLE_ANALYTICS_ID='G-TEST123', POSTHOG_API_KEY='phc_test')
class AnalyticsUrlTests(TestCase):
    def loader(self):
        html = self.client.get(reverse('privacy-policy')).content.decode()
        self.assertIn('var PH_KEY', html)
        return html

    def test_htmx_pageviews_send_the_path_only(self):
        html = self.loader()
        self.assertNotIn("'$current_url': window.location.href", html)
        self.assertIn("'$current_url': pageUrl()", html)

    def test_posthog_scrubs_every_address_property_and_nested_first_visit_properties(self):
        html = self.loader()
        self.assertIn('sanitize_properties', html)
        self.assertIn('$set_once', html)

    def test_google_analytics_gets_the_address_without_query_or_fragment(self):
        html = self.loader()
        self.assertIn('page_location: pageUrl()', html)
        self.assertIn('page_referrer: cleanUrl(document.referrer)', html)

    def test_cleaning_function_drops_query_and_fragment(self):
        if not shutil.which('node'):
            self.skipTest('node is not installed')
        html = self.loader()
        function = re.search(r'function cleanUrl\(url\) \{.*?\n    \}', html, re.S).group(0)
        code = function + (
            "\nconsole.log(JSON.stringify([cleanUrl('https://a.com/expenses/?search=salary&x=1#top'),"
            " cleanUrl('https://a.com/'), cleanUrl(null)]));"
        )
        out = subprocess.run(['node', '-e', code], capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), '["https://a.com/expenses/","https://a.com/",null]')


class ProviderListTests(TestCase):
    def test_every_third_party_the_app_contacts_is_in_the_policy(self):
        html = self.client.get(reverse('privacy-policy')).content.decode()
        for provider in ('Supabase', 'DigitalOcean', 'Razorpay', 'Brevo', 'Google Analytics', 'PostHog', 'Sentry',
                         'jsDelivr', 'Cloudflare cdnjs', 'Google Fonts', 'Google Charts',
                         'Sign in with Google', 'Browser push services'):
            self.assertIn(provider, html, provider)

    def test_policy_makes_no_legal_retention_claim_about_payments(self):
        html = self.client.get(reverse('privacy-policy')).content.decode()
        self.assertNotIn('5 years', html)
        self.assertNotIn('tax law', html)
        self.assertIn('deleted with your account', html)


class LoanFormHeaderTests(TestCase):
    """Add Loan form: no Back button in the header, and the Tutorial pill centres its text."""

    def setUp(self):
        from django.contrib.auth.models import User
        user = User.objects.create_user('loaner', 'loaner@example.com', 'pw')
        user.profile.tier = 'PRO'
        user.profile.consent_granted = True
        user.profile.has_seen_tutorial = True
        user.profile.save()
        self.client.force_login(user)

    def test_no_back_button_and_tutorial_text_is_centred(self):
        html = self.client.get(reverse('loan-create')).content.decode()
        self.assertIn('Add New Loan', html)
        self.assertNotIn('bi-arrow-left', html.split('Need guidance')[0].split('Add New Loan')[-1])
        link = re.search(r'<a href="https://docs\.trackmyrupee\.com/08-loans/"[^>]*>', html).group(0)
        for css in ('d-inline-flex', 'align-items-center'):
            self.assertIn(css, link)
