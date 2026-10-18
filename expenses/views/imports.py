"""Import preview, history, undo and the download of skipped rows. The upload itself is `misc.upload_view`."""
import csv
import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from ..importing import ImportFileError, ImportOptions, TooManyRows, analyse
from ..importing.layout import ROLES, column_label
from ..importing.service import CREDIT_ROW, DUPLICATE, ERROR, EXCLUDED, LIMIT, NEW
from ..importing.undo import UndoBusy, remaining_expenses, undo_batch
from ..models import Account, ImportBatch

__all__ = ['upload_preview_view', 'import_history_view', 'import_undo_view', 'import_report_view']

MAX_PREVIEW_ROWS = 1000
_ROW_ID = re.compile(r'^\d{1,3}-\d{1,7}$')
_MAPPED_ROLES = ('date', 'description', 'amount', 'debit', 'credit', 'balance', 'category', 'reference')


def options_from_post(post):
    """What the person chose in the preview, as ImportOptions, plus the rows they unticked."""
    columns = {}
    for role in _MAPPED_ROLES:
        value = (post.get(f'map_{role}') or '').strip()
        if value.lstrip('-').isdigit() and -1 <= int(value) < 200:
            columns[role] = int(value)
    sign = post.get('sign_mode') if post.get('sign_mode') in ('positive', 'negative') else 'auto'
    order = post.get('date_order') if post.get('date_order') in ('day', 'month') else 'auto'
    shown = {s for s in (post.get('shown') or '').split(',') if _ROW_ID.match(s)}
    included = {s for s in post.getlist('include') if _ROW_ID.match(s)}
    excluded = shown - included if shown else set()
    return ImportOptions(columns=columns, sign_mode=sign, date_order=order), excluded


def _column_roles(layout, options):
    """For the preview's column pickers: each role, which column it was detected in, and every column to pick."""
    labels = [(i, column_label(layout, i)) for i in range(max(layout.width, max(layout.columns.values(), default=-1) + 1))]
    names = {
        'date': _('Date'), 'description': _('Description'), 'amount': _('Amount'),
        'debit': _('Debit (money out)'), 'credit': _('Credit (money in)'), 'balance': _('Balance'),
        'category': _('Category'), 'reference': _('Reference'),
    }
    roles = []
    for role in _MAPPED_ROLES:
        detected = layout.columns.get(role)
        roles.append({'role': role, 'name': names[role], 'detected': detected, 'chosen': options.columns.get(role),
                      'detected_label': column_label(layout, detected) if detected is not None else _('none')})
    return roles, labels


@login_required
@require_POST
def upload_preview_view(request):
    """Read the uploaded file and show what an import would do, without saving anything."""
    from finance_tracker.plans import get_limit as _plan_limit
    from . import predict_category_ai
    from .misc import MAX_IMPORT_ROWS, _IMPORT_NOTES, read_upload

    def panel(**context):
        return render(request, 'expenses/partials/_import_preview.html', context)

    upload = read_upload(request)
    if upload.error:
        return panel(error=upload.error)

    options, excluded = options_from_post(request.POST)
    lock = f'expense_import_lock_{request.user.id}'
    if cache.get(lock):
        return panel(error=_("An import is already running. Wait for it to finish, then upload again."))
    try:
        analysis = analyse(
            user=request.user, uploaded_file=upload.file, file_name=upload.file_name, account=upload.account,
            currency=upload.currency, predict_category=predict_category_ai,
            plan_cap=_plan_limit(request.user.profile.active_tier, 'expenses_per_month'),
            max_rows=MAX_IMPORT_ROWS, options=options, excluded=excluded)
    except ImportFileError as error:
        return panel(error=str(error))
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Import preview failed for user %s", request.user.id)
        return panel(error=_("We could not read this file. Check that it is a valid Excel or CSV file with Date, "
                             "Amount and Description columns, then try again."))
    if analysis is None:
        return panel(error=_("Could not detect required columns (Date, Amount, Description). Please check your file."))

    layout = analysis.sheets[0].layout
    roles, column_choices = _column_roles(layout, options)
    decisions = analysis.decisions
    shown = decisions[:MAX_PREVIEW_ROWS]
    return panel(
        analysis=analysis, upload=upload, rows=shown, truncated=len(decisions) - len(shown),
        shown_ids=','.join(d.row_id for d in shown),
        counts={'new': analysis.count(NEW), 'duplicate': analysis.count(DUPLICATE), 'credit': analysis.count(CREDIT_ROW),
                'error': analysis.count(ERROR), 'limit': analysis.count(LIMIT), 'excluded': analysis.count(EXCLUDED)},
        notes=[str(_IMPORT_NOTES[n]) for n in analysis.notes],
        roles=roles, column_choices=column_choices, options=options,
        single_amount_column='amount' in layout.columns and 'debit' not in layout.columns and 'credit' not in layout.columns,
        sample=analysis.sheets[0].sample, header=layout.header,
    )


@login_required
def import_history_view(request):
    batches = ImportBatch.objects.filter(user=request.user).select_related('account')[:100]
    rows = []
    for batch in batches:
        live = remaining_expenses(batch) if batch.status != ImportBatch.UNDONE else 0
        rows.append({'batch': batch, 'live': live})
    resume = next((r['batch'] for r in rows if r['batch'].status == ImportBatch.UNDOING), None)
    return render(request, 'expenses/import_history.html', {'rows': rows, 'resume': resume})


@login_required
@require_POST
def import_undo_view(request, uuid):
    batch = get_object_or_404(ImportBatch, uuid=uuid, user=request.user)
    if batch.status == ImportBatch.UNDONE:
        messages.info(request, _("This import has already been undone."))
        return redirect('import-history')
    try:
        deleted, left = undo_batch(batch)
    except UndoBusy:
        messages.info(request, _("This import is already being undone. Give it a moment."))
        return redirect('import-history')
    if left:
        messages.info(request, _("Undoing this import: %(left)s expenses to go. This page continues by itself.") % {'left': left})
    else:
        messages.success(request, _("The import was undone: its expenses were removed and your balances restored."))
    return redirect('import-history')


@login_required
def import_report_view(request, uuid):
    """The rows an import did not bring in, and why, as a CSV."""
    batch = get_object_or_404(ImportBatch, uuid=uuid, user=request.user)
    if not batch.report:
        raise Http404
    status_names = {ERROR: _('Error'), DUPLICATE: _('Duplicate'), CREDIT_ROW: _('Money in'), LIMIT: _('Over plan limit')}
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="skipped-rows-{batch.created_at:%Y%m%d-%H%M}.csv"'
    response.write('﻿')                          # so Excel opens it as UTF-8
    writer = csv.writer(response)
    writer.writerow([_('Row'), _('Date'), _('Description'), _('Amount'), _('Status'), _('Reason')])
    for item in batch.report:
        writer.writerow([item.get('row'), item.get('date'), _csv_safe(item.get('description', '')), item.get('amount'),
                         status_names.get(item.get('status'), item.get('status')), item.get('reason')])
    return response


def _csv_safe(text):
    """A cell that starts with = + - @ is read by a spreadsheet as a formula; a quote in front stops that."""
    text = str(text)
    return "'" + text if text[:1] in ('=', '+', '-', '@') else text
