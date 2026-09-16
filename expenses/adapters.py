import logging
from datetime import timedelta

from allauth.account.adapter import DefaultAccountAdapter
from django.contrib.auth.models import User
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class CustomAccountAdapter(DefaultAccountAdapter):
    """
    Custom django-allauth AccountAdapter enforcing:
    1. Inactive user check: Inactive users (is_active=False) cannot trigger any emails.
    2. DB-backed cooldown: Minimum 15 minutes between verification emails to the same address.
    3. DB-backed daily cap: Maximum 3 verification emails per 24-hour window per recipient.
    """

    COOLDOWN_MINUTES = 15
    DAILY_LIMIT = 3

    def is_email_bomb_risk(self, recipient_email, user=None):
        """
        Check database-backed EmailLog history to enforce cooldown and daily limits.
        """
        from expenses.models import EmailLog

        if not recipient_email:
            return False, ""

        now = timezone.now()

        # Check if user account is deactivated
        if user and not user.is_active:
            logger.warning("Blocked email send to inactive user: %s (%s)", user.username, recipient_email)
            return True, _("This account is inactive. Please contact support.")

        # Also check if user with this email exists and is inactive
        if not user:
            existing_user = User.objects.filter(email__iexact=recipient_email, is_active=False).first()
            if existing_user:
                logger.warning("Blocked email send to inactive user email: %s", recipient_email)
                return True, _("This account is inactive. Please contact support.")

        fifteen_minutes_ago = now - timedelta(minutes=self.COOLDOWN_MINUTES)
        twenty_four_hours_ago = now - timedelta(hours=24)

        # 1. Cooldown check: Was any confirmation email sent in the last 15 minutes?
        recent_sends = EmailLog.objects.filter(
            to_email__iexact=recipient_email,
            sent_at__gte=fifteen_minutes_ago,
            status='SENT',
        ).exists()

        if recent_sends:
            logger.warning("Email rate limit cooldown triggered for recipient: %s", recipient_email)
            return True, _(f"Please wait {self.COOLDOWN_MINUTES} minutes before requesting another verification email.")

        # 2. Daily cap check: Have 3 or more confirmation emails been sent in the last 24 hours?
        daily_count = EmailLog.objects.filter(
            to_email__iexact=recipient_email,
            sent_at__gte=twenty_four_hours_ago,
            status='SENT',
        ).count()

        if daily_count >= self.DAILY_LIMIT:
            logger.warning("Email daily limit reached (%d) for recipient: %s", daily_count, recipient_email)
            return True, _("Daily limit for verification emails reached. Please try again tomorrow.")

        return False, ""

    def send_mail(self, template_prefix, email, context):
        """
        Intercept allauth email sends to enforce DB-backed rate limiting and active user checks.
        """
        user = context.get('user')

        # Identify confirmation / verification emails
        is_verification_email = any(
            keyword in template_prefix for keyword in ['email_confirmation', 'email_confirmation_signup', 'email_confirm']
        )

        if is_verification_email:
            is_blocked, error_msg = self.is_email_bomb_risk(email, user=user)
            if is_blocked:
                logger.warning("Blocked allauth email send (template=%s, to=%s): %s", template_prefix, email, error_msg)
                # Fail gracefully by not calling super().send_mail()
                return

        super().send_mail(template_prefix, email, context)
