"""The invitation email. The recipient may not have an account yet, so it is plain English and names only
the person who invited them."""
import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def send_invitation_email(invitation, link):
    """Email ``link`` to the invitation's address. Returns True when it was handed to the mail backend."""
    if not invitation.email:
        return False
    inviter = invitation.invited_by
    context = {
        'inviter_name': inviter.get_full_name() or inviter.username,
        'household_name': invitation.household.name,
        'link': link,
        'days': max(1, (invitation.expires_at - invitation.created_at).days),
    }
    try:
        send_mail(
            subject=f"{context['inviter_name']} invited you to share finances on TrackMyRupee",
            message=render_to_string('email/family_invite.txt', context),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[invitation.email],
            html_message=render_to_string('email/family_invite.html', context),
        )
        return True
    except Exception:
        logger.exception('Family invitation email failed for invitation %s', invitation.pk)
        return False
