from django.conf import settings

from .models import Notification, UserProfile


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
