import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)


class LoggedEmailBackend(BaseEmailBackend):
    """
    A transparent email backend that logs every sent email to the database
    (EmailLog model) while delegating actual delivery to the correct real backend.

    In production (DEBUG=False) it uses Brevo (Anymail).
    In development (DEBUG=True) it falls back to the console backend.

    During tests, Django's test runner calls setup_test_environment() which
    overrides settings.EMAIL_BACKEND to 'locmem' BEFORE this backend is ever
    used, so this class is never instantiated during tests.  That means
    mail.outbox works correctly with no special-casing needed here.
    """

    def _get_real_backend(self):
        """Return an instance of the actual delivery backend."""
        if settings.DEBUG:
            from django.core.mail.backends.console import EmailBackend as ConsoleBackend
            return ConsoleBackend(fail_silently=self.fail_silently)
        else:
            from anymail.backends.brevo import EmailBackend as BrevoBackend
            return BrevoBackend(fail_silently=self.fail_silently)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        backend = self._get_real_backend()
        try:
            sent_count = backend.send_messages(email_messages)
        except Exception as e:
            logger.error(f"Error sending email messages via real backend: {e}")
            sent_count = 0

        # Log each message to the database regardless of send success
        for message in email_messages:
            try:
                from .models import EmailLog

                recipient = message.to[0] if message.to else ""
                user = User.objects.filter(email=recipient).first() if recipient else None

                html_body = ""
                if hasattr(message, 'alternatives'):
                    for content, mimetype in message.alternatives:
                        if mimetype == 'text/html':
                            html_body = content
                            break

                EmailLog.objects.create(
                    user=user,
                    to_email=", ".join(message.to) if message.to else "No Recipient",
                    subject=message.subject,
                    body=message.body,
                    html_body=html_body,
                    status='SENT' if sent_count > 0 else 'FAILED',
                    error_message=None if sent_count > 0 else "Backend reported 0 sent messages",
                )
            except Exception as e:
                logger.error(f"Failed to log email to database: {e}")

        return sent_count
