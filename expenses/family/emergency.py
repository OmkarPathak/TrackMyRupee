"""Emergency access: "if something happens to me".

A sheet lists what exists and where to look, never account numbers or passwords. The owner chooses what goes on
it and who may ask. By default a request is told to the owner, who can deny; if they do not answer within the
delay the requester gets a read-only view of the sheet. Nothing here depends on a plan: it must still open when
a subscription has lapsed. Only people in the owner's household, and only those the owner allowed, can ever ask.
"""
import logging
import re
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from ..models import (
    Account,
    EmergencyAllowed,
    EmergencyRequest,
    EmergencySheet,
    EmergencySheetItem,
    FinancialAuditLog,
    Notification,
)
from . import catalog, households, summary
from .households import FamilyError
from .presenters import display_name

logger = logging.getLogger(__name__)

NOTE_MAX = 1000
NOMINEE_MAX = 100
# A run this long looks like an account, card or Aadhaar number, which must never be written on the sheet
NUMBER_RUN = re.compile(r'\d[\d \-]{7,}\d')

NONE, ASK, PENDING, DENIED, GRANTED = 'none', 'ask', 'pending', 'denied', 'granted'


def _audit(user, action, obj_id, diff):
    FinancialAuditLog.objects.create(user=user, model_name='EmergencySheet', object_id=obj_id, action=action, diff=diff)


def sheet_of(user):
    return EmergencySheet.objects.filter(owner=user).first()


def _same_household(a, b):
    household = households.household_for(a)
    other = households.household_for(b)
    return household is not None and other is not None and household.pk == other.pk


# ---- the owner's side -------------------------------------------------------------------------------------------

def check_notes(*notes):
    for note in notes:
        if len(note) > NOTE_MAX:
            raise FamilyError('notes', _("Keep each note under %(max)d characters.") % {'max': NOTE_MAX})
        if NUMBER_RUN.search(note):
            raise FamilyError('notes', _("That looks like an account or card number. Please leave numbers out: "
                                         "say where to look, not what the number is."))


@transaction.atomic
def save_sheet(owner, mode, included, allowed_ids, documents, contacts):
    """Save ``owner``'s sheet. ``included`` maps account id -> nominee for what is on it; ``allowed_ids`` are the
    users who may ask."""
    household = households.household_for(owner)
    if household is None:
        raise FamilyError('not_member', _("You are not in a family."))
    if mode not in (EmergencySheet.REQUEST, EmergencySheet.ALWAYS):
        raise FamilyError('mode', _("Choose how they get access."))
    documents, contacts = (documents or '').strip(), (contacts or '').strip()
    check_notes(documents, contacts)

    sheet, _created = EmergencySheet.objects.get_or_create(owner=owner)
    sheet.mode, sheet.documents_note, sheet.contacts_note = mode, documents, contacts
    sheet.save()

    own_ids = set(Account.objects.filter(user=owner, is_active=True, pk__in=list(included)).values_list('pk', flat=True))
    sheet.items.exclude(account_id__in=own_ids).delete()
    for account_id in own_ids:
        EmergencySheetItem.objects.update_or_create(
            sheet=sheet, account_id=account_id, defaults={'nominee': ' '.join((included[account_id] or '').split())[:NOMINEE_MAX]})

    member_ids = set(household.members.exclude(user=owner).values_list('user_id', flat=True))
    wanted = set(allowed_ids) & member_ids
    removed = sheet.allowed.exclude(user_id__in=wanted)
    removed_users = list(removed.values_list('user_id', flat=True))
    removed.delete()
    for user_id in wanted:
        EmergencyAllowed.objects.get_or_create(sheet=sheet, user_id=user_id)
    now = timezone.now()
    sheet.requests.filter(requester_id__in=removed_users, status__in=[EmergencyRequest.PENDING, EmergencyRequest.APPROVED]) \
        .update(status=EmergencyRequest.DENIED, decided_at=now)
    _audit(owner, 'UPDATE', sheet.pk, {'mode': mode, 'items': len(own_ids), 'allowed': len(wanted)})
    return sheet


# ---- state of a request -----------------------------------------------------------------------------------------

def _latest_request(sheet, viewer):
    return sheet.requests.filter(requester=viewer).first()


def state_for(viewer, sheet, now=None):
    """What ``viewer`` can do with ``sheet``: (state, request). The only place access is decided."""
    now = now or timezone.now()
    if sheet is None or viewer.pk == sheet.owner_id:
        return NONE, None
    if not sheet.allowed.filter(user=viewer).exists() or not _same_household(viewer, sheet.owner):
        return NONE, None
    if sheet.mode == EmergencySheet.ALWAYS:
        return GRANTED, None
    request = _latest_request(sheet, viewer)
    if request is None or request.status == EmergencyRequest.CANCELLED:
        return ASK, request
    if request.status == EmergencyRequest.APPROVED:
        return GRANTED, request
    if request.status == EmergencyRequest.DENIED:
        retry = timedelta(days=settings.FAMILY_EMERGENCY_RETRY_DAYS)
        return (DENIED, request) if request.decided_at and request.decided_at + retry > now else (ASK, request)
    if request.release_at <= now:
        return GRANTED, request
    return PENDING, request


