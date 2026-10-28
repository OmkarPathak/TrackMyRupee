"""Households and invitations: creating a family, inviting, joining, leaving, removing."""
import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext as _

from expenses.models import (
    ConsentEvent,
    FinancialAuditLog,
    Household,
    HouseholdInvitation,
    HouseholdMember,
)

CONSENT_PURPOSE = 'family_sharing'
CONSENT_VERSION = '1.0'


class FamilyError(Exception):
    """Something a person did cannot be done; the message is ready to show them."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def household_for(user):
    """The household ``user`` belongs to, or None."""
    if user is None or not user.is_authenticated:
        return None
    membership = HouseholdMember.objects.select_related('household').filter(user=user).first()
    return membership.household if membership else None


def bump(household):
    """Invalidate every cached family value for this household."""
    Household.objects.filter(pk=household.pk).update(cache_version=F('cache_version') + 1)
    household.cache_version = (household.cache_version or 0) + 1


def _audit(user, action, obj_name, obj_id, diff):
    FinancialAuditLog.objects.create(user=user, model_name=obj_name, object_id=obj_id, action=action, diff=diff)


def seats_used(household):
    open_invites = household.invitations.filter(
        accepted_at__isnull=True, cancelled_at__isnull=True, expires_at__gt=timezone.now()).count()
    return household.members.count(), open_invites


def create_household(user, name=''):
    if household_for(user) is not None:
        raise FamilyError('already_member', _("You are already in a family."))
    if not user.profile.is_pro:
        raise FamilyError('needs_pro', _("Family sharing needs a Pro plan."))
    with transaction.atomic():
        household = Household.objects.create(owner=user, name=(name or '').strip()[:100] or _('Our family'))
        member = HouseholdMember.objects.create(household=household, user=user)
        _audit(user, 'CREATE', 'Household', household.id, {'member': member.id})
    return household


def invite(household, invited_by, email=''):
    """A new open invitation. Returns (invitation, raw token); the raw token exists only now."""
    members, open_invites = seats_used(household)
    if members + open_invites >= settings.FAMILY_MAX_MEMBERS:
        raise FamilyError('full', _("Your family is full. Remove a member or cancel an invitation first."))
    token = secrets.token_urlsafe(32)
    invitation = HouseholdInvitation.objects.create(
        household=household,
        email=(email or '').strip().lower(),
        token_hash=hash_token(token),
        invited_by=invited_by,
        expires_at=timezone.now() + timedelta(days=settings.FAMILY_INVITE_DAYS),
    )
    _audit(invited_by, 'CREATE', 'HouseholdInvitation', invitation.id, {'email': bool(invitation.email)})
    return invitation, token


def renew_invitation(invitation, by_user):
    """Cancel an open invitation and issue a fresh link for the same address."""
    if invitation.accepted_at is not None:
        raise FamilyError('used', _("That invitation has already been used."))
    with transaction.atomic():
        invitation.cancelled_at = timezone.now()
        invitation.save(update_fields=['cancelled_at'])
        return invite(invitation.household, by_user, invitation.email)


def cancel_invitation(invitation, by_user):
    if invitation.accepted_at is None and invitation.cancelled_at is None:
        invitation.cancelled_at = timezone.now()
        invitation.save(update_fields=['cancelled_at'])
        _audit(by_user, 'DELETE', 'HouseholdInvitation', invitation.id, {})


def find_invitation(token):
    if not token:
        return None
    return (HouseholdInvitation.objects.select_related('household', 'invited_by')
            .filter(token_hash=hash_token(token)).first())


def invitation_problem(invitation):
    """Why an invitation cannot be used, as a short code, or None when it can."""
    if invitation is None:
        return 'unknown'
    if invitation.accepted_at is not None:
        return 'used'
    if invitation.cancelled_at is not None:
        return 'cancelled'
    if invitation.expires_at <= timezone.now():
        return 'expired'
    return None


def accept_invitation(invitation, user, request=None):
    """Put ``user`` in the invitation's household and record their consent."""
    problem = invitation_problem(invitation)
    if problem:
        raise FamilyError(problem, _("This invitation can no longer be used."))
    if household_for(user) is not None:
        raise FamilyError('already_member', _("You are already in a family. Leave it first to join another."))
    if not user.profile.is_pro:
        raise FamilyError('needs_pro', _("To join a family you need a Pro plan, and so does everyone in it."))
    household = invitation.household
    if household.members.count() >= settings.FAMILY_MAX_MEMBERS:
        raise FamilyError('full', _("This family is full."))
    try:
        with transaction.atomic():
            member = HouseholdMember.objects.create(household=household, user=user)
            invitation.accepted_at = timezone.now()
            invitation.accepted_by = user
            invitation.save(update_fields=['accepted_at', 'accepted_by'])
            ConsentEvent.objects.create(
                user=user, action='GRANTED', purpose=CONSENT_PURPOSE, consent_version=CONSENT_VERSION,
                ip_address=_client_ip(request), user_agent=(request.META.get('HTTP_USER_AGENT', '')[:500] if request else None))
            _audit(user, 'CREATE', 'HouseholdMember', member.id, {'household': household.id})
            bump(household)
    except IntegrityError as exc:
        raise FamilyError('already_member', _("You are already in a family.")) from exc
    return household


def _client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() or request.META.get('REMOTE_ADDR')) or None


def leave(user):
    """``user`` leaves their family. If the owner leaves, the longest-standing member takes over, or the
    family ends when nobody is left. Everything shared stops being visible straight away."""
    membership = HouseholdMember.objects.select_related('household').filter(user=user).first()
    if membership is None:
        raise FamilyError('not_member', _("You are not in a family."))
    household = membership.household
    membership_id = membership.id
    with transaction.atomic():
        membership.delete()
        ConsentEvent.objects.create(user=user, action='WITHDRAWN', purpose=CONSENT_PURPOSE,
                                    consent_version=CONSENT_VERSION)
        remaining = household.members.order_by('joined_at', 'id').first()
        if remaining is None:
            household.delete()
            return None
        if household.owner_id == user.id:
            household.owner = remaining.user
            household.save(update_fields=['owner'])
        bump(household)
        _audit(user, 'DELETE', 'HouseholdMember', membership_id, {'household': household.id})
    return household


def remove_member(owner, membership):
    household = membership.household
    if household.owner_id != owner.id:
        raise FamilyError('not_owner', _("Only the person who started the family can remove someone."))
    if membership.user_id == owner.id:
        raise FamilyError('is_owner', _("You cannot remove yourself. Leave the family instead."))
    with transaction.atomic():
        removed_user = membership.user
        membership_id = membership.id
        membership.delete()
        ConsentEvent.objects.create(user=removed_user, action='WITHDRAWN', purpose=CONSENT_PURPOSE,
                                    consent_version=CONSENT_VERSION)
        bump(household)
        _audit(owner, 'DELETE', 'HouseholdMember', membership_id, {'removed_user': removed_user.id})
