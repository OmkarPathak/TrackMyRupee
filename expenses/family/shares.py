"""An owner's choices about what to share: share, change, stop. Everything goes through here so the household's
cache version, the activity list and the optional "something new was shared" notice stay in step."""
from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext as _

from ..models import Account, FamilyShare, FamilyShareEvent, Notification
from . import households
from .presenters import display_name

NAME_MAX = 100
COVERS_MAX = 255


def shareable_accounts(user):
    return list(user.accounts.filter(is_active=True).select_related('linked_physical_asset', 'family_share')
                .order_by('name', 'id'))


def _clean_level(level):
    if level not in (FamilyShare.AMOUNTS, FamilyShare.LIST_ONLY):
        raise households.FamilyError('level', _("Choose what your family can see."))
    return level


def _log(user, kind, name, level=''):
    FamilyShareEvent.objects.create(owner=user, kind=kind, item_name=name[:NAME_MAX], level=level)


def _notify_members(household, owner, name):
    """Tell the other members, but only if ``owner`` switched on "tell family when I share something new"."""
    if not household.members.filter(user=owner, notify_new_shares=True).exists():
        return
    for membership in household.members.exclude(user=owner).select_related('user'):
        Notification.objects.create(
            user=membership.user, notification_type='SYSTEM', link=reverse('family-home'),
            title=_("%(name)s shared something new") % {'name': display_name(owner)},
            message=_("%(name)s is now shared with the family.") % {'name': name})


@transaction.atomic
def set_share(user, account, level, alias='', covers=None):
    """Share ``account`` (or change how it is shared). Returns (share, created)."""
    household = households.household_for(user)
    if household is None:
        raise households.FamilyError('not_member', _("You are not in a family."))
    if account.user_id != user.id or not account.is_active:
        raise households.FamilyError('not_yours', _("That account cannot be shared."))
    level = _clean_level(level)
    alias = ' '.join((alias or '').split())[:NAME_MAX]
    if alias == account.name:
        alias = ''

    policy = account.linked_physical_asset
    if covers is not None and policy is not None:
        policy.covers = ' '.join(covers.split())[:COVERS_MAX]
        policy.save(update_fields=['covers', 'updated_at'])

    share, created = FamilyShare.objects.get_or_create(
        account=account, defaults={'household': household, 'owner': user, 'level': level, 'alias': alias})
    shown_name = alias or account.name
    if created:
        _log(user, FamilyShareEvent.SHARED, shown_name, level)
        _notify_members(household, user, shown_name)
    elif (share.level, share.alias) != (level, alias):
        share.level, share.alias = level, alias
        share.save(update_fields=['level', 'alias', 'updated_at'])
        _log(user, FamilyShareEvent.CHANGED, shown_name, level)
    households.bump(household)
    return share, created


@transaction.atomic
def stop_share(user, account):
    """Stop sharing ``account``. Returns True if it was shared."""
    share = FamilyShare.objects.filter(owner=user, account=account).select_related('household').first()
    if share is None:
        return False
    name = share.alias or account.name
    household = share.household
    share.delete()
    _log(user, FamilyShareEvent.STOPPED, name)
    households.bump(household)
    return True


def recent_events(user, limit=8):
    return list(FamilyShareEvent.objects.filter(owner=user)[:limit])


def counts(user):
    """(shared, total) of the user's active accounts."""
    total = Account.objects.filter(user=user, is_active=True).count()
    shared = FamilyShare.objects.filter(owner=user, account__is_active=True).count()
    return shared, total
