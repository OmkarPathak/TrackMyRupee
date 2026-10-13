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

Privacy rules enforced here:
  * Nothing is sent unless the current request carries the visitor's analytics consent
    (the `tmr_consent=accepted` cookie, read by AnalyticsConsentMiddleware). Calls made
    outside a request (management commands, webhooks) therefore send nothing.
  * Users are identified only by a salted hash of their id, never by email or username.
  * Amounts, descriptions and other free-form or financial values are stripped from
    event properties before sending.
"""

import contextvars
import hashlib
import hmac
import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_client = None

# Set per request by AnalyticsConsentMiddleware. Defaults to "no consent".
_analytics_consent = contextvars.ContextVar('tmr_analytics_consent', default=False)

# Property names that must never leave the app, whatever the call site passes.
_FORBIDDEN_PROPERTY_KEYS = {
    'amount', 'target_amount', 'balance', 'salary', 'description', 'text', 'note', 'notes',
    'email', 'username', 'name', 'account_name', 'merchant', 'interest_rate',
}


def set_analytics_consent(value: bool):
    """Record whether the current request has analytics consent. Returns a reset token."""
    return _analytics_consent.set(bool(value))


def reset_analytics_consent(token):
    _analytics_consent.reset(token)


def analytics_consent_given() -> bool:
    return _analytics_consent.get()


def posthog_distinct_id(user) -> str:
    """Salted hash of the user id, so PostHog never sees the raw id, email or username."""
    secret = (getattr(settings, 'SECRET_KEY', '') or '').encode()
    digest = hmac.new(secret, f'posthog:{user.pk}'.encode(), hashlib.sha256).hexdigest()
    return digest[:32]


def _scrub(properties: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (properties or {}).items() if str(k).lower() not in _FORBIDDEN_PROPERTY_KEYS}


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
        logger.debug('posthog package is not installed. Server-side events will be skipped.')
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
    if not analytics_consent_given():
        return
    client = _get_client()
    if not client:
        return

    try:
        client.capture(posthog_distinct_id(user), event=event, properties=_scrub(properties))
    except Exception:
        logger.debug('PostHog capture failed for event %s', event, exc_info=True)


def ph_identify(user, extra_properties: dict[str, Any] | None = None):
    """
    Set non-identifying properties (plan tier, signup method) on the user's PostHog person.
    Called on login and signup. No email, username or join date is sent.
    """
    if not analytics_consent_given():
        return
    client = _get_client()
    if not client:
        return

    try:
        profile = getattr(user, 'profile', None)
        props = {'tier': getattr(profile, 'active_tier', 'FREE') if profile else 'FREE'}
        props.update(_scrub(extra_properties))
        client.identify(posthog_distinct_id(user), properties=props)
    except Exception:
        logger.debug('PostHog identify failed', exc_info=True)
