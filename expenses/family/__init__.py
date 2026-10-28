"""Family sharing: households, invitations, the Pro rule and (from Phase 2) shared accounts.

See docs/FAMILY_SHARING.md. The rules in short: nothing is shared until its owner shares it, only accounts
(never transactions) are shared, and a fixed set of fields is all a family member ever sees.
"""
from django.conf import settings


def family_enabled_for(user):
    """Whether family sharing is switched on for ``user``. Settings only: no queries."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(settings, 'FAMILY_ENABLED', False):
        return True
    return user.pk in getattr(settings, 'FAMILY_USER_IDS', ())
