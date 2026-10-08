"""JSON endpoints behind the New Expense composer (parse, bootstrap data, save, undo, events).

Nothing here logs or emits amounts, descriptions or account names.
"""
import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from ..composer import (
    UNDO_WINDOW,
    ComposerError,
    account_info,
    build_payload,
    display_amount,
    fetch_hints,
    usual_cache_key,
    visible_accounts,
    visible_categories,
)
from ..models import Account, Expense
from ..parser import parse_expense_nl
from ..posthog_utils import ph_capture
from .utils import get_object_by_uuid_or_pk

MAX_PARSE_CHARS = 300

COMPOSER_FIELDS = {'amount', 'currency', 'date', 'description', 'category', 'account', 'payment_method'}
COMPOSER_ENTRIES = {
    'fab', 'shortcut_key', 'sidebar', 'navbar', 'mobile_sheet',
    'deep_link', 'pwa_shortcut', 'empty_state', 'other',
}
COMPOSER_LANGS = {'en', 'hi', 'mr'}


def _clamp_int(value, low, high):
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return low


def _json_body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


@require_GET
@login_required
@never_cache
def expense_composer_data(request):
    """Everything the composer needs on open: categories, accounts, defaults, usuals."""
    return JsonResponse({'success': True, 'data': build_payload(request.user)})


@require_POST
@login_required
def parse_expense_view(request):
    """Dry-run parse of one line of text.  Saves nothing; a handful of indexed queries."""
    body = _json_body(request)
    if body is None:
        return JsonResponse({'success': False, 'error': _('Unable to parse expense right now.')}, status=400)
    text = str(body.get('text') or '')[:MAX_PARSE_CHARS]
    if not text.strip():
        return JsonResponse({'success': False, 'error': _('No input text provided.')})

    try:
        user = request.user
        accounts = visible_accounts(user)
        infos = account_info(accounts)
        categories = visible_categories(user)
        hints = fetch_hints(user, text)

        default_account = None
        default_id = body.get('default_account_id')
        if default_id not in (None, ''):
            default_account = next((a for a in infos if str(a['id']) == str(default_id)), None)

        result = parse_expense_nl(
            text,
            user_categories=categories,
            account_info=infos,
            hints=hints,
            default_currency=user.profile.currency,
            default_account=default_account,
            skip_genai=True,
        )
    except Exception:
        return JsonResponse({'success': False, 'error': _('Unable to parse expense right now.')}, status=400)

    if not result:
        return JsonResponse({'success': False, 'error': _('No input text provided.')})

    # Only offer a category the user actually has; anything else is a guess the user must confirm.
    canonical = {c.lower(): c for c in categories}
    matched = canonical.get((result.get('category') or '').lower())
    if matched:
        result['category'] = matched
    else:
        result['category'] = None
        result['confidence']['category'] = 'low'
        if 'category' not in result['check']:
            result['check'].append('category')
    return JsonResponse({'success': True, 'data': result})


def _fresh_account_label(account_id):
    if not account_id:
        return None
    acc = Account.objects.filter(pk=account_id).first()
    return str(acc) if acc else None


