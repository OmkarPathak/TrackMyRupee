"""Telling people when something about their family changes without them doing it: the family was deleted, they were
removed, or they were made the owner. Each person gets an in-app notification and a short plain email, in their own
language. Nothing here reveals anything about the other person's accounts."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse
from django.utils import translation
from django.utils.translation import gettext as _

from ..models import Notification
from .presenters import display_name

logger = logging.getLogger(__name__)


def _tell(user, build):
    """Send ``build()`` -> (title, message) to ``user`` in their own language. Never raises: the change that
    prompted the notice has already happened and must not be undone by a failed email."""
    language = getattr(getattr(user, 'profile', None), 'language', None) or settings.LANGUAGE_CODE
    try:
        with translation.override(language):
            title, message = build()
            link = reverse('family-home')
        Notification.objects.create(user=user, title=title[:255], message=message, link=link, notification_type='SYSTEM')
    except Exception:
        logger.exception('Family notice failed for user %s', user.pk)
        return
    if user.email:
        try:
            send_mail(subject=title, message=f"{message}\n", from_email=settings.DEFAULT_FROM_EMAIL,
                      recipient_list=[user.email])
        except Exception:
            logger.exception('Family notice email failed for user %s', user.pk)


def family_deleted(users, owner, household_name):
    """Tell every former member (not the owner, who did it) that the family no longer exists."""
    owner_name = display_name(owner)
    for user in users:
        if user.pk == owner.pk:
            continue
        _tell(user, lambda: (
            _("%(name)s deleted the family") % {'name': owner_name},
            _("The family \"%(family)s\" no longer exists. Nothing is shared any more, and you no longer see anything "
              "shared with you. Your own data is untouched.") % {'family': household_name}))


def member_removed(user, owner, household_name):
    owner_name = display_name(owner)
    _tell(user, lambda: (
        _("You were removed from the family"),
        _("%(name)s removed you from the family \"%(family)s\". Nothing of yours is shared any more, and you no longer "
          "see anything shared with you. Your own data is untouched.") % {'name': owner_name, 'family': household_name}))


def ownership_given(user, previous_owner, household_name):
    previous_name = display_name(previous_owner)
    _tell(user, lambda: (
        _("You are now the owner of the family"),
        _("%(name)s made you the owner of the family \"%(family)s\". You can now invite people, remove members "
          "and delete the family.") % {'name': previous_name, 'family': household_name}))


def ownership_handed_on(user, previous_owner, household_name):
    """The owner left and the family passed to ``user``, the longest-standing member."""
    previous_name = display_name(previous_owner)
    _tell(user, lambda: (
        _("You are now the owner of the family"),
        _("%(name)s left the family \"%(family)s\", so you are now its owner. You can invite people, remove members "
          "and delete the family.") % {'name': previous_name, 'family': household_name}))
