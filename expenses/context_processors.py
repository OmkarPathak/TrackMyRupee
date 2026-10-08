import calendar
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    Announcement,
    Loan,
    Notification,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from .utils import translate_digits as ud


GLOBAL_BADGE_CACHE_TTL = 120  # seconds; signal invalidation covers the common write paths


def global_badge_data_cache_key(user_id):
    return f'global_badge_data_{user_id}'


def _build_global_badge_data(user):
    """Run the per-user queries behind the navbar/sidebar/base template. Query logic is
    unchanged from the five processors this replaced."""
    from webpush.models import PushInformation

    from .models import Account

    today = timezone.now().date()
    next_week = today + timedelta(days=7)

    unread_notifications = list(
        Notification.objects.filter(user=user, is_read=False).order_by('-created_at')[:9]
    )
    all_accounts = list(Account.objects.filter(user=user, is_active=True).order_by('name'))
    # Subscriptions: due within next 7 days or overdue
    upcoming_subscriptions_count = RecurringTransaction.objects.filter(
        user=user, is_active=True, next_due_date__lte=next_week
    ).count()

    return {
        'notifications': unread_notifications,
        'has_unread_notifications': bool(unread_notifications),
        'unread_notifications_count': len(unread_notifications),
        'active_goals_count': SavingsGoal.objects.filter(user=user, is_completed=False).count(),
        'upcoming_subscriptions_count': upcoming_subscriptions_count,
        'calendar_this_week_count': upcoming_subscriptions_count,
        'active_loans_count': Loan.objects.filter(user=user, is_active=True).count(),
        'sidebar_accounts': all_accounts[:5],
        'sidebar_accounts_count': len(all_accounts),
        'has_more_accounts': len(all_accounts) > 5,
        'is_webpush_subscribed': PushInformation.objects.filter(user=user).exists(),
    }


def global_badge_data(request):
    """Single cache entry for notifications, sidebar badges, sidebar accounts and webpush
    subscription status (replaces the notifications / sidebar_badges / user_accounts /
    webpush_vapid_key processors, which each had their own TTL).

    Deliberately NOT part of the per-user blob:
    - VAPID public key: comes from settings, no DB or cache cost.
    - Active announcement: cached per *tier* (shared by all users) and invalidated by
      Announcement.save() via invalidate_announcement_cache(); folding it into a per-user
      key would make that invalidation impossible. It's a cache hit, not a query.
    - Matured-deposit processing: a write side effect, not a read, so it keeps its own
      1-hour cooldown and runs *before* the cache lookup so any Account changes it makes
      are visible (and signal-invalidate the blob) on the same request.
    """
    vapid = {'vapid_public_key': getattr(settings, 'WEBPUSH_SETTINGS', {}).get('VAPID_PUBLIC_KEY', '')}
    user = request.user
    if not user.is_authenticated:
        return {
            **vapid,
            'notifications': [],
            'has_unread_notifications': False,
            'sidebar_accounts': [],
            'sidebar_accounts_count': 0,
            'has_more_accounts': False,
            'is_webpush_subscribed': False,
            **_active_announcement(request),
        }

    cooldown_key = f'deposit_matured_processed_{user.id}'
    if not cache.get(cooldown_key):
        try:
            from .account_valuation import process_matured_deposit_incomes
            process_matured_deposit_incomes(user)
            cache.set(cooldown_key, True, 3600)
        except Exception:
            pass

    cache_key = global_badge_data_cache_key(user.id)
    data = cache.get(cache_key)
    if data is None:
        data = _build_global_badge_data(user)
        cache.set(cache_key, data, GLOBAL_BADGE_CACHE_TTL)
    return {**data, **vapid, **_active_announcement(request)}


def currency_symbol(request):
    """Provides the user's preferred currency symbol to all templates."""
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            return {'currency_symbol': profile.currency}
        except UserProfile.DoesNotExist:
            return {'currency_symbol': '₹'}
    return {'currency_symbol': '₹'}

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


def _active_announcement(request):
    """Provides the active modal feature announcement to all templates."""
    tier = 'ANONYMOUS'
    if request.user.is_authenticated and hasattr(request.user, 'profile'):
        tier = request.user.profile.active_tier or 'FREE'

    cache_key = f'active_announcement_{tier}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        now = timezone.now()
        announcements = Announcement.objects.filter(
            show_modal=True,
            status__in=['DRAFT', 'QUEUED', 'SENT']
        ).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).order_by('-created_at')

        active = None
        for ann in announcements:
            if ann.audience == 'ALL':
                active = ann
                break
            elif request.user.is_authenticated and hasattr(request.user, 'profile'):
                if ann.audience == 'PAID' and tier in ['PLUS', 'PRO']:
                    active = ann
                    break
                elif ann.audience == 'FREE' and tier == 'FREE':
                    active = ann
                    break

        result = {
            'active_announcement': active
        }

        ttl = 300
        if active and active.expires_at:
            remaining = int((active.expires_at - now).total_seconds())
            if remaining > 0:
                ttl = min(300, remaining)

        cache.set(cache_key, result, ttl)
        return result
    except Exception:
        return {
            'active_announcement': None
        }


