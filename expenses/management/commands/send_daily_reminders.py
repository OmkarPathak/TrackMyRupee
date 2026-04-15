
import json
from django.conf import settings
from django.templatetags.static import static
from django.core.management.base import BaseCommand
from django.utils import timezone
from webpush import send_user_notification
from expenses.models import UserProfile, Expense, Notification
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sends daily PWA reminders to users who have not added any expenses today'

    def handle(self, *args, **kwargs):
        today = timezone.now().date()
        self.stdout.write(f"Starting daily reminder run for {today}...")
        
        # Get all profiles with daily_reminder enabled
        profiles = UserProfile.objects.filter(daily_reminder=True).select_related('user')
        
        sent_count = 0
        skipped_count = 0
        
        # Base URL for media assets
        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')
        icon_path = static('img/pwa-icon-512.png')
        absolute_icon_url = f"{site_url}{icon_path}"
        
        for profile in profiles:
            user = profile.user
            
            # Check if user has added any expense today
            has_expense = Expense.objects.filter(user=user, date=today).exists()
            
            if not has_expense:
                title = "Expense Reminder 💸"
                message = "Don't forget to add your expenses for today to keep your tracker up to date!"
                
                # Deduplication for UI: Only show ONE unread reminder in the app's notification center
                ui_slug = "daily-expense-reminder"
                if not Notification.objects.filter(user=user, slug=ui_slug, is_read=False).exists():
                    Notification.objects.create(
                        user=user,
                        title=title,
                        message=message,
                        notification_type='SYSTEM',
                        slug=ui_slug,
                        link='/expenses/add/'
                    )
                
                # 2. Send Push Notification
                # Using absolute URLs and JSON stringification for max compatibility in production
                payload = json.dumps({
                    "head": title,
                    "body": message,
                    "icon": absolute_icon_url,
                    "url": f"{site_url}/expenses/add/"
                })
                
                try:
                    # django-webpush sends to ALL registered devices for this user
                    send_user_notification(user=user, payload=payload, ttl=3600)
                    sent_count += 1
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Failed to send Push to {user.username}: {e}"))
            else:
                skipped_count += 1
                
        self.stdout.write(self.style.SUCCESS(f"Daily reminders sent: {sent_count}, Skipped: {skipped_count}"))
