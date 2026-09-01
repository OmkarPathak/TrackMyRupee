import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils import timezone
from webpush import send_user_notification
from webpush.models import PushInformation

from blog.templatetags.blog_extras import markdown as render_markdown
from expenses.models import Announcement
from expenses.utils import markdown_to_plain_text

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Broadcasts queued feature announcements via WebPush and Email based on audience filter'

    def handle(self, *args, **kwargs):
        queued_announcements = Announcement.objects.filter(status='QUEUED')
        if not queued_announcements.exists():
            self.stdout.write("No queued announcements to send.")
            return

        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')
        icon_path = static('img/pwa-icon-512.png')
        absolute_icon_url = f"{site_url}{icon_path}"

        # Pre-fetch WebPush subscription presence to avoid N+1 queries
        subscribed_user_ids = set(PushInformation.objects.values_list('user_id', flat=True))

        for announcement in queued_announcements:
            self.stdout.write(f"Processing announcement: '{announcement.title}' (Audience: {announcement.audience})...")

            # Resolve target users based on audience
            users_qs = User.objects.filter(is_active=True).exclude(username='demo').select_related('profile')

            if announcement.audience == 'PAID':
                target_users = [u for u in users_qs if hasattr(u, 'profile') and u.profile.active_tier in ['PLUS', 'PRO']]
            elif announcement.audience == 'FREE':
                target_users = [u for u in users_qs if hasattr(u, 'profile') and u.profile.active_tier == 'FREE']
            else:  # ALL
                target_users = list(users_qs)

            body_html = render_markdown(announcement.body)
            body_plain = markdown_to_plain_text(announcement.body)
            hosted_image_url = f"{site_url}{announcement.image.url}" if announcement.image else None

            push_count = 0
            email_count = 0

            for user in target_users:
                # 1. Send WebPush if enabled and user is subscribed
                if announcement.send_push and user.id in subscribed_user_ids:
                    push_payload = {
                        "head": announcement.title,
                        "body": body_plain,
                        "icon": absolute_icon_url,
                        "url": announcement.cta_link or f"{site_url}/",
                    }
                    if hosted_image_url:
                        push_payload["image"] = hosted_image_url

                    try:
                        send_user_notification(user=user, payload=push_payload, ttl=3600)
                        push_count += 1
                    except Exception as e:
                        logger.warning(f"Failed push announcement send for {user.username}: {e}")
                        self.stdout.write(self.style.WARNING(f"Push send failed for {user.username}: {e}"))

                # 2. Send Email if enabled and user has valid email
                if announcement.send_email and user.email:
                    context = {
                        'user': user,
                        'announcement': announcement,
                        'subject': announcement.title,
                        'body_html': body_html,
                        'image_url': hosted_image_url,
                    }

                    html_message = render_to_string('email/announcement.html', context)
                    plain_text = f"{announcement.title}\n\nHi {user.username},\n\n{body_plain}\n\nVisit: {announcement.cta_link or site_url}"

                    try:
                        msg = EmailMultiAlternatives(
                            subject=announcement.title,
                            body=plain_text,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[user.email]
                        )
                        msg.attach_alternative(html_message, "text/html")
                        msg.send()
                        email_count += 1
                    except Exception as e:
                        logger.error(f"Failed email announcement send for {user.email}: {e}")
                        self.stdout.write(self.style.ERROR(f"Email send failed for {user.email}: {e}"))

            # Update announcement status
            announcement.status = 'SENT'
            announcement.sent_at = timezone.now()
            announcement.save(update_fields=['status', 'sent_at'])

            self.stdout.write(
                self.style.SUCCESS(
                    f"Finished announcement '{announcement.title}': Sent {push_count} pushes, {email_count} emails to {len(target_users)} users."
                )
            )
