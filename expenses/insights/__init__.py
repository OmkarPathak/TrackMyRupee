"""The Insights Engine: one set of rules, many surfaces. See expenses/insights/README.md.

This module stays import-light on purpose: settings and the dashboard import it on every request.
"""
from django.conf import settings

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
