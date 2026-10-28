"""Gentle notes: a few calm, optional observations on the family inventory.

Rules in short: they are built only from what is already shared (the family summary), never from anyone's private
data; they never compare people or judge an amount; each says what was noticed and, where it fits, that there is
nothing to do. Each person can dismiss a note or switch them all off for themselves.
"""
import re
from dataclasses import dataclass

from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from ..models import GentleNoteDismissal, HouseholdMember
from . import catalog

MAX_NOTES = 3


@dataclass(frozen=True)
class Note:
    key: str
    text: str


def _join(names):
    """"A", "A and B", "A, B and C"."""
    names = list(names)
    if len(names) <= 1:
        return ''.join(names)
    return _("%(head)s and %(last)s") % {'head': ', '.join(names[:-1]), 'last': names[-1]}


def _first_name(full_name):
    return (full_name.split() or [''])[0]


def _named_in(first_name, text):
    return bool(first_name) and re.search(rf'\b{re.escape(first_name)}\b', text, re.IGNORECASE) is not None


def _health_gap(data):
    """Some members have health cover shared (held or named as covered) and some have none shared."""
    policies = [i for i in data.items if i.kind == catalog.INSURANCE and i.type_code == 'HEALTH_INSURANCE'
                and i.show_amounts]
    if not policies:
        return None
    covered, missing = [], []
    for member in data.members:
        first = _first_name(member.name)
        holds = any(i.owner_id == member.user_id and not i.covers for i in policies)
        named = any(_named_in(first, i.covers) for i in policies)
        (covered if holds or named else missing).append(member)
    if not covered or not missing:
        return None
    names = _join(_first_name(m.name) for m in covered)
    gap = _join(_first_name(m.name) for m in missing)
    text = _("Health cover is shared for %(covered)s. Nothing is shared yet for %(missing)s. "
             "If they have a policy, they can add it. If not, there is nothing to do.") % {
        'covered': names, 'missing': gap}
    return Note('health-gap:' + ','.join(str(m.user_id) for m in sorted(missing, key=lambda m: m.user_id)), text)


def _upcoming(data, kind):
    rows = [u for u in data.upcoming if u.kind == kind]
    if not rows:
        return None
    parts = _join(_("%(name)s on %(date)s") % {'name': u.name, 'date': date_format(u.when, 'j M')} for u in rows)
    key = f"{kind}s:" + ','.join(f"{u.name}@{u.when.isoformat()}" for u in rows)
    if kind == 'bill':
        text = ngettext("One card bill falls in the next 30 days: %(items)s. Nothing to do but keep it in mind.",
                        "%(count)d card bills fall in the next 30 days: %(items)s. Nothing to do but keep them in mind.",
                        len(rows)) % {'count': len(rows), 'items': parts}
    else:
        text = ngettext("One insurance premium falls due in the next 30 days: %(items)s.",
                        "%(count)d insurance premiums fall due in the next 30 days: %(items)s.",
                        len(rows)) % {'count': len(rows), 'items': parts}
    return Note(key[:120], text)


def build_notes(data):
    """Every note the shared data gives rise to, before anyone's dismissals."""
    notes = [n for n in (_health_gap(data), _upcoming(data, 'bill'), _upcoming(data, 'premium')) if n]
    return notes[:MAX_NOTES]


def notes_for(user, household, data):
    """(notes still to show, whether this person has gentle notes switched on)."""
    membership = HouseholdMember.objects.filter(user=user, household=household).first()
    if membership is None or not membership.gentle_notes:
        return [], False
    dismissed = set(GentleNoteDismissal.objects.filter(user=user, household=household)
                    .values_list('key', flat=True))
    return [n for n in build_notes(data) if n.key not in dismissed], True


def dismiss(user, household, data, key):
    """Dismiss the note called ``key``. Only a note that is currently showing can be dismissed."""
    if key in {n.key for n in build_notes(data)}:
        GentleNoteDismissal.objects.get_or_create(user=user, household=household, key=key)
        return True
    return False


def set_enabled(user, household, enabled):
    HouseholdMember.objects.filter(user=user, household=household).update(gentle_notes=enabled)
