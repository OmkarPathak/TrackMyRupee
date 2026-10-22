"""HTTP for insights: the panel fragment, the person's actions on a card, their Settings, and the cron triggers."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core import signing
from django.core.cache import cache
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..models import InsightState
from ..views.notifications import _cron_authorized, _dispatch_cron_command
from . import engine, is_engine_on, prefs, registry, version
from .types import Module, Surface

# A page's strip leaves out what the page already says on its own, because one insight is never shown twice in a view:
# the Budget page lists limits passed or nearing and has its "Smart move" banner; the Subscriptions page lists what is due;
# the Accounts page has its own "bill due soon" card (a maturing deposit stays in the strip).
STRIP_SKIPS = {
    Module.BUDGETS: frozenset({'budgets.limit_passed', 'budgets.limit_nearing', 'budgets.room_to_share'}),
    Module.RECURRING: frozenset({'recurring.bills_soon'}),
    Module.ACCOUNTS: frozenset({'accounts.card_billing_soon'}),
}
PANEL_SURFACES = {s.value: s for s in (Surface.DASHBOARD, Surface.MODULE, Surface.ANALYTICS)}


def _int(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _modules(raw: str | None):
    if not raw:
        return None
    wanted = {m for m in raw.split(',') if m in Module._value2member_map_}
    return {Module(m) for m in wanted}          # unknown names narrow to nothing, never to everything


logger = logging.getLogger(__name__)


@login_required
@require_GET
def panel(request):
    """The insight cards, as a fragment the page loads after it has painted. When the person's data has changed
    since the last run, the quick rules are re-run here first (once, and not more than every ten minutes)."""
    if not is_engine_on(request.user):
        return HttpResponse('')
    surface = PANEL_SURFACES.get(request.GET.get('surface', 'dashboard'))
    if surface is None:
        return HttpResponseBadRequest('unknown surface')
    profile = request.user.profile
    strip = request.GET.get('strip') == '1'
    cache_key = None
    if strip and settings.INSIGHTS_STRIP_CACHE_SECONDS:
        # a page's strip is asked for again every time its list is swapped (filter, search, next page): reuse the
        # last one for a minute; anything that changes what it says (a run, a dismissal, a setting) retires it
        cache_key = (f'insights_strip_{request.user.pk}_{version(request.user.pk)}_{get_language()}_'
                     f'{request.GET.get("surface", "")}_{request.GET.get("modules", "")}')
        cached = cache.get(cache_key)
        if cached is not None:
            return HttpResponse(cached)
    if engine.needs_refresh(profile):
        try:
            engine.refresh(request.user, costs=('light',), profile=profile)
        except Exception as exc:       # noqa: BLE001 - show what is stored rather than a broken panel
            logger.warning('insight refresh failed type=%s', type(exc).__name__)
    modules = _modules(request.GET.get('modules'))
    skip = set().union(*(STRIP_SKIPS.get(m, ()) for m in modules or ())) if strip else set()
    items = engine.generate(request.user, surface, modules=modules, profile=profile, skip_rules=skip)
    if strip:         # a page's strip says nothing at all when there is nothing to say
        html = render_to_string('expenses/partials/_insight_strip.html', {'items': items}, request=request) if items else ''
        if cache_key:
            cache.set(cache_key, html, settings.INSIGHTS_STRIP_CACHE_SECONDS)
        return HttpResponse(html)
    return HttpResponse(render_to_string('expenses/partials/_insight_cards.html', {
        'items': items,
        'learning': engine.is_learning(profile),
        'switched_off': not prefs.get(profile)['enabled'],
    }, request=request))


def _acted_on(request, state_uuid, action) -> HttpResponse:
    if not InsightState.objects.filter(user=request.user, uuid=state_uuid).exists():
        raise Http404
    action(request.user, state_uuid)
    return HttpResponse('')


@login_required
@require_POST
def dismiss(request, state_uuid):
    return _acted_on(request, state_uuid, engine.dismiss)


@login_required
@require_POST
def snooze(request, state_uuid):
    return _acted_on(request, state_uuid, engine.snooze)


@login_required
@require_POST
def helpful(request, state_uuid, verdict):
    row = InsightState.objects.filter(user=request.user, uuid=state_uuid).only('rule_id').first()
    if row is None or verdict not in ('yes', 'no'):
        raise Http404
    engine.feedback(request.user, row.rule_id, verdict == 'yes', profile=request.user.profile)
    return HttpResponse(render_to_string('expenses/partials/_insight_thanks.html', {'helpful': verdict == 'yes'}, request=request))


@login_required
@require_GET
def go(request, state_uuid):
    """The card's next step. Following it counts as resolved. The target comes from the stored insight, never from
    the query string, so this cannot be used to redirect anywhere else."""
    row = InsightState.objects.filter(user=request.user, uuid=state_uuid).first()
    if row is None:
        raise Http404
    engine.acted(request.user, state_uuid)
    action = (row.payload or {}).get('action')
    target = action[1] if action and action[1] and action[1].startswith('/') and not action[1].startswith('//') and '\\' not in action[1] else None
    return redirect(target or reverse('home'))


@login_required
@require_http_methods(['POST'])
def settings_save(request):
    profile = request.user.profile
    prefs.save(
        profile,
        enabled='enabled' in request.POST,
        modules_off=[m.value for m in registry.modules_with_rules() if m.value not in request.POST.getlist('modules')],
        notify=request.POST.get('notify', 'normal'),
        push='push' in request.POST,
        emergency_months=_int(request.POST.get('emergency_months')),
        digest=request.POST.get('digest'),
    )
    messages.success(request, _('Your Insights settings are saved.'))
    return redirect(reverse('profile-settings') + '#insights')


# ---- the email digest's unsubscribe link: signed, works without signing in, one click ----

DIGEST_SALT = 'insights.digest.unsubscribe'


def digest_token(user) -> str:
    return signing.dumps({'u': user.pk}, salt=DIGEST_SALT)


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def digest_unsubscribe(request, token):
    """GET shows a button (a mail scanner opening the link changes nothing); POST switches the digest off. Mail apps
    that support one-click unsubscribe post here directly (List-Unsubscribe-Post)."""
    try:
        user_id = signing.loads(token, salt=DIGEST_SALT)['u']
    except (signing.BadSignature, KeyError, TypeError):
        return HttpResponseBadRequest(_('This unsubscribe link is not valid.'))
    user = User.objects.filter(pk=user_id).select_related('profile').first()
    if user is None:
        return HttpResponseBadRequest(_('This unsubscribe link is not valid.'))
    done = request.method == 'POST'
    if done:
        prefs.set_digest(user.profile, 'off')
    return HttpResponse(render_to_string('expenses/insight_digest_unsubscribe.html', {'done': done}, request=request))


# ---- cron (same pattern as the other jobs: a secret, a background thread, a lock) ----

@csrf_exempt
@require_POST
def trigger_generate(request):
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('generate_insights', lock_timeout=1800)


@csrf_exempt
@require_POST
def trigger_digest(request):
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('send_insight_digest', lock_timeout=1800)


@csrf_exempt
@require_POST
def trigger_deliver(request):
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('deliver_insights', lock_timeout=900)
