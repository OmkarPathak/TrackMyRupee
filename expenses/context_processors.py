from django.conf import settings

from datetime import timedelta
from django.utils import timezone
from .models import Notification, UserProfile, SavingsGoal, RecurringTransaction


def webpush_vapid_key(request):
    """Provides the VAPID public key and subscription status to all templates."""
    webpush_settings = getattr(settings, 'WEBPUSH_SETTINGS', {})
    is_subscribed = False
    
    if request.user.is_authenticated:
        from webpush.models import PushInformation
        is_subscribed = PushInformation.objects.filter(user=request.user).exists()
        
    return {
        'vapid_public_key': webpush_settings.get('VAPID_PUBLIC_KEY', ''),
        'is_webpush_subscribed': is_subscribed
    }

def notifications(request):
    """Provides unread notifications to all templates."""
    if request.user.is_authenticated:
        # Get unread notifications, ordered by newest first, limited to 5
        unread_notifications = Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:9]
        has_unread = unread_notifications.exists()

        return {
            'notifications': unread_notifications,
            'has_unread_notifications': has_unread,
            'unread_notifications_count': unread_notifications.count()
        }
    return {'notifications': [], 'has_unread_notifications': False}

def currency_symbol(request):
    """Provides the user's preferred currency symbol to all templates."""
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            return {'currency_symbol': profile.currency}
        except UserProfile.DoesNotExist:
            return {'currency_symbol': '₹'}
    return {'currency_symbol': '₹'}

def user_accounts(request):
    """Provides user accounts to all templates for the sidebar."""
    if request.user.is_authenticated:
        from .models import Account
        accounts = Account.objects.filter(user=request.user).order_by('name')
        count = accounts.count()
        return {
            'sidebar_accounts': accounts[:5],
            'sidebar_accounts_count': count,
            'has_more_accounts': count > 5
        }
    return {'sidebar_accounts': [], 'sidebar_accounts_count': 0, 'has_more_accounts': False}

def sidebar_badges(request):
    """Provides badge counts for the sidebar navigation."""
    if not request.user.is_authenticated:
        return {}

    today = timezone.now().date()
    next_week = today + timedelta(days=7)

    # 1. Goals: Active (incomplete) goals
    active_goals_count = SavingsGoal.objects.filter(user=request.user, is_completed=False).count()

    # 2. Subscriptions: Due within next 7 days
    upcoming_subscriptions_count = 0
    active_recurring = RecurringTransaction.objects.filter(user=request.user, is_active=True)
    for rt in active_recurring:
        if today <= rt.next_due_date <= next_week:
            upcoming_subscriptions_count += 1

    # 3. Calendar: Events this week (reusing subscription count for now as they are the primary scheduled events)
    # We could also include other items if available.
    calendar_this_week_count = upcoming_subscriptions_count

    return {
        'active_goals_count': active_goals_count,
        'upcoming_subscriptions_count': upcoming_subscriptions_count,
        'calendar_this_week_count': calendar_this_week_count,
    }
