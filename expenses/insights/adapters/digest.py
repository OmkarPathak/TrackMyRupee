"""The email digest (Pro): up to three insights from different modules, in one email a week (or a day, if the person
chose that), only when there is something worth saying.

It reads what is already stored (the same rows the pages read), so it works nothing out. Never a sensitive insight,
never one the person dismissed, snoozed or acted on, never one already emailed. Every email has an unsubscribe link
(and the one-click header mail apps look for); the unsubscribe switches only the digest off.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.html import strip_tags
from django.utils.translation import gettext as _

from .. import channel_enabled, engine, telemetry
from .. import prefs as prefs_module
from ..gating import plan_of, surface_allowed
from ..types import Surface

WINDOW = {'weekly': timedelta(days=7), 'daily': timedelta(days=1)}
PICKS = 3


def plain_text(html: str) -> str:
    """The text part of an email made from its HTML: no styles or title, entities turned back into characters."""
    import html as html_lib
    import re

    html = re.sub(r'<(style|title|head)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    return ' '.join(html_lib.unescape(strip_tags(html)).split())


def _site_url() -> str:
    return getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')


def allowed(user, profile) -> str | None:
    """The person's chosen frequency when they may get the digest at all, else None."""
    prefs = prefs_module.get(profile)
    if (not channel_enabled('email') or not surface_allowed(Surface.DIGEST, plan_of(profile)) or not prefs['enabled']
            or prefs['digest'] == 'off' or prefs['notify'] == 'off' or not user.email):
        return None
    return prefs['digest']


def select(user, profile, now) -> list[tuple]:
    """[(Insight, InsightState)] for the next email, at most three from different modules, best first."""
    eligible = [(i, r) for i, r in engine.candidates(user, Surface.DASHBOARD, profile=profile, now=now,
                                                      also=(Surface.MODULE,))
                if not r.email_at and not r.locked and not i.sensitive]
    by_id = {id(i): (i, r) for i, r in eligible}
    return [by_id[id(i)] for i in engine.pick([i for i, _r in eligible], PICKS, positive_first=False)]


def _claim(row, now) -> bool:
    from ...models import InsightState
    return bool(InsightState.objects.filter(pk=row.pk, email_at__isnull=True).update(email_at=now))


def send(user, profile, now) -> int:
    """Send this person's digest if it is due and there is something to say. Returns 1 when an email went, else 0."""
    from ...models import EmailLog, InsightState
    from ..views import digest_token

    frequency = allowed(user, profile)
    if frequency is None:
        return 0
    if InsightState.objects.filter(user=user, email_at__gte=now - WINDOW[frequency] + timedelta(hours=2)).exists():
        return 0
    chosen = select(user, profile, now)
    if not chosen:
        return 0
    claimed = [(i, r) for i, r in chosen if _claim(r, now)]
    if not claimed:
        return 0
    unsubscribe = f"{_site_url()}{reverse('insights-digest-unsubscribe', args=[digest_token(user)])}"
    with translation.override(profile.language or 'en'):
        subject = _('Your TrackMyRupee insights, %(date)s') % {'date': date_format(timezone.localtime(now), 'j M')}
        html = render_to_string('emails/insight_digest.html', {
            'items': [{'title': i.title, 'message': i.message, 'link': f"{_site_url()}{reverse('insights-go', args=[r.uuid])}",
                       'action': i.action.label if i.action else ''} for i, r in claimed],
            'unsubscribe_url': unsubscribe, 'settings_url': f"{_site_url()}{reverse('profile-settings')}#insights",
            'site_url': _site_url(), 'frequency': frequency})
    message = EmailMultiAlternatives(
        subject=subject, body=plain_text(html), from_email=settings.DEFAULT_FROM_EMAIL, to=[user.email],
        headers={'List-Unsubscribe': f'<{unsubscribe}>', 'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'})
    message.attach_alternative(html, 'text/html')
    try:
        if not message.send(fail_silently=False):
            raise RuntimeError('the email backend reported 0 messages sent')
    except Exception:                    # noqa: BLE001 - give the rows back so a later run can try again
        InsightState.objects.filter(pk__in=[r.pk for _i, r in claimed]).update(email_at=None)
        raise
    if not EmailLog.objects.filter(user=user, subject=subject, status='SENT').exists():
        EmailLog.objects.create(user=user, to_email=user.email, subject=subject, body=subject, html_body='', status='SENT')
    telemetry.event(user, 'delivered', surface='email', count=len(claimed))
    return 1
