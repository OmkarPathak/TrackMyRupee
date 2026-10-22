"""A person's Insights choices, kept in UserProfile.insights_prefs (the profile is loaded on every request, so
reading them costs no query).

    enabled      the whole feature on or off
    modules_off  modules the person has switched off
    notify       'normal' (bell at most 1 a day), 'quiet' (bell at most 1 a week, no push), 'off' (pages only)
    push         may insights be pushed at all (still capped, still never for sensitive ones)
    muted        {rule_id: ISO date}: rules silenced after two "not helpful" votes
    digest       the email digest (Pro): 'weekly', 'daily' or 'off'; the unsubscribe link sets it to 'off'
    emergency_months  the person's own cushion target, in months of spending (rules compare with it, not with anyone else)
    learning     engine-owned: True when the last run found too little history to say anything yet
"""
from __future__ import annotations

from datetime import date, timedelta

from .registry import modules_with_rules

NOTIFY_CHOICES = ('normal', 'quiet', 'off')
MUTE_DAYS = 90
EMERGENCY_MONTHS = (3, 6, 9, 12, 18, 24)
DIGEST_CHOICES = ('weekly', 'daily', 'off')
DEFAULTS = {'enabled': True, 'modules_off': [], 'notify': 'normal', 'push': True, 'muted': {}, 'learning': False,
            'emergency_months': 6, 'digest': 'weekly'}


def get(profile) -> dict:
    stored = getattr(profile, 'insights_prefs', None) or {}
    merged = {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}
    merged['modules_off'] = list(merged['modules_off'])
    merged['muted'] = dict(merged['muted'])
    if merged['notify'] not in NOTIFY_CHOICES:
        merged['notify'] = 'normal'
    if merged['digest'] not in DIGEST_CHOICES:
        merged['digest'] = DEFAULTS['digest']
    if merged['emergency_months'] not in EMERGENCY_MONTHS:
        merged['emergency_months'] = DEFAULTS['emergency_months']
    return merged


def module_on(prefs: dict, module) -> bool:
    return prefs['enabled'] and str(getattr(module, 'value', module)) not in prefs['modules_off']


def is_muted(prefs: dict, rule_id: str, today: date) -> bool:
    until = prefs['muted'].get(rule_id)
    try:
        return bool(until) and date.fromisoformat(until) >= today
    except ValueError:
        return False


def _update(profile, mutate, **extra) -> dict:
    """Change the stored choices on a fresh read inside a lock, so two writers (the Settings form, an unsubscribe
    link, a nightly run) never overwrite each other's change. `extra` are other profile columns to set."""
    from django.db import transaction

    from ..models import UserProfile

    with transaction.atomic():
        fresh = UserProfile.objects.select_for_update().filter(pk=profile.pk).only('id', 'insights_prefs').first()
        current = get(fresh if fresh is not None else profile)
        mutate(current)
        UserProfile.objects.filter(pk=profile.pk).update(insights_prefs=current, **extra)
    profile.insights_prefs = current
    for name, value in extra.items():
        setattr(profile, name, value)
    from . import touch
    touch(profile.user_id)
    return current


def save(profile, *, enabled: bool, modules_off, notify: str, push: bool, emergency_months: int | None = None,
         digest: str | None = None) -> dict:
    """Store the choices from the Settings form. Unknown modules and choices are ignored; the engine-owned keys
    (muted, learning) are kept as they are."""
    known = {m.value for m in modules_with_rules()}

    def apply(current):
        current.update({
            'enabled': bool(enabled),
            'modules_off': sorted({str(m) for m in modules_off} & known),
            'notify': notify if notify in NOTIFY_CHOICES else 'normal',
            'push': bool(push),
        })
        if emergency_months is not None:
            current['emergency_months'] = emergency_months if emergency_months in EMERGENCY_MONTHS else current['emergency_months']
        if digest in DIGEST_CHOICES:
            current['digest'] = digest

    return _update(profile, apply, insights_stale=True)


def mute(profile, rule_id: str, today: date) -> None:
    _update(profile, lambda current: current['muted'].__setitem__(
        rule_id, (today + timedelta(days=MUTE_DAYS)).isoformat()))


def set_digest(profile, choice: str) -> None:
    """Change only the email digest (the unsubscribe link does this, without touching anything else)."""
    def apply(current):
        current['digest'] = choice if choice in DIGEST_CHOICES else current['digest']

    _update(profile, apply)


def stamp_run(profile, *, learning: bool, now) -> None:
    """A run finished: record when, and whether there was too little history to say anything yet."""
    _update(profile, lambda current: current.__setitem__('learning', bool(learning)),
            insights_generated_at=now, insights_stale=False)
