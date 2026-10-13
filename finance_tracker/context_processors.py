from django.conf import settings

from .plans import PLAN_DETAILS


def google_analytics(request):
    """
    Adds GOOGLE_ANALYTICS_ID to the context if it exists in settings.
    """
    return {
        'GOOGLE_ANALYTICS_ID': getattr(settings, 'GOOGLE_ANALYTICS_ID', None)
    }


def posthog(request):
    """
    Exposes PostHog configuration to all templates for the JS snippet.
    """
    context = {
        'POSTHOG_API_KEY': getattr(settings, 'POSTHOG_API_KEY', None),
        'POSTHOG_HOST': getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com'),
    }
    user = getattr(request, 'user', None)
    if context['POSTHOG_API_KEY'] and user is not None and user.is_authenticated:
        from expenses.posthog_utils import posthog_distinct_id

        # Salted hash only; the raw id, email and username are never sent to PostHog.
        context['POSTHOG_DISTINCT_ID'] = posthog_distinct_id(user)
        profile = getattr(user, 'profile', None)
        context['POSTHOG_TIER'] = getattr(profile, 'active_tier', '') if profile else ''
    return context


def plan_details(request):
    """
    Exposes PLAN_DETAILS from plans.py to all templates.
    """
    return {
        'PLAN_DETAILS': PLAN_DETAILS
    }