def notify_due_releases(owner=None, now=None):
    """Tell owners, once, that a delay ran out with no answer. Reading a sheet never writes; this runs from the
    notification job and when an owner opens their own emergency page. Returns how many owners were told."""
    now = now or timezone.now()
    due = EmergencyRequest.objects.filter(status=EmergencyRequest.PENDING, release_noted=False, release_at__lte=now)
    if owner is not None:
        due = due.filter(sheet__owner=owner)
    told = 0
    for request in due.select_related('sheet__owner__profile', 'requester__profile'):
        # the conditional update is the claim: only one caller wins it, so the owner is told exactly once
        if not EmergencyRequest.objects.filter(pk=request.pk, release_noted=False).update(release_noted=True):
            continue
        owner_user = request.sheet.owner
        with translation.override(getattr(owner_user.profile, 'language', None) or settings.LANGUAGE_CODE):
            _notify(owner_user, _("%(name)s can now open your emergency sheet") % {'name': display_name(request.requester)},
                    _("You did not answer within the delay, so they now have a read-only copy. You can withdraw access at any time."))
        told += 1
    return told


def _notify(user, title, message):
    link = reverse('family-emergency')
    language = getattr(getattr(user, 'profile', None), 'language', None) or settings.LANGUAGE_CODE
    Notification.objects.create(user=user, title=title[:255], message=message, link=link, notification_type='SYSTEM')
    if user.email:
        try:
            send_mail(subject=title, message=f"{message}\n", from_email=settings.DEFAULT_FROM_EMAIL,
                      recipient_list=[user.email])
        except Exception:
            logger.exception('Emergency email failed for user %s (%s)', user.pk, language)


def time_left(request, now=None):
    """(days, hours) until the request is released."""
    seconds = max(0, int((request.release_at - (now or timezone.now())).total_seconds()))
    return seconds // 86400, (seconds % 86400) // 3600


# ---- the requester's side ---------------------------------------------------------------------------------------

@transaction.atomic
def ask(viewer, sheet):
    EmergencySheet.objects.select_for_update().get(pk=sheet.pk)     # one request at a time per sheet
    state, _request = state_for(viewer, sheet)
    if state != ASK or sheet.mode != EmergencySheet.REQUEST:
        raise FamilyError('not_allowed', _("You cannot ask for this sheet right now."))
    now = timezone.now()
    request = EmergencyRequest.objects.create(
        sheet=sheet, requester=viewer, release_at=now + timedelta(days=settings.FAMILY_EMERGENCY_DELAY_DAYS))
    _audit(viewer, 'CREATE', sheet.pk, {'request': request.pk})
    with translation.override(getattr(sheet.owner.profile, 'language', None) or settings.LANGUAGE_CODE):
        _notify(sheet.owner, _("%(name)s asked for your emergency sheet") % {'name': display_name(viewer)},
                _("If you do nothing, they get a read-only copy in %(days)d days. You can deny or approve now.")
                % {'days': settings.FAMILY_EMERGENCY_DELAY_DAYS})
    return request


def cancel(viewer, request):
    if request.requester_id != viewer.pk or request.status != EmergencyRequest.PENDING:
        raise FamilyError('not_allowed', _("That request cannot be cancelled."))
    request.status, request.decided_at = EmergencyRequest.CANCELLED, timezone.now()
    request.save(update_fields=['status', 'decided_at'])
    _audit(viewer, 'UPDATE', request.sheet_id, {'request': request.pk, 'status': 'CANCELLED'})


def decide(owner, request, approve):
    """The owner approves now or denies (or withdraws access that was given)."""
    if request.sheet.owner_id != owner.pk:
        raise FamilyError('not_owner', _("Only the owner of the sheet can answer."))
    if request.status not in (EmergencyRequest.PENDING, EmergencyRequest.APPROVED):
        raise FamilyError('closed', _("That request is already closed."))
    if approve and request.status == EmergencyRequest.APPROVED:
        return request
    request.status = EmergencyRequest.APPROVED if approve else EmergencyRequest.DENIED
    request.decided_at = timezone.now()
    request.save(update_fields=['status', 'decided_at'])
    _audit(owner, 'UPDATE', request.sheet_id, {'request': request.pk, 'status': request.status})
    with translation.override(getattr(request.requester.profile, 'language', None) or settings.LANGUAGE_CODE):
        if approve:
            _notify(request.requester, _("%(name)s approved your request") % {'name': display_name(owner)},
                    _("You can now open their emergency sheet. It is read-only."))
        else:
            _notify(request.requester, _("%(name)s has not shared their emergency sheet") % {'name': display_name(owner)},
                    _("Their sheet stays closed for now."))
    return request


def open_requests(sheet, now=None):
    """Pending requests still inside the delay, for the owner to answer."""
    now = now or timezone.now()
    return list(sheet.requests.filter(status=EmergencyRequest.PENDING, release_at__gt=now).select_related('requester'))


def people_with_access(sheet, now=None):
    """Requests that currently give someone the sheet (approved, or the delay ran out), in request mode."""
    now = now or timezone.now()
    out = []
    for request in sheet.requests.filter(status__in=[EmergencyRequest.APPROVED, EmergencyRequest.PENDING]) \
            .select_related('requester'):
        if request.status == EmergencyRequest.APPROVED or request.release_at <= now:
            if sheet.allowed.filter(user=request.requester).exists():
                out.append(request)
    return out


# ---- what the sheet says ----------------------------------------------------------------------------------------

def sheet_content(sheet):
    """The sheet as a reader sees it: only the accounts the owner put on it, grouped, with nominees and notes."""
    nominees = dict(sheet.items.values_list('account_id', 'nominee'))
    owned = summary.own_items(sheet.owner)
    by_section = {}
    for account_id, nominee in nominees.items():
        item = owned.get(account_id)
        if item is None:
            continue
        by_section.setdefault(item.section, []).append({'item': item, 'nominee': nominee})
    groups = [(key, label, sorted(by_section[key], key=lambda r: r['item'].name))
              for key, label, _kinds in catalog.SECTIONS if by_section.get(key)]
    return {'owner_name': display_name(sheet.owner), 'groups': groups, 'documents': sheet.documents_note,
            'contacts': sheet.contacts_note, 'updated_at': sheet.updated_at, 'count': len(nominees)}