@require_POST
@login_required
def expense_composer_save(request):
    """Create exactly one expense.  Idempotent on ``key`` so a double tap cannot double-post."""
    from ..composer import save_expense

    body = _json_body(request)
    if body is None:
        return JsonResponse({'success': False, 'error': _('Unable to save expense right now.')}, status=400)

    try:
        expense, created = save_expense(request.user, body)
    except ComposerError as exc:
        if exc.code == 'limit':
            return JsonResponse({
                'success': False,
                'code': 'limit',
                'error': _('You have reached the monthly limit of %(limit)s expenses for your current plan. Please upgrade to add more.')
                % {'limit': _monthly_limit(request.user)},
                'upgrade_url': reverse('pricing'),
            }, status=403)
        messages = {
            'invalid': _('Please check the highlighted fields.'),
            'currency': _('Unable to save expense right now because currency conversion failed or data is invalid.'),
            'duplicate': _('Duplicate record found! You already have this expense recorded for this date.'),
        }
        return JsonResponse({
            'success': False,
            'code': exc.code,
            'error': messages.get(exc.code, _('Unable to save expense right now.')),
            'field_errors': exc.field_errors,
        }, status=exc.status)

    if created:
        meta = body.get('meta') if isinstance(body.get('meta'), dict) else {}
        edited = [f for f in (meta.get('edited') or []) if f in COMPOSER_FIELDS]
        duration = meta.get('duration_ms')
        props = {
            'mode': 'quick' if meta.get('mode') == 'quick' else 'manual',
            'edited_before_add': bool(edited),
            'edited_count': len(edited),
            'add_another': bool(meta.get('add_another')),
            'currency': expense.currency,
            'has_account': bool(expense.account_id),
            'payment_method': expense.payment_method,
        }
        if isinstance(duration, (int, float)) and 0 <= duration < 3600000:
            props['duration_ms'] = int(duration)
        ph_capture(request.user, 'expense_added', props)

    return JsonResponse({
        'success': True,
        'duplicate': not created,
        'expense': {
            'id': str(expense.uuid),
            'date': expense.date.isoformat(),
            'amount_display': display_amount(expense),
            'category': expense.category,
            'account': expense.account.name if expense.account_id else '',
            'currency': expense.currency,
        },
        'account_id': expense.account_id,
        'account_label': _fresh_account_label(expense.account_id),
        'undo_url': reverse('expense-composer-undo', args=[expense.uuid]),
        'edit_url': reverse('expense-edit', args=[expense.uuid]),
    })


def _monthly_limit(user):
    from finance_tracker.plans import get_limit
    return get_limit(user.profile.active_tier, 'expenses_per_month')


@require_POST
@login_required
def expense_composer_undo(request, pk):
    """Take back an expense that was just added (restores balances through Expense.delete)."""
    from django.core.cache import cache

    expense = get_object_by_uuid_or_pk(Expense, pk, user=request.user)
    if timezone.now() - expense.created_at > UNDO_WINDOW:
        return JsonResponse({
            'success': False,
            'error': _('This expense is too old to undo. Delete it from the expense list instead.'),
        }, status=409)
    account_id = expense.account_id
    expense.delete()
    cache.delete(usual_cache_key(request.user.id))
    ph_capture(request.user, 'expense_undone', {})
    return JsonResponse({
        'success': True,
        'account_id': account_id,
        'account_label': _fresh_account_label(account_id),
    })


@require_POST
@login_required
def expense_composer_event(request):
    """Allow-listed, payload-sanitised analytics events from the composer UI."""
    body = _json_body(request)
    if body is None:
        return JsonResponse({'success': False}, status=400)
    name = body.get('event')
    raw = body.get('props') if isinstance(body.get('props'), dict) else {}

    props = {}
    if name == 'expense_form_opened':
        props['entry'] = raw.get('entry') if raw.get('entry') in COMPOSER_ENTRIES else 'other'
    elif name == 'quick_add_parsed':
        props['success'] = bool(raw.get('success'))
        props['fields_filled'] = _clamp_int(raw.get('fields_filled'), 0, 7)
        props['check_count'] = _clamp_int(raw.get('check_count'), 0, 7)
    elif name == 'expense_field_edited':
        field = raw.get('field')
        if field not in COMPOSER_FIELDS:
            return JsonResponse({'success': False}, status=400)
        props['field'] = field
    elif name == 'voice_used':
        props['lang'] = raw.get('lang') if raw.get('lang') in COMPOSER_LANGS else 'en'
    elif name == 'your_usual_chip_used':
        pass
    else:
        return JsonResponse({'success': False}, status=400)

    ph_capture(request.user, name, props)
    return JsonResponse({'success': True})
