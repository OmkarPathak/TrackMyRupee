"""Undoing an import: every expense it created is deleted the normal way (`Expense.delete()`), so the account
balance, the ledger, the audit log and the caches are reversed exactly as if each had been deleted by hand.

That is several queries per expense, so a big import cannot be undone inside one request. Undo works for a time
budget at a time and says how many are left; the page keeps asking until none are.
"""
import time

from django.core.cache import cache
from django.utils import timezone

from ..models import ImportBatch
from .commit import clear_caches_after_import

UNDO_BUDGET_SECONDS = 20
_LOCK_SECONDS = 90


class UndoBusy(Exception):
    """Another request is already undoing this import."""


def remaining_items(batch):
    """What an undo still has to remove: the import's expenses and the transfers it recorded."""
    return (batch.expenses.filter(is_deleted=False).count()
            + batch.transfers.filter(is_deleted=False).count())


remaining_expenses = remaining_items        # the name the history page used before transfers were imported


def undo_batch(batch, budget_seconds=UNDO_BUDGET_SECONDS):
    """Delete this import's expenses for up to `budget_seconds`. Returns (deleted now, still to delete)."""
    lock = f'import_undo_lock_{batch.pk}'
    if not cache.add(lock, 1, timeout=_LOCK_SECONDS):
        raise UndoBusy()
    deleted = 0
    try:
        if batch.status == ImportBatch.UNDONE:
            return 0, 0
        if batch.status != ImportBatch.UNDOING:
            batch.status = ImportBatch.UNDOING
            batch.save(update_fields=['status'])
        deadline = time.monotonic() + budget_seconds
        transfers = batch.transfers.filter(is_deleted=False).select_related('from_account', 'to_account', 'user')
        expenses = batch.expenses.filter(is_deleted=False).select_related('account', 'user').order_by('id')
        done = False
        for queryset in (transfers.order_by('id'), expenses):
            for item in queryset.iterator(chunk_size=100):
                item.delete()
                deleted += 1
                if time.monotonic() >= deadline:    # always at least one, so a call can never stall
                    done = True
                    break
            if done:
                break
        left = remaining_items(batch)
        if left == 0:
            batch.status = ImportBatch.UNDONE
            batch.undone_at = timezone.now()
            batch.save(update_fields=['status', 'undone_at'])
        if deleted:
            clear_caches_after_import(batch.user_id)
        return deleted, left
    finally:
        cache.delete(lock)
