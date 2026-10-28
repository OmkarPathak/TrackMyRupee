"""Shared set-up for the family sharing tests."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from expenses.family import households
from expenses.models import Account

D = Decimal


def make_user(username, *, pro=True, days=365, lifetime=False, currency='₹'):
    """A user with a profile. ``pro`` makes them Pro for ``days`` more days (or for ever with ``lifetime``)."""
    user = User.objects.create_user(username=username, password='password', email=f'{username}@example.com')
    profile = user.profile
    profile.currency = currency
    profile.has_seen_tutorial = True
    profile.consent_granted = True
    profile.consent_timestamp = timezone.now()
    profile.consent_version = '1.0'
    if pro:
        profile.tier = 'PRO'
        profile.is_lifetime = lifetime
        profile.subscription_end_date = None if lifetime else timezone.now() + timedelta(days=days)
    profile.save()
    return user


def end_pro(user, *, days_from_now=-1):
    """Move the end of ``user``'s Pro plan (negative: it ended that long ago)."""
    profile = user.profile
    profile.tier = 'PRO'
    profile.is_lifetime = False
    profile.subscription_end_date = timezone.now() + timedelta(days=days_from_now)
    profile.save()


def make_family(owner, *members):
    """A household owned by ``owner`` with ``members`` already in it. Returns the household."""
    household = households.create_household(owner)
    for member in members:
        invitation, token = households.invite(household, owner)
        households.accept_invitation(invitation, member)
    return household


def add_account(user, name, account_type='SAVINGS_ACCOUNT', balance='1000.00', **extra):
    return Account.objects.create(user=user, name=name, account_type=account_type, balance=D(balance),
                                  currency=extra.pop('currency', '₹'), **extra)


@override_settings(FAMILY_ENABLED=True)
class FamilyTestCase(TestCase):
    """Family sharing switched on, with an owner and two possible members."""

    def setUp(self):
        self.owner = make_user('arjun')
        self.partner = make_user('priya')
        self.parent = make_user('meera')
        mail.outbox.clear()     # creating a user sends a welcome email; the tests look at the family ones
