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
    return {
        'POSTHOG_API_KEY': getattr(settings, 'POSTHOG_API_KEY', None),
        'POSTHOG_HOST': getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com'),
    }


def plan_details(request):
    """
    Exposes PLAN_DETAILS from plans.py to all templates.
    """
    return {
        'PLAN_DETAILS': PLAN_DETAILS
    }
