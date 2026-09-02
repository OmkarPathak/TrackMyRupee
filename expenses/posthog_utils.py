from __future__ import annotations

"""
posthog_utils.py — thin server-side PostHog capture helper.

Usage:
    from expenses.posthog_utils import ph_capture

    ph_capture(request.user, 'expense_created', {
        'amount': str(expense.amount),
        'category': expense.category,
    })

The helper is a no-op when POSTHOG_API_KEY is not set, so it is safe
in development and CI environments without any configuration.
"""

import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily initialise the PostHog client (one instance per process)."""
    global _client
    if _client is not None:
        return _client

    api_key = getattr(settings, 'POSTHOG_API_KEY', None)
    if not api_key:
        return None

    try:
        import posthog as _ph

        _ph.api_key = api_key
        _ph.host = getattr(settings, 'POSTHOG_HOST', 'https://us.i.posthog.com')
        # Disable the noisy startup log from the SDK
        _ph.disabled = False
        _client = _ph
    except ImportError:
        logger.warning('posthog package is not installed. Server-side events will be skipped.')
        _client = False  # falsy sentinel so we don't retry

    return _client


def ph_capture(user, event: str, properties: dict[str, Any] | None = None):
    """
    Fire a PostHog event for an authenticated Django user.

    Args:
        user:       A Django User instance (or any object with .id and .email).
        event:      The PostHog event name (e.g. 'expense_created').
        properties: Optional dict of event properties.
    """
    client = _get_client()
    if not client:
        return

    try:
        distinct_id = str(user.id)
        props = properties or {}
        client.capture(distinct_id, event=event, properties=props)
    except Exception:
        logger.debug('PostHog capture failed for event %s', event, exc_info=True)


def ph_identify(user, extra_properties: dict[str, Any] | None = None):

    """
    Identify/update a user's Person profile in PostHog.
    Called on login and signup.
    """
    client = _get_client()
    if not client:
        return

    try:
        profile = getattr(user, 'profile', None)
        props = {
            'email': user.email,
            'username': user.username,
            'date_joined': user.date_joined.isoformat() if user.date_joined else None,
            'tier': getattr(profile, 'active_tier', 'FREE') if profile else 'FREE',
        }
        if extra_properties:
            props.update(extra_properties)
        client.identify(str(user.id), properties=props)
    except Exception:
        logger.debug('PostHog identify failed for user %s', user.id, exc_info=True)
