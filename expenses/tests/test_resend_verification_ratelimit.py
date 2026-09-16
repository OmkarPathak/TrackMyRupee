"""
Tests for email-bombing protection on the resend_verification_email endpoint.

Run with:
    python manage.py test expenses.tests.test_resend_verification_ratelimit -v 2
"""
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse


class ResendVerificationRateLimitTests(TestCase):
    """Verify that the resend-verification endpoint is properly rate-limited."""

    def setUp(self):
        self.url = reverse('resend-verification')

        # Create a user whose email is NOT yet verified
        self.user = User.objects.create_user(
            username='testuser',
            email='testuser@example.com',
            password='secret123',
        )
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()

        # Wire up the allauth EmailAddress record (unverified)
        self.email_obj = EmailAddress.objects.create(
            user=self.user,
            email=self.user.email,
            primary=True,
            verified=False,
        )

        self.client = Client()
        self.client.login(username='testuser', password='secret123')

        # Always start each test with a clean cache
        cache.clear()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_first_send_succeeds(self, mock_send):
        """First POST should send the email and return success."""
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        mock_send.assert_called_once()

    # ------------------------------------------------------------------
    # Cooldown enforcement
    # ------------------------------------------------------------------

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_second_send_within_cooldown_returns_429(self, mock_send):
        """A second POST within the 300-second cooldown must be rejected."""
        # First request — sets the cooldown key in cache
        self.client.post(self.url)
        mock_send.reset_mock()

        # Immediate second request — should be blocked
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('wait', data['error'].lower())
        mock_send.assert_not_called()

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_send_allowed_after_cooldown_expires(self, mock_send):
        """After the cooldown cache key expires a new send should succeed."""
        # Simulate that the cooldown key has already expired
        cooldown_key = f'resend_verify_cooldown_{self.user.pk}'
        cache.delete(cooldown_key)

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        mock_send.assert_called_once()

    # ------------------------------------------------------------------
    # Daily cap enforcement
    # ------------------------------------------------------------------

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_daily_cap_blocks_after_three_sends(self, mock_send):
        """After 3 successful sends in a day the next one must be blocked."""
        daily_key = f'resend_verify_daily_{self.user.pk}'
        cooldown_key = f'resend_verify_cooldown_{self.user.pk}'

        # Simulate 3 sends already recorded today
        cache.set(daily_key, 3, timeout=86400)
        # Cooldown expired (they waited 15 min between each send)
        cache.delete(cooldown_key)

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('daily limit', data['error'].lower())
        mock_send.assert_not_called()

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_daily_counter_increments_on_success(self, mock_send):
        """Each successful send increments the 24-hour counter by 1."""
        daily_key = f'resend_verify_daily_{self.user.pk}'
        cache.set(daily_key, 2, timeout=86400)

        self.client.post(self.url)

        self.assertEqual(cache.get(daily_key), 3)

    # ------------------------------------------------------------------
    # Already-verified / missing email
    # ------------------------------------------------------------------

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_already_verified_returns_error(self, mock_send):
        """If the email is already verified no send should occur."""
        self.email_obj.verified = True
        self.email_obj.save()

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error'], 'Already verified')
        mock_send.assert_not_called()

    @patch('allauth.account.internal.flows.email_verification.send_verification_email_for_user')
    def test_missing_email_address_returns_error(self, mock_send):
        """If no EmailAddress record exists the view returns an error."""
        self.email_obj.delete()

        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertEqual(data['error'], 'Email not found')
        mock_send.assert_not_called()

    # ------------------------------------------------------------------
    # Wrong HTTP method
    # ------------------------------------------------------------------

    def test_get_request_returns_400(self):
        """GET is not allowed; should return 400."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    # ------------------------------------------------------------------
    # Unauthenticated access
    # ------------------------------------------------------------------

    def test_unauthenticated_is_redirected(self):
        """Unauthenticated users should be redirected to login."""
        anon_client = Client()
        response = anon_client.post(self.url)
        # Django redirects to login page for unauthenticated requests
        self.assertIn(response.status_code, [302, 403])
