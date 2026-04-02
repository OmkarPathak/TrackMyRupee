
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand
from django.template.loader import render_to_string
from django.utils import timezone
from django.db.models import Sum
from webpush import send_user_notification

from expenses.models import Notification, RecurringTransaction, UserProfile, SavingsGoal, Expense, Category
from finance_tracker.plans import PLAN_DETAILS


class Command(BaseCommand):
    help = 'Sends optimized notifications (Recurring, Milestones, High Spending) with deduplication'

    def handle(self, *args, **kwargs):
        self.today = timezone.now().date()
        self.stdout.write(f"Starting notification run for {self.today}...")
        
        users = UserProfile.objects.all().select_related('user')
        
        for profile in users:
            user = profile.user
            self.stdout.write(f"Processing notifications for {user.username}...")
            
            self.current_user_notifications = []
            
            # 1. Check for Upcoming Recurring Transactions (Income, Expense, Transfer)
            self._process_recurring_reminders(user)
            
            # 2. Check for AI Insights: High Spending
            self._process_budget_alerts(user)
            
            # 3. Check for AI Insights: Milestones
            self._process_milestone_alerts(user)
            
            # 4. Check for Subscription Expiries
            self._process_subscription_reminders(profile)

            # 5. Send Consolidated Email
            if self.current_user_notifications:
                self._send_consolidated_email(user, self.current_user_notifications)
            
        # 6. Cleanup Old Notifications
        self._cleanup_old_notifications()
        
        self.stdout.write(self.style.SUCCESS("Notification run complete!"))

    def _is_recently_sent(self, user, slug):
        """Deduplication logic: check if this slug was sent in the current calendar month."""
        return Notification.objects.filter(
            user=user,
            slug=slug,
            created_at__year=self.today.year,
            created_at__month=self.today.month
        ).exists()

    def _create_notification(self, user, title, message, n_type, slug=None, link=None, metadata=None, related_transaction=None):
        """Helper to create UI and Push notification if not recently sent."""
        if slug and self._is_recently_sent(user, slug):
            return False

        # 1. Create UI Notification
        Notification.objects.create(
            user=user,
            title=title,
            message=message,
            notification_type=n_type,
            slug=slug,
            link=link,
            metadata=metadata,
            related_transaction=related_transaction
        )
        
        # 2. Send Push Notification (WebPush)
        payload = {
            "head": title,
            "body": message,
            "icon": "/static/img/pwa-icon-512.png", 
            "url": link or "/notifications/"
        }
        try:
            send_user_notification(user=user, payload=payload, ttl=1000)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Failed to send Push to {user}: {e}"))
            
        # 3. Queue for Email Consolidation
        self.current_user_notifications.append({
            'title': title,
            'message': message,
            'type': n_type,
            'link': link or "/notifications/"
        })
        return True

    def _send_consolidated_email(self, user, notifications):
        """Sends a single consolidated HTML email only if the user tier allows it."""
        if not user.email:
            return

        profile = user.profile
        tier = profile.active_tier
        if not PLAN_DETAILS.get(tier, {}).get('limits', {}).get('email_notifications', False):
            self.stdout.write(f"Skipping email for {user.username} ({tier.capitalize()} Tier)")
            return

        subject = notifications[0]['title'] if len(notifications) == 1 else "Your Daily Financial Digest"
        
        # Prepare context for the template
        context = {
            'user': user,
            'notifications': notifications,
            'today': self.today,
            'currency_symbol': profile.currency,
            'base_url': settings.SITE_URL if hasattr(settings, 'SITE_URL') else "https://trackmyrupee.com"
        }

        html_content = render_to_string('email/recurring_reminder.html', context)
        text_content = "\n\n".join([f"{n['title']}: {n['message']}" for n in notifications])

        try:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email]
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send()
            self.stdout.write(self.style.SUCCESS(f"Sent consolidated email to {user.email} ({len(notifications)} alerts)"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to send Email to {user.email}: {e}"))

    def _process_recurring_reminders(self, user):
        """Notifies about upcoming recurring transactions (Income, Expense, Transfer) in 3 days."""
        reminder_days = 3
        due_date = self.today + timedelta(days=reminder_days)
        
        recurring = RecurringTransaction.objects.filter(user=user, is_active=True)
        for rt in recurring:
            next_due = rt.next_due_date
            if next_due == due_date:
                slug = f"recurring-{rt.id}-{next_due.year}-{next_due.month}"
                title = f"Upcoming {rt.get_transaction_type_display()}: {rt.description}"
                message = f"Your {rt.get_frequency_display().lower()} {rt.get_transaction_type_display().lower()} of {rt.currency}{rt.amount} is due on {next_due.strftime('%b %d')}."
                
                # Dynamic Link based on type
                link = "/expenses/" if rt.transaction_type == 'EXPENSE' else "/income/list/"
                if rt.transaction_type == 'TRANSFER': link = "/transfers/"
                
                self._create_notification(
                    user, title, message, 'RECURRING', 
                    slug=slug, link=link, related_transaction=rt
                )

    def _process_budget_alerts(self, user):
        """Notifies if user exceeds 80% or 100% of a category's budget limit."""
        categories_with_limits = Category.objects.filter(user=user, limit__gt=0)
        for cat in categories_with_limits:
            spent = Expense.objects.filter(
                user=user, category=cat.name, 
                date__year=self.today.year, date__month=self.today.month
            ).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            
            if spent >= cat.limit:
                slug = f"budget-exceeded-{cat.id}-{self.today.year}-{self.today.month}"
                title = f"Budget Exceeded: {cat.name}"
                message = f"You have exceeded your budget for {cat.name}. Total spent: {user.profile.currency}{spent} (Limit: {user.profile.currency}{cat.limit})"
                link = f"/expenses/?category={cat.name}"
                self._create_notification(user, title, message, 'ANALYTICS', slug=slug, link=link)
            elif spent >= cat.limit * Decimal('0.8'):
                slug = f"budget-warning-{cat.id}-{self.today.year}-{self.today.month}"
                title = f"Budget Alert: {cat.name}"
                message = f"You have reached 80% of your budget for {cat.name}. Total spent: {user.profile.currency}{spent} (Limit: {user.profile.currency}{cat.limit})"
                link = f"/expenses/?category={cat.name}"
                self._create_notification(user, title, message, 'ANALYTICS', slug=slug, link=link)

    def _process_milestone_alerts(self, user):
        """Notifies about savings goal milestones (50, 90, 100)."""
        goals = SavingsGoal.objects.filter(user=user, is_completed=False)
        for goal in goals:
            pct = goal.progress_percentage
            
            milestones = [(100, "Goal Completed!"), (90, "Almost There!"), (50, "Halfway Mark!")]
            for threshold, label in milestones:
                if pct >= threshold:
                    slug = f"milestone-{goal.id}-{threshold}"
                    title = f"Milestone: {goal.name}"
                    message = f"{label} You've reached {pct}% of your goal for {goal.name}!"
                    link = f"/goals/{goal.id}/"
                    
                    # We found the highest threshold reached. 
                    # Attempt to create. If it's a duplicate, we still stop (break) for THIS goal.
                    self._create_notification(user, title, message, 'MILESTONE', slug=slug, link=link)
                    break 

    def _process_subscription_reminders(self, profile):
        """Notifies about upcoming subscription expiries (2 days prior)."""
        if profile.tier in ['PLUS', 'PRO'] and not profile.is_lifetime and profile.subscription_end_date:
            expiry_target = self.today + timedelta(days=2)
            if profile.subscription_end_date.date() == expiry_target and not profile.expiry_reminder_sent:
                title = "Subscription Expiring Soon"
                message = f"Your {profile.get_tier_display()} plan ends in 2 days ({profile.subscription_end_date.strftime('%b %d')})."
                link = "/pricing/"
                if self._create_notification(profile.user, title, message, 'SYSTEM', link=link):
                    profile.expiry_reminder_sent = True
                    profile.save(update_fields=['expiry_reminder_sent'])

    def _cleanup_old_notifications(self):
        """Cleanup notifications older than 90 days."""
        cutoff_date = timezone.now() - timedelta(days=90)
        deleted_count, _ = Notification.objects.filter(created_at__lt=cutoff_date).delete()
        if deleted_count > 0:
            self.stdout.write(self.style.SUCCESS(f"Cleaned up {deleted_count} old notifications"))
