from datetime import timedelta
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.adapters import CustomAccountAdapter
from expenses.models import EmailLog


class EmailBombPreventionTests(TestCase):
    """
    Comprehensive tests for DB-backed rate limiting, adapter email checks,
    and inactive user blocking to prevent email bombing.
    """

    def setUp(self):
        cache.clear()
        self.adapter = CustomAccountAdapter()

        self.active_user = User.objects.create_user(
            username='active_target',
            email='target@example.com',
            password='Password123!',
        )
        self.active_user.profile.has_seen_tutorial = True
        self.active_user.profile.save()

        self.inactive_user = User.objects.create_user(
            username='inactive_target',
            email='inactive@example.com',
            password='Password123!',
            is_active=False,
        )

        self.email_obj = EmailAddress.objects.create(
            user=self.active_user,
            email=self.active_user.email,
            primary=True,
            verified=False,
        )
        self.inactive_email_obj = EmailAddress.objects.create(
            user=self.inactive_user,
            email=self.inactive_user.email,
            primary=True,
            verified=False,
        )

        # Clear welcome email logs created during User post_save signals
        EmailLog.objects.all().delete()

    def test_inactive_user_email_is_blocked(self):
        """Inactive users must be blocked from sending emails."""
        is_blocked, msg = self.adapter.is_email_bomb_risk(self.inactive_user.email, user=self.inactive_user)
        self.assertTrue(is_blocked)
        self.assertIn('inactive', msg.lower())

    def test_db_backed_cooldown_blocks_email(self):
        """If a confirmation email was logged within 15 minutes, subsequent sends must be blocked."""
        # Create an EmailLog entry 5 minutes ago
        EmailLog.objects.create(
            user=self.active_user,
            to_email=self.active_user.email,
            subject="[trackmyrupee.com] Please Confirm Your Email Address",
            body="Confirmation text",
            status="SENT",
            sent_at=timezone.now() - timedelta(minutes=5),
        )

        is_blocked, msg = self.adapter.is_email_bomb_risk(self.active_user.email, user=self.active_user)
        self.assertTrue(is_blocked)
        self.assertIn("wait", msg.lower())

    def test_db_backed_daily_cap_blocks_email(self):
        """If 3 confirmation emails were logged in the last 24 hours, next send must be blocked."""
        now = timezone.now()
        for i in range(3):
            log = EmailLog.objects.create(
                user=self.active_user,
                to_email=self.active_user.email,
                subject="[trackmyrupee.com] Please Confirm Your Email Address",
                body="Confirmation text",
                status="SENT",
            )
            # Override auto_now_add timestamp
            EmailLog.objects.filter(id=log.id).update(sent_at=now - timedelta(hours=i * 2 + 1))

        is_blocked, msg = self.adapter.is_email_bomb_risk(self.active_user.email, user=self.active_user)
        self.assertTrue(is_blocked)
        self.assertIn("daily limit", msg.lower())

    def test_adapter_send_mail_intercepts_verification(self):
        """CustomAccountAdapter.send_mail drops verification emails when risk threshold is met."""
        # Log 3 previous emails today
        now = timezone.now()
        for i in range(3):
            log = EmailLog.objects.create(
                user=self.active_user,
                to_email=self.active_user.email,
                subject="[trackmyrupee.com] Please Confirm Your Email Address",
                body="Confirmation text",
                status="SENT",
            )
            EmailLog.objects.filter(id=log.id).update(sent_at=now - timedelta(hours=i * 2 + 1))

        context = {'user': self.active_user}
        with patch('allauth.account.adapter.DefaultAccountAdapter.send_mail') as mock_super_send:
            self.adapter.send_mail('account/email/email_confirmation', self.active_user.email, context)
            mock_super_send.assert_not_called()

    def test_resend_verification_endpoint_blocks_inactive_user(self):
        """The resend-verification endpoint must reject inactive users with redirect (302) or 403."""
        client = Client()
        client.force_login(self.inactive_user)

        response = client.post(reverse('resend-verification'))
        self.assertIn(response.status_code, [302, 403])
        if response.status_code == 200:
            self.assertFalse(response.json()['success'])

    def test_resend_verification_endpoint_respects_db_cooldown(self):
        """The resend-verification endpoint must reject requests if EmailLog has a recent send."""
        EmailLog.objects.create(
            user=self.active_user,
            to_email=self.active_user.email,
            subject="[trackmyrupee.com] Please Confirm Your Email Address",
            body="Confirmation text",
            status="SENT",
            sent_at=timezone.now() - timedelta(minutes=2),
        )

        client = Client()
        client.force_login(self.active_user)

        response = client.post(reverse('resend-verification'))
        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()['success'])
