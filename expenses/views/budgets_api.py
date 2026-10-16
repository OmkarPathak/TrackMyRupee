"""JSON API for the redesigned Budget page.

  GET  api/budgets/?month=YYYY-MM[&sort=urgent|name|limit|spent]   the month's page payload
  PUT  api/budgets/limit/      {category_id, month, amount|null, scope: this_month|every_month}
  POST api/budgets/rebalance/  {month, scope, moves: [{from_category, to_category, amount}]}
  POST api/budgets/reset/      {category_id, month}   drop a one-month override
  POST api/budgets/suggestion/dismiss/  {key}   stop showing a suggestion to this user

Every write returns the updated page payload for the month. Errors are
{"success": false, "error": <translated message>, "code": <stable code>}. "Today" and month
boundaries use the user's timezone (IST by default).
"""

import json
import re
from datetime import date
from functools import wraps

from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .. import budget_writes as writes
from ..budget_page import build_budget_page
from ..models import Category
from .utils import get_object_by_uuid_or_pk

MONTH_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')
SORTS = ('urgent', 'name', 'limit', 'spent')


def _error(code, message, status=400):
    return JsonResponse({'success': False, 'error': str(message), 'code': code}, status=status)


def api_login_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error('auth_required', _('Please sign in.'), 401)
        return view(request, *args, **kwargs)
    return wrapper


def _parse_month(value, today):
    """'YYYY-MM' -> first of that month; missing -> this month. Raises ValueError."""
    if value in (None, ''):
        return today.replace(day=1)
    match = MONTH_RE.match(str(value))
    if not match or not 2000 <= int(match.group(1)) <= 2100:
        raise ValueError(_('Month must look like 2026-10.'))
    return date(int(match.group(1)), int(match.group(2)), 1)


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        raise ValueError(_('Send a JSON body.')) from None
    if not isinstance(data, dict):
        raise ValueError(_('Send a JSON object.'))
    return data


def _required_month(data):
    if not data.get('month'):
        raise ValueError(_('Month must look like 2026-10.'))
    return _parse_month(data['month'], None)


def _category(user, value):
    """The user's own category by id or uuid; anything else (including someone else's) is 404."""
    if isinstance(value, bool) or value in (None, ''):
        raise writes.BudgetNotFound('not_found', _('Category not found.'))
    try:
        return get_object_by_uuid_or_pk(Category.objects.filter(user=user), value)
    except Exception:
        raise writes.BudgetNotFound('not_found', _('Category not found.')) from None


def _amount(data):
    if 'amount' not in data:
        raise writes.BudgetError('invalid_amount', _('Enter an amount, or null to remove the limit.'))
    value = data['amount']
    if isinstance(value, bool) or not (value is None or isinstance(value, (int, float, str))):
        raise writes.BudgetError('invalid_amount', _('Enter a valid amount.'))
    return value


def _page(request, month, sort='urgent'):
    return JsonResponse({'success': True, **build_budget_page(request.user, month, timezone.localdate(), sort)})


def _guarded(view):
    """Turn a rejected budget write or bad input into the API's JSON error."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except writes.BudgetError as exc:
            return _error(exc.code, exc, exc.status)
        except ValueError as exc:
            return _error('invalid_request', exc)
    return wrapper


@api_login_required
@require_http_methods(['GET'])
@_guarded
def budgets_page(request):
    today = timezone.localdate()
    month = _parse_month(request.GET.get('month'), today)
    sort = request.GET.get('sort', 'urgent')
    return _page(request, month, sort if sort in SORTS else 'urgent')


@api_login_required
@require_http_methods(['PUT'])
@_guarded
def budgets_set_limit(request):
    data = _body(request)
    month = _required_month(data)
    category = _category(request.user, data.get('category_id'))
    writes.set_limit(request.user, category, month, _amount(data), data.get('scope'), actor=request.user)
    return _page(request, month)


@api_login_required
@require_http_methods(['POST'])
@_guarded
def budgets_reset(request):
    data = _body(request)
    month = _required_month(data)
    writes.reset_override(request.user, _category(request.user, data.get('category_id')), month)
    return _page(request, month)


@api_login_required
@require_http_methods(['POST'])
@_guarded
def budgets_rebalance(request):
    data = _body(request)
    month = _required_month(data)
    raw = data.get('moves')
    if not isinstance(raw, list) or not raw or len(raw) > 50:
        raise writes.BudgetError('no_moves', _('Choose at least one move.'))
    moves = []
    for mv in raw:
        if not isinstance(mv, dict):
            raise writes.BudgetError('invalid_move', _('Each move needs two categories and an amount.'))
        moves.append({
            'from_category': _category(request.user, mv.get('from_category')),
            'to_category': _category(request.user, mv.get('to_category')),
            'amount': mv.get('amount'),
        })
    writes.rebalance(request.user, month, data.get('scope'), moves, actor=request.user)
    return _page(request, month)




@api_login_required
@require_http_methods(['POST'])
@_guarded
def budgets_dismiss_suggestion(request):
    from ..models import BudgetSuggestionDismissal

    key = _body(request).get('key')
    if not isinstance(key, str) or not re.match(r'^move:\d+:\d+:\d{4}-\d{2}$', key):
        raise ValueError(_('Unknown suggestion.'))
    BudgetSuggestionDismissal.objects.get_or_create(user=request.user, key=key)
    return JsonResponse({'success': True})


@api_login_required
@require_http_methods(['GET'])
@_guarded
def budgets_suggestion_fragment(request):
    """The "Smart move" banner as an HTML fragment. The page loads it after it has painted, because it
    needs three months of history that the page itself does not."""
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    from ..budget_suggestions import find_suggestion

    today = timezone.localdate()
    month = _parse_month(request.GET.get('month'), today)
    if month != today.replace(day=1):
        return HttpResponse('')
    page = build_budget_page(request.user, month, today)
    suggestion = find_suggestion(request.user, month, page['categories'])
    if not suggestion:
        return HttpResponse('')
    profile = getattr(request.user, 'profile', None)
    return HttpResponse(render_to_string('expenses/partials/_budget_suggestion.html', {
        'suggestion': suggestion,
        'currency_symbol': profile.currency if profile and profile.currency else '₹',
    }, request=request))
