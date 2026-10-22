"""The Insights Engine: one set of rules, many surfaces. See expenses/insights/README.md.

This module stays import-light on purpose: settings and the dashboard import it on every request.
"""
from django.conf import settings
from django.core.cache import cache

MODES = ('off', 'shadow', 'on')


def engine_mode(user=None) -> str:
    """"off", "shadow" or "on" for this person. A named user (INSIGHTS_ENGINE_USER_IDS) always gets "on"."""
    if user is not None and getattr(user, 'pk', None) in settings.INSIGHTS_ENGINE_USER_IDS:
        return 'on'
    mode = settings.INSIGHTS_ENGINE_MODE
    return mode if mode in MODES else 'off'


def is_engine_on(user) -> bool:
    return engine_mode(user) == 'on'


def channel_enabled(channel: str) -> bool:
    return channel in settings.INSIGHTS_CHANNELS


def _version_key(user_id) -> str:
    return f'insights_version_{user_id}'


def version(user_id) -> int:
    """Changes whenever what a person's pages would show changes (a run, a dismissal, a setting): the key of the
    short-lived copies of rendered strips."""
    return cache.get(_version_key(user_id), 1)


def touch(user_id) -> None:
    try:
        cache.incr(_version_key(user_id))
    except ValueError:
        cache.set(_version_key(user_id), 2, 7 * 86400)
