import calendar
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import Loan, Notification, RecurringTransaction, SavingsGoal, UserProfile
from .utils import translate_digits as ud


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
        # Evaluate once to avoid 3 separate queries (filter + exists + count)
        unread_notifications = list(
            Notification.objects.filter(user=request.user, is_read=False).order_by('-created_at')[:9]
        )
        return {
            'notifications': unread_notifications,
            'has_unread_notifications': bool(unread_notifications),
            'unread_notifications_count': len(unread_notifications),
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
        try:
            from .account_valuation import process_matured_deposit_incomes
            process_matured_deposit_incomes(request.user)
        except Exception:
            pass
        cache_key = f'sidebar_accounts_{request.user.id}'
        result = cache.get(cache_key)
        if result is None:
            from .models import Account
            all_accounts = list(Account.objects.filter(user=request.user, is_active=True).order_by('name'))
            count = len(all_accounts)
            result = {
                'sidebar_accounts': all_accounts[:5],
                'sidebar_accounts_count': count,
                'has_more_accounts': count > 5,
            }
            cache.set(cache_key, result, 300)  # 5 minutes
        return result
    return {'sidebar_accounts': [], 'sidebar_accounts_count': 0, 'has_more_accounts': False}

def sidebar_badges(request):
    """Provides badge counts for the sidebar navigation."""
    if not request.user.is_authenticated:
        return {}

    cache_key = f'sidebar_badges_{request.user.id}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    today = timezone.now().date()
    next_week = today + timedelta(days=7)

    # 1. Goals: Active (incomplete) goals
    active_goals_count = SavingsGoal.objects.filter(user=request.user, is_completed=False).count()

    # 2. Subscriptions: Due within next 7 days
    upcoming_subscriptions_count = RecurringTransaction.objects.filter(
        user=request.user,
        is_active=True,
        next_due_date__gte=today,
        next_due_date__lte=next_week
    ).count()

    # 3. Calendar: Events this week
    calendar_this_week_count = upcoming_subscriptions_count

    # 4. Loans: Active loans count
    active_loans_count = Loan.objects.filter(user=request.user, is_active=True).count()

    result = {
        'active_goals_count': active_goals_count,
        'upcoming_subscriptions_count': upcoming_subscriptions_count,
        'calendar_this_week_count': calendar_this_week_count,
        'active_loans_count': active_loans_count,
    }
    cache.set(cache_key, result, 120)  # 2 minutes
    return result

def personalization(request):
    """Provides time-based greetings and month progress encouragement to all templates."""
    if not request.user.is_authenticated:
        return {}

    # Get current time in user's timezone (Middleware handles activation)
    now = timezone.localtime(timezone.now())
    hour = now.hour
    
    # 1. Time-based greeting logic
    if 5 <= hour < 12:
        greeting = _("Good morning")
    elif 12 <= hour < 17:
        greeting = _("Good afternoon")
    elif 17 <= hour < 21:
        greeting = _("Good evening")
    else:
        greeting = _("Good night")

    # 2. User name logic (Prefer first name, fallback to username)
    user_name = request.user.first_name or request.user.username

    # 3. Month progress & Encouragement
    day = now.day
    
    unused_weekday, last_day = calendar.monthrange(now.year, now.month)
    
    # Heuristic for week number
    week_num = (day - 1) // 7 + 1
    # Use calendar.month_name for stable English keys
    month_name = _(calendar.month_name[now.month])
    
    # Suffix for ordinal week (1st, 2nd, etc.)
    if 10 <= week_num <= 20:
        suffix = _('th')
    else:
        suffix = {1: _('st'), 2: _('nd'), 3: _('rd')}.get(week_num % 10, _('th'))
            
    week_str = _("%(week_num)s%(suffix)s week of %(month_name)s") % {
        'week_num': ud(week_num),
        'suffix': suffix,
        'month_name': month_name
    }
    
    # Context-aware encouragement
    if day > last_day - 3:
        encouragement = _("month almost over - stay disciplined")
    elif week_num >= 4:
        encouragement = _("finish strong")
    elif day <= 7:
        encouragement = _("fresh start - track everything")
    else:
        encouragement = _("keep the momentum going")

    return {
        'personalized_greeting': greeting,
        'greeting_user_name': user_name,
        'month_progress_encouragement': f"{week_str} - {encouragement}"
    }
