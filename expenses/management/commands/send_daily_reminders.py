
import json
from django.conf import settings
from django.templatetags.static import static
from django.core.management.base import BaseCommand
from django.utils import timezone
from webpush import send_user_notification
from expenses.models import UserProfile, Expense, Notification
from webpush.models import PushInformation

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
        no_subscription_count = 0
        
        # Base URL for media assets
        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')
        icon_path = static('img/pwa-icon-512.png')
        absolute_icon_url = f"{site_url}{icon_path}"
        
        for profile in profiles:
            user = profile.user
            
            # 1. Internal UI Notification
            title = "Expense Reminder 💸"
            message = "Don't forget to add your expenses for today to keep your tracker up to date!"
            
            # ui_slug = f"daily-expense-reminder-{today}"
            # if not Notification.objects.filter(user=user, slug=ui_slug).exists():
            #     Notification.objects.create(
            #         user=user,
            #         title=title,
            #         message=message,
            #         notification_type='SYSTEM',
            #         slug=ui_slug,
            #         link='/expenses/add/'
            #     )
            
            # 2. External Push Notification
            if PushInformation.objects.filter(user=user).exists():
                payload = {
                    "head": title,
                    "body": message,
                    "icon": absolute_icon_url,
                    "url": f"{site_url}/expenses/add/"
                }
                
                try:
                    send_user_notification(user=user, payload=payload, ttl=3600)
                    sent_count += 1
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"Failed to send Push to {user.username}: {e}"))
            else:
                no_subscription_count += 1
                
        self.stdout.write(self.style.SUCCESS(f"Daily reminders sent: {sent_count}, No Subscription: {no_subscription_count}"))
