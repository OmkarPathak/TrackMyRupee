"""Turn household and plan data into what the family pages show: names, avatars and the plan banner."""
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import ngettext

from . import plan as plan_rules

# Bootstrap "subtle" colour pairs, so avatars read in both light and dark mode like the rest of the app
AVATAR_TONES = ('success', 'info', 'warning', 'danger', 'secondary')


def display_name(user):
    return user.get_full_name() or user.username


def initials(user):
    if user.first_name and user.last_name:
        return f'{user.first_name[:1]}{user.last_name[:1]}'.upper()
    return user.username[:2].upper()


def avatar_tone(position):
    return AVATAR_TONES[position % len(AVATAR_TONES)]


def member_rows(household, viewer):
    """One row per member, with the plan state of each, for the members list."""
    state = plan_rules.household_plan_state(household)
    plans = {p.user_id: p for p in state.members}
    rows = []
    for position, membership in enumerate(household.members.select_related('user')):
        user = membership.user
        rows.append({
            'membership': membership,
            'user': user,
            'name': display_name(user),
            'initials': initials(user),
            'tone': avatar_tone(position),
            'is_you': user.pk == viewer.pk,
            'is_owner': user.pk == household.owner_id,
            'joined': membership.joined_at,
            'plan': plans.get(user.pk),
        })
    return rows, state


def _days_left(ends_at, now):
    return max(0, (ends_at - now).days)


def plan_banner(state, rows, viewer, now=None):
    """The banner for a household's plan state: {'kind': 'ok'|'warning'|'paused', 'text': str}."""
    now = now or timezone.now()
    names = {row['user'].pk: row for row in rows}

    if state.state == plan_rules.PAUSED:
        lapsed = [names[p.user_id] for p in state.lapsed if p.user_id in names]
        if any(r['is_you'] for r in lapsed):
            text = _("Family view is paused because your Pro plan has ended. It resumes as soon as everyone is on Pro. "
                     "Your own data and your sharing choices are untouched.")
        else:
            who = ', '.join(r['name'] for r in lapsed)
            text = _("Family view is paused. The Pro plan of %(names)s has ended. It resumes as soon as everyone is on Pro. "
                     "Your own data and your sharing choices are untouched.") % {'names': who}
        return {'kind': 'paused', 'text': text}

    if state.state == plan_rules.WARNING:
        first = state.ending[0]
        row = names.get(first.user_id)
        days = _days_left(first.ends_at, now)
        when = date_format(first.ends_at, 'j M')
        if row and row['is_you']:
            text = ngettext(
                "Your Pro plan ends in %(days)d day. Family view will pause on %(date)s unless you renew. Everything you share stays exactly as it is.",
                "Your Pro plan ends in %(days)d days. Family view will pause on %(date)s unless you renew. Everything you share stays exactly as it is.",
                days) % {'days': days, 'date': when}
        else:
            text = ngettext(
                "The Pro plan of %(name)s ends in %(days)d day. Family view will pause on %(date)s unless they renew. Everything you share stays exactly as it is.",
                "The Pro plan of %(name)s ends in %(days)d days. Family view will pause on %(date)s unless they renew. Everything you share stays exactly as it is.",
                days) % {'name': row['name'] if row else '', 'days': days, 'date': when}
        return {'kind': 'warning', 'text': text}

    count = len(state.members)
    text = ngettext(
        "Family view is on. %(count)d member is on Pro, so the Me / Family toggle is available.",
        "Family view is on. All %(count)d members are on Pro, so the Me / Family toggle is available to everyone.",
        count) % {'count': count}
    return {'kind': 'ok', 'text': text}
