"""Keep the family cache honest when an owner changes an account outside the family pages.

Sharing, stopping and member changes bump the household's cache version themselves. An account that is deleted or
deactivated (switched off) elsewhere must stop showing to the family at once too, not when the cache expires."""
from django.db.models import F
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from ..models import Account, FamilyShare, Household


def _bump_households_sharing(account_id):
    household_ids = list(FamilyShare.objects.filter(account_id=account_id).values_list('household_id', flat=True))
    if household_ids:
        Household.objects.filter(pk__in=household_ids).update(cache_version=F('cache_version') + 1)


@receiver(post_save, sender=Account, dispatch_uid='family_account_deactivated')
def account_saved(sender, instance, created, **kwargs):
    # Only a switched-off account can disappear for the family, so the common save (a new balance) costs nothing
    if not created and not instance.is_active:
        _bump_households_sharing(instance.pk)


@receiver(pre_delete, sender=Account, dispatch_uid='family_account_deleted')
def account_deleting(sender, instance, **kwargs):
    _bump_households_sharing(instance.pk)
