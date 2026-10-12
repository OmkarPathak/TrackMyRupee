
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from django.utils import timezone

from expenses.models import Expense, Income

logger = logging.getLogger(__name__)

# Drip email schedule: (day_threshold, template, subject)
DRIP_SCHEDULE = [
    (2, 'email/drip_tips.html', '3 Tips to Master Your Finances 💡'),
    (5, 'email/drip_discover.html', 'Never Miss a Bill Again 🔄'),
    (14, 'email/drip_upgrade.html', 'Unlock Your Full Financial Potential 🚀'),
    (30, 'email/drip_summary.html', '🎉 Your First Month in Review'),
]


class Command(BaseCommand):
    help = 'Sends lifecycle drip emails to free-tier users based on their signup date'

    def handle(self, *args, **kwargs):
        today = timezone.now()
        sent_count = 0
        skipped_count = 0

        # Only target free-tier users who have an email
        free_users = User.objects.filter(
            is_active=True,
            profile__tier='FREE',
            email__isnull=False,
        ).exclude(
            email=''
        ).exclude(
            username='demo'
        ).select_related('profile')

        # Only verified addresses: a mistyped sign-up address would only bounce
        try:
            from allauth.account.models import EmailAddress
            free_users = free_users.filter(
                id__in=EmailAddress.objects.filter(verified=True).values('user_id')
            )
        except ImportError:
            pass

        self.stdout.write(f"Found {free_users.count()} free-tier users to evaluate...")

        for user in free_users:
            profile = user.profile
            days_since_signup = (today - user.date_joined).days

            # Find the appropriate drip email for this user
            drip_to_send = None
            for day_threshold, template, subject in DRIP_SCHEDULE:
                # User has passed this threshold AND hasn't received this email yet
                if days_since_signup >= day_threshold and profile.last_drip_email_day < day_threshold:
                    drip_to_send = (day_threshold, template, subject)
                    # Don't break — we want the latest applicable one
                    # (in case the cron was down for a few days)

            if not drip_to_send:
                skipped_count += 1
                continue

            day_threshold, template, subject = drip_to_send

            try:
                # Build context
                context = {
                    'user': user,
                }

                # Upgrade pitch (day 14 and 30): numbers come from the plan table, never typed in the template
                if day_threshold in (14, 30):
                    from finance_tracker.plans import PLAN_DETAILS
                    context['monthly_price'] = PLAN_DETAILS['PLUS']['price_monthly']
                    context['plus'] = PLAN_DETAILS['PLUS']['limits']

                # For Day 30: include user stats
                if day_threshold == 30:
                    one_month_ago = today - timedelta(days=30)
                    context['expense_count'] = Expense.objects.filter(
                        user=user, created_at__gte=one_month_ago
                    ).count()
                    context['income_count'] = Income.objects.filter(
                        user=user, created_at__gte=one_month_ago
                    ).count()
                    context['category_count'] = Expense.objects.filter(
                        user=user, created_at__gte=one_month_ago
                    ).values('category').distinct().count()

                html_message = render_to_string(template, context)

                plain_text = " ".join(strip_tags(html_message).split())
                delivered = send_mail(
                    subject=subject,
                    message=plain_text,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[user.email],
                    html_message=html_message,
                )
                if not delivered:
                    raise RuntimeError("the email backend reported 0 messages sent")

                # Update tracking (only after a real send, so a failed one is retried tomorrow)
                profile.last_drip_email_day = day_threshold
                profile.save(update_fields=['last_drip_email_day'])

                sent_count += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Sent Day {day_threshold} email to {user.email}"
                    )
                )

            except Exception as e:
                logger.error(f"Failed to send drip email to {user.email}: {e}")
                self.stdout.write(
                    self.style.ERROR(f"Failed to send email to {user.email}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\nDrip email run complete: {sent_count} sent, {skipped_count} skipped."
            )
        )
