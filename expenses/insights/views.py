"""HTTP for insights: the panel fragment, the person's actions on a card, their Settings, and the cron triggers."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..models import InsightState
from ..views.notifications import _cron_authorized, _dispatch_cron_command
from . import engine, is_engine_on, prefs, registry
from .types import Module, Surface

PANEL_SURFACES = {s.value: s for s in (Surface.DASHBOARD, Surface.MODULE, Surface.ANALYTICS)}


def _modules(raw: str | None):
    if not raw:
        return None
    wanted = {m for m in raw.split(',') if m in Module._value2member_map_}
    return {Module(m) for m in wanted} or None


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
    if engine.needs_refresh(profile):
        engine.refresh(request.user, costs=('light',), profile=profile)
    items = engine.generate(request.user, surface, modules=_modules(request.GET.get('modules')), profile=profile)
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
    target = action[1] if action and action[1] and action[1].startswith('/') and not action[1].startswith('//') else None
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
    )
    messages.success(request, _('Your Insights settings are saved.'))
    return redirect(reverse('profile-settings') + '#insights')


# ---- cron (same pattern as the other jobs: a secret, a background thread, a lock) ----

@csrf_exempt
@require_POST
def trigger_generate(request):
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('generate_insights', lock_timeout=1800)


@csrf_exempt
@require_POST
def trigger_deliver(request):
    if not _cron_authorized(request):
        return JsonResponse({'error': 'Unauthorized'}, status=403)
    return _dispatch_cron_command('deliver_insights', lock_timeout=900)
