"""Which prompt, if any, a person sees on this visit, and what they have already answered.

Four things can ask for attention: the cookie question, an announcement, the daily-reminder card and the year in
review banner. They used to decide for themselves, in each browser, so a new device or a visit after a long break
showed them all at once. Now the answers live on the account (`UserProfile.prompt_state`, a small JSON dict that
travels with the profile row the request already loads), and one rule picks at most one of the three that are not
legal: announcement, then the daily reminder, then the year in review.

    {'cookie': {'choice': 'accepted', 'at': '2026-11-12T09:00:00+00:00', 'version': 'v1.0'},
     'reminder': {'n': 2, 'until': '...'} | {'accepted': True},
     'announcement:12': {'dismissed': '...'},
     'yir:2026': {'dismissed': '...'}}
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.utils import timezone

KEY_PATTERN = re.compile(r'^(cookie|reminder|announcement:\d+|yir:\d{4})$')
REMINDER_WAIT_DAYS = (3, 7, 14, 30)        # after the 4th "Not now" the card stays away
COOKIE_CHOICES = ('accepted', 'declined')
CONSENT_VERSION = 'v1.0'                   # the privacy policy version the choice was made under
QUIET_AFTER_JOINING = timedelta(days=1)    # nothing is announced to someone in their first day
YIR_FIRST = (12, 15)                       # the year in review shows from 15 December ...
YIR_LAST = (1, 31)                         # ... to 31 January


def state_of(profile) -> dict:
    state = getattr(profile, 'prompt_state', None)
    return state if isinstance(state, dict) else {}


def cookie_choice(profile) -> str:
    choice = (state_of(profile).get('cookie') or {}).get('choice')
    return choice if choice in COOKIE_CHOICES else ''


def _when(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else timezone.make_aware(parsed)


def apply(state: dict, key: str, action: str, value: str | None = None, now: datetime | None = None) -> dict | None:
    """The new state after `action` on `key`, or None when the pair is not allowed. Pure: nothing is saved."""
    now = now or timezone.now()
    if not KEY_PATTERN.match(key or ''):
        return None
    new = dict(state)
    if key == 'cookie':
        if action != 'choice' or value not in COOKIE_CHOICES:
            return None
        new['cookie'] = {'choice': value, 'at': now.isoformat(), 'version': CONSENT_VERSION}
    elif key == 'reminder':
        if action == 'accept':
            new['reminder'] = {'accepted': True, 'at': now.isoformat()}
        elif action == 'dismiss':
            n = int((state.get('reminder') or {}).get('n', 0)) + 1
            days = REMINDER_WAIT_DAYS[min(n, len(REMINDER_WAIT_DAYS)) - 1]
            new['reminder'] = {'n': n, 'until': (now + timedelta(days=days)).isoformat()}
        else:
            return None
    else:                                    # an announcement or a year in review
        if action != 'dismiss':
            return None
        new[key] = {'dismissed': now.isoformat()}
    return new


def save(user_id, state: dict) -> None:
    """Write the state without touching anything else (no signals, no cache invalidation: this is only what a
    person has dismissed)."""
    from .models import UserProfile
    UserProfile.objects.filter(user_id=user_id).update(prompt_state=state)


def reminder_due(state: dict, now: datetime | None = None) -> bool:
    record = state.get('reminder') or {}
    if record.get('accepted'):
        return False
    if int(record.get('n', 0)) >= len(REMINDER_WAIT_DAYS):
        return False
    until = _when(record.get('until'))
    return until is None or (now or timezone.now()) >= until


def announcement_visible(user, announcement, state: dict, now: datetime | None = None) -> bool:
    """News is for people who were around for it: not in the first day, nothing from before they joined, and
    nothing they have already closed."""
    if announcement is None:
        return False
    now = now or timezone.now()
    if f'announcement:{announcement.id}' in state:
        return False
    joined = getattr(user, 'date_joined', None)
    if joined is None:
        return True
    if now - joined < QUIET_AFTER_JOINING:
        return False
    return announcement.created_at > joined


def yir_year(today: date) -> int | None:
    """The year to look back on: shown from 15 December (that year) to 31 January (the year before)."""
    if (today.month, today.day) >= YIR_FIRST:
        return today.year
    if (today.month, today.day) <= YIR_LAST:
        return today.year - 1
    return None


def inline_flags(request, *, has_any_data: bool, yir_candidate_year: int | None, tour_pending: bool) -> dict:
    """What the dashboard shows from the three that sit in the page. At most one is decided as the one to show; when
    the reminder card goes first, the year in review waits behind it and the card's script reveals it only if it
    ends up hidden (a browser with no push support, or notifications already allowed)."""
    from .context_processors import pending_announcement

    user = request.user
    flags = {'show_reminder_card': False, 'show_year_in_review': False, 'yir_waits_for_reminder': False}
    if user.username == 'demo':
        return flags
    profile = user.profile
    state = state_of(profile)
    if not cookie_choice(profile):
        return flags                                   # the cookie question comes first; this visit stays quiet
    if tour_pending or pending_announcement(request) is not None:
        return flags
    yir_open = bool(yir_candidate_year) and f'yir:{yir_candidate_year}' not in state
    from .context_processors import global_badge_data_cache_key
    subscribed = bool((cache.get(global_badge_data_cache_key(user.pk)) or {}).get('is_webpush_subscribed'))
    if has_any_data and reminder_due(state) and not subscribed:
        flags['show_reminder_card'] = True
        flags['show_year_in_review'] = yir_open
        flags['yir_waits_for_reminder'] = yir_open
    elif yir_open:
        flags['show_year_in_review'] = True
    return flags
