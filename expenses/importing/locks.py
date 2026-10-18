"""One import at a time per person, held in the database.

It used to be a key in the worker's memory cache, which a restart or a second worker process silently dropped. A
row in `ImportLock` holds across both, and one older than the time an import is allowed to take is treated as
abandoned, so a crashed request can never lock someone out for good.
"""
from contextlib import contextmanager
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from ..models import ImportLock

LOCK_TTL = timedelta(minutes=5)         # well over the time one request may spend importing


class ImportBusy(Exception):
    """An import for this person is already running."""


def _fresh(user):
    return ImportLock.objects.filter(user=user, acquired_at__gte=timezone.now() - LOCK_TTL)


def is_locked(user) -> bool:
    return _fresh(user).exists()


@contextmanager
def import_lock(user):
    """Hold the person's import lock for the length of the block; raises ImportBusy if someone else holds it."""
    with transaction.atomic():
        ImportLock.objects.filter(user=user, acquired_at__lt=timezone.now() - LOCK_TTL).delete()
        try:
            with transaction.atomic():
                lock = ImportLock.objects.create(user=user)
        except IntegrityError:
            raise ImportBusy()
    try:
        yield
    finally:
        ImportLock.objects.filter(pk=lock.pk).delete()
