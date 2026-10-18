"""Writing imported expenses to the database.

Imports use bulk_create for speed, which skips everything `Expense.save()` and its signals do. This module does
the same work in bulk so an imported expense leaves exactly the traces a typed one does: the account balance, the
ledger entry, the audit-log row and the cleared caches. `test_import_parity` pins that down.
"""
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

from ..models import Account, Expense, FinancialAuditLog


def save_batch(batch_items, *, account, account_rate, summary):
    """Create one batch of expenses atomically and update `summary`. If the batch trips a unique constraint, the
    rows are saved one at a time so a single clash costs only that row."""
    if not batch_items:
        return
    try:
        with transaction.atomic():
            created = Expense.objects.bulk_create(batch_items)
            summary['created_count'] += len(created)
            summary['total_amount'] += sum(float(e.amount) for e in created)

            if account:
                total_deduct = sum(
                    (e.amount * account_rate).quantize(Decimal('0.01')) if account_rate != Decimal('1.0') else e.amount
                    for e in created
                )
                locked = Account.objects.select_for_update().get(pk=account.id)
                locked.balance -= total_deduct
                locked.save(update_fields=['balance', 'updated_at'])

            FinancialAuditLog.objects.bulk_create([
                FinancialAuditLog(
                    user=e.user, model_name='Expense', object_id=e.id, object_uuid=e.uuid, action='CREATE',
                    diff={'before': None, 'after': {'amount': str(e.amount), 'category': e.category}},
                )
                for e in created
            ])

            if getattr(settings, 'LEDGER_WRITE_ENABLED', False) and account:
                _post_ledger_entries(created)
    except IntegrityError:
        for item in batch_items:
            try:
                with transaction.atomic():
                    item.save()
                    summary['created_count'] += 1
                    summary['total_amount'] += float(item.amount)
            except IntegrityError:
                summary['duplicate_count'] += 1
            except Exception as error:
                _record_error(summary, getattr(item, '_row_idx', '?'), str(error))
    except Exception as error:
        for item in batch_items:
            _record_error(summary, getattr(item, '_row_idx', '?'), str(error))


def _record_error(summary, row, reason):
    if len(summary['errors']) < 15:
        summary['errors'].append({'row': row, 'reason': reason})
    summary['error_count'] += 1


def _post_ledger_entries(created):
    from ..ledger_service import LedgerPostingService
    from ..models import _build_ledger_version, _run_ledger_shadow

    for expense in created:
        def post(e=expense):
            LedgerPostingService.shadow_post_expense_create(expense=e, version_token=_build_ledger_version(e, 'CREATE'))

        _run_ledger_shadow(
            post,
            source_type='EXPENSE',
            source_id=expense.id,
            action='CREATE',
            payload={
                'handler': 'expense_create',
                'version_token': _build_ledger_version(expense, 'CREATE'),
                'expense': {
                    'user_id': expense.user_id,
                    'amount': str(expense.amount),
                    'currency': expense.currency,
                    'category': expense.category,
                    'description': expense.description,
                    'account_id': expense.account_id,
                    'source_id': expense.id,
                },
                'previous_expense': None,
            },
        )


def clear_caches_after_import(user_id):
    """bulk_create sends no signals, so everything a saved expense would clear is cleared here."""
    from ..signals import invalidate_dashboard_cache

    cache.delete_many([
        f'filter_merchants:{user_id}',
        f'filter_categories:{user_id}',
        f'interest_summary_{user_id}_None_None',
    ])
    invalidate_dashboard_cache(user_id=user_id)
