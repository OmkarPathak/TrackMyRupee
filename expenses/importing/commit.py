"""Writing imported expenses to the database.

Imports use bulk_create for speed, which skips everything `Expense.save()` and its signals do. This module does
the same work in bulk so an imported expense leaves exactly the traces a typed one does: the account balance, the
ledger entry, the audit-log row and the cleared caches. `test_import_parity` pins that down.
"""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction

from ..models import Account, Expense, FinancialAuditLog, Income


logger = logging.getLogger(__name__)


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


def save_income_batch(batch_items, *, account, account_rate, summary):
    """Create one batch of income rows atomically, as `Income.save()` would for each: the account is credited, the
    ledger entry and the audit-log row are written. If the batch trips a unique constraint, the rows are saved one
    at a time so a single clash costs only that row."""
    if not batch_items:
        return
    try:
        with transaction.atomic():
            created = Income.objects.bulk_create(batch_items)
            summary['income_count'] += len(created)
            summary['income_total'] += sum(float(i.amount) for i in created)

            if account:
                total = sum(
                    (i.amount * account_rate).quantize(Decimal('0.01')) if account_rate != Decimal('1.0') else i.amount
                    for i in created
                )
                locked = Account.objects.select_for_update().get(pk=account.id)
                locked.balance += total
                locked.save(update_fields=['balance', 'updated_at'])

            FinancialAuditLog.objects.bulk_create([
                FinancialAuditLog(
                    user=i.user, model_name='Income', object_id=i.id, object_uuid=i.uuid, action='CREATE',
                    diff={'before': None, 'after': {'amount': str(i.amount), 'source': i.source}},
                )
                for i in created
            ])

            if getattr(settings, 'LEDGER_WRITE_ENABLED', False) and account:
                _post_income_ledger_entries(created)
    except IntegrityError:
        for item in batch_items:
            try:
                with transaction.atomic():
                    item.save()
                    summary['income_count'] += 1
                    summary['income_total'] += float(item.amount)
            except IntegrityError:
                summary['duplicate_count'] += 1
            except Exception as error:
                _record_error(summary, getattr(item, '_row_idx', '?'), str(error))
    except Exception as error:
        for item in batch_items:
            _record_error(summary, getattr(item, '_row_idx', '?'), str(error))


def _post_income_ledger_entries(created):
    from ..ledger_service import LedgerPostingService
    from ..models import _build_ledger_version, _run_ledger_shadow

    try:
        with transaction.atomic():
            LedgerPostingService.bulk_post(created, kind='INCOME')
        return
    except Exception:
        logger.exception('Bulk ledger posting of imported income failed; posting it one by one.')

    for income in created:
        def post(i=income):
            LedgerPostingService.shadow_post_income_create(income=i, version_token=_build_ledger_version(i, 'CREATE'))

        _run_ledger_shadow(
            post,
            source_type='INCOME',
            source_id=income.id,
            action='CREATE',
            payload={
                'handler': 'income_create',
                'version_token': _build_ledger_version(income, 'CREATE'),
                'income': {
                    'user_id': income.user_id,
                    'amount': str(income.amount),
                    'currency': income.currency,
                    'source': income.source,
                    'description': income.description,
                    'account_id': income.account_id,
                    'source_id': income.id,
                },
                'previous_income': None,
            },
        )


def _record_error(summary, row, reason):
    if len(summary['errors']) < 15:
        summary['errors'].append({'row': row, 'reason': reason})
    summary['error_count'] += 1


def _post_ledger_entries(created):
    from ..ledger_service import LedgerPostingService
    from ..models import _build_ledger_version, _run_ledger_shadow

    # One bulk posting for the whole batch (about 9 queries a row became a handful per batch). If it fails for any
    # reason, fall back to posting row by row, where a failure is recorded per row for the retry job as before.
    try:
        with transaction.atomic():
            LedgerPostingService.bulk_post(created, kind='EXPENSE')
        return
    except Exception:
        logger.exception('Bulk ledger posting of imported expenses failed; posting them one by one.')

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
