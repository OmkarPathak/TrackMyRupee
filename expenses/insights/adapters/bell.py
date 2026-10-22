"""The bell and push adapter. It only formats and sends; what is worth sending was decided by the rules and by
engine.candidates.

Caps (all in addition to the alerts send_notifications already creates): the bell takes at most one new insight a
day (one a week for people who chose "quietly", none for "only on the pages"); push at most two a week, only for
rules marked time-sensitive, never for a sensitive insight, never at night, and only for people who allow it.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone

from .. import channel_enabled, engine, registry, telemetry
from .. import prefs as prefs_module
from ..types import Surface

BELL_WINDOW = {'normal': timedelta(days=1), 'quiet': timedelta(days=7)}
PUSH_PER_WEEK = 2
QUIET_FROM, QUIET_TO = 21, 8        # local time (IST): no push from 9 pm to 8 am


def in_quiet_hours(now) -> bool:
    hour = timezone.localtime(now).hour
    return hour >= QUIET_FROM or hour < QUIET_TO


def _claim(row, column: str, now) -> bool:
    """Mark the row as sent on this channel, once. False when another run got there first."""
    from ...models import InsightState
    return bool(InsightState.objects.filter(pk=row.pk, **{f'{column}__isnull': True}).update(**{column: now}))


def deliver_bell(user, profile, now) -> int:
    from ...models import InsightState, Notification

    prefs = prefs_module.get(profile)
    window = BELL_WINDOW.get(prefs['notify'])
    if window is None or not channel_enabled('bell'):
        return 0
    if InsightState.objects.filter(user=user, bell_at__gte=now - window).exists():
        return 0
    for insight, row in engine.candidates(user, Surface.BELL, profile=profile, now=now):
        if row.bell_at or row.locked or not _claim(row, 'bell_at', now):
            continue
        Notification.objects.create(
            user=user, title=insight.title, message=insight.message, notification_type='INSIGHT',
            slug=f'insight-{row.rule_id}-{row.dedupe_key}'[:255], link=reverse('insights-go', args=[row.uuid]),
            metadata={'rule_id': row.rule_id, 'surface': 'bell'})
        telemetry.event(user, 'delivered', rule_id=row.rule_id, surface='bell', count=1)
        return 1
    return 0


def deliver_push(user, profile, now, *, has_push: bool) -> int:
    from webpush import send_user_notification

    from ...models import InsightState

    prefs = prefs_module.get(profile)
    if (not has_push or not prefs['push'] or prefs['notify'] != 'normal' or not channel_enabled('push')
            or in_quiet_hours(now)):
        return 0
    week_ago = now - timedelta(days=7)
    if InsightState.objects.filter(user=user, push_at__gte=week_ago).count() >= PUSH_PER_WEEK:
        return 0
    for insight, row in engine.candidates(user, Surface.PUSH, profile=profile, now=now):
        rule = registry.get(row.rule_id)
        if row.push_at or row.locked or insight.sensitive or not (rule and rule.push) or not _claim(row, 'push_at', now):
            continue
        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')
        payload = {
            'head': insight.title, 'body': insight.message, 'icon': f"{site_url}{static('img/pwa-icon-512.png')}",
            'url': f"{site_url}{reverse('insights-go', args=[row.uuid])}",
        }
        try:
            send_user_notification(user=user, payload=payload, ttl=3600)
        except Exception:               # noqa: BLE001 - a failed push is not worth failing the batch for
            InsightState.objects.filter(pk=row.pk).update(push_at=None)        # let a later run try again
            return 0
        telemetry.event(user, 'delivered', rule_id=row.rule_id, surface='push', count=1)
        return 1
    return 0
