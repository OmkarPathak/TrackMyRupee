"""The engine: it evaluates rules into stored insights (`refresh`) and reads them back for a surface (`generate`).

Pages only ever call `generate`, which is one query on InsightState and no arithmetic. `refresh` is what does the
work, in a nightly batch, or once in the dashboard fragment when a person's data has changed since the last run.

A rule that raises is skipped and logged by its id and exception class only (never `exc_info`, which could carry an
amount or a category name into logs and error reports). One bad rule never breaks a page.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from . import prefs as prefs_module
from . import registry, telemetry, touch
from .context import InsightContext
from .gating import plan_of, rule_allowed
from .types import Action, Insight, Kind, Plan, Presented, Surface, Tone

logger = logging.getLogger(__name__)

CAPS = {Surface.DASHBOARD: 3, Surface.MODULE: 2, Surface.ANALYTICS: 3}      # most shown at once
PER_MODULE = {Surface.DASHBOARD: 2}                                          # and from any one module
KIND_ORDER = {Kind.UPCOMING: 0, Kind.OPPORTUNITY: 1, Kind.PATTERN: 2, Kind.OBSERVATION: 3, Kind.PROGRESS: 4,
              Kind.MILESTONE: 5}
SNOOZE_DAYS = 7
LOOKBACK_DAYS = 90               # how far back a dismissal still holds a rule's cooldown
LOCKED_GAP = timedelta(days=7)   # a locked preview is shown at most once a week
POOL_WINDOW = timedelta(days=7)  # how long a Free person's weekly observation stays
MIN_REFRESH_GAP = timedelta(minutes=10)
MAX_AGE = timedelta(hours=1)
DIRTY_TTL = 6 * 3600

FIELDS = ['payload', 'lang', 'priority', 'valid_until', 'generated_at', 'locked']


@dataclass
class RefreshResult:
    written: int = 0
    retired: int = 0
    failed: list[str] = field(default_factory=list)
    skipped_for_history: int = 0
    ran: bool = False


# ---------------------------------------------------------------------------------------------- write path

def _dirty_key(user_id) -> str:
    return f'insights_dirty_{user_id}'


def mark_stale(user_id) -> None:
    """Something the insights are built from changed. This is a cache write, not a database one, so saving an entry
    costs no extra query. (With a per-process cache another worker will not see it: the hourly and daily refreshes
    in `needs_refresh` cover that.)"""
    cache.set(_dirty_key(user_id), 1, DIRTY_TTL)


def needs_refresh(profile, now=None) -> bool:
    """Whether a page visit should re-run the quick rules. Never within ten minutes of the last run; after that, when
    the data changed (a write, or a change in Settings), the day turned over (pace and dates move every day), or the
    last run is an hour old."""
    now = now or timezone.now()
    last = profile.insights_generated_at
    if last is None:
        return True
    if now - last < MIN_REFRESH_GAP:
        return False
    return (profile.insights_stale or bool(cache.get(_dirty_key(profile.user_id)))
            or timezone.localdate(last) != timezone.localdate(now) or now - last >= MAX_AGE)


def _locked_copy(rule, insight: Insight) -> Insight | None:
    """What a plan below the rule's minimum sees: a kind line with no figures and a link to the plans."""
    if rule.preview is None or insight.sensitive:
        return None
    return Insight(
        rule_id=insight.rule_id, module=insight.module, kind=Kind.OBSERVATION, tone=Tone.CALM,
        priority=min(insight.priority, 30), title=str(rule.preview()),
        message=_('There is a little more to see with %(plan)s.') % {'plan': rule.min_plan.name.title()},
        action=Action(_('See plans'), reverse('pricing')), dedupe_key=insight.dedupe_key,
        valid_until=insight.valid_until, surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}) & insight.surfaces,
        plan=rule.min_plan, icon='lock')


def _refresh_lock(user_id) -> str:
    return f'insights_refresh_{user_id}'


def refresh(user, *, today=None, costs=('light', 'heavy'), profile=None) -> RefreshResult:
    """Evaluate the rules for one person and store what they have to say. Safe to run again: stored rows are
    updated in place and a dismissal, a snooze or an "acted on" is never undone.

    Only one run per person at a time (two tabs, or a page visit during the nightly batch): a second caller
    returns at once with `ran` False. A run that fails still stamps the time, so a broken run is not retried on
    every page view."""
    from ..models import UserProfile

    profile = profile if profile is not None else user.profile
    if not cache.add(_refresh_lock(user.pk), 1, 120):
        return RefreshResult(ran=False)
    try:
        return _refresh(user, today=today, costs=costs, profile=profile)
    except Exception:
        UserProfile.objects.filter(pk=profile.pk).update(insights_generated_at=timezone.now())
        raise
    finally:
        cache.delete(_refresh_lock(user.pk))


_background_slots = threading.BoundedSemaphore(4)       # at most this many background runs per process at once


def refresh_in_background(user_id) -> bool:
    """Run the quick rules for one person after the response has gone, so a page's insights come back at once from
    what is stored and the next visit shows the fresh ones. Returns False when the process is already busy with
    several of these (the next visit tries again: nothing is marked as done)."""
    if not _background_slots.acquire(blocking=False):
        return False
    threading.Thread(target=_run_background, args=(user_id,), daemon=True, name='insights-refresh').start()
    return True


def _run_background(user_id, *, release=True) -> None:
    from django.contrib.auth import get_user_model
    from django.db import connection

    try:
        user = get_user_model().objects.select_related('profile').filter(pk=user_id).first()
        if user is not None:
            refresh(user, costs=('light',), profile=user.profile)
    except Exception as exc:       # noqa: BLE001 - nobody is waiting: log the class only
        logger.warning('insight background refresh failed type=%s', type(exc).__name__)
    finally:
        connection.close()
        if release:
            _background_slots.release()


def _refresh(user, *, today, costs, profile) -> RefreshResult:
    from ..models import InsightState, UserProfile

    result = RefreshResult(ran=True)
    prefs = prefs_module.get(profile)
    now = timezone.now()
    today = today or timezone.localdate()
    if not prefs['enabled']:
        UserProfile.objects.filter(pk=profile.pk).update(insights_generated_at=now, insights_stale=False)
        return result

    plan = plan_of(profile)
    with translation.override(profile.language or 'en'):
        ctx = InsightContext(user, today=today, profile=profile)
        existing = {(s.rule_id, s.dedupe_key): s for s in InsightState.objects.filter(user=user)}
        to_create, to_update, emitted, succeeded = {}, [], set(), set()

        for rule in registry.all_rules():
            if rule.cost not in costs or rule.mode != 'stored' or not prefs_module.module_on(prefs, rule.module):
                continue
            try:
                missing = bool(rule.requires) and not all(ctx.inventory[k] for k in rule.requires)
                short = bool(rule.min_history) and ctx.history_cycles() < rule.min_history
            except Exception as exc:         # noqa: BLE001 - a loader that fails skips this rule, not the whole run
                result.failed.append(rule.id)
                logger.warning('insight rule failed rule_id=%s type=%s', rule.id, type(exc).__name__)
                continue
            if missing:
                succeeded.add(rule.id)    # no loans, no cards, no recurring payments (any more): nothing to say, so old rows retire
                continue
            if short:
                result.skipped_for_history += 1
                continue
            locked = not rule_allowed(rule.min_plan, plan)
            try:
                with ctx.declared(rule.needs):
                    produced = rule.evaluate(ctx)
                if locked:      # a plan below the rule's minimum keeps only the figure-free preview
                    produced = [copy for copy in (_locked_copy(rule, i) for i in produced) if copy is not None]
            except Exception as exc:         # noqa: BLE001 - isolation is the point; only the id and class are logged
                result.failed.append(rule.id)
                logger.warning('insight rule failed rule_id=%s type=%s', rule.id, type(exc).__name__)
                continue
            succeeded.add(rule.id)
            for insight in produced:
                key = (rule.id, insight.dedupe_key)
                emitted.add(key)
                row = existing.get(key) or to_create.get(key)
                values = {'payload': insight.to_payload(), 'lang': translation.get_language() or 'en',
                          'priority': insight.priority, 'valid_until': insight.valid_until, 'generated_at': now,
                          'locked': locked}
                if row is None:       # (a rule that says the same thing twice in one run is stored once)
                    to_create[key] = InsightState(user=user, rule_id=rule.id, dedupe_key=insight.dedupe_key, **values)
                else:
                    for name, value in values.items():
                        setattr(row, name, value)
                    if key not in to_create:
                        to_update.append(row)

        if to_create:
            InsightState.objects.bulk_create(list(to_create.values()), ignore_conflicts=True)
        if to_update:
            InsightState.objects.bulk_update(to_update, FIELDS)
        result.written = len(to_create) + len(to_update)

        # A rule that ran and no longer has something to say (the spending came back down) retires its old rows.
        stale = [s.pk for key, s in existing.items()
                 if key[0] in succeeded and key not in emitted and s.valid_until >= today]
        if stale:
            InsightState.objects.filter(pk__in=stale).update(valid_until=today - timedelta(days=1))
        result.retired = len(stale)
        InsightState.objects.filter(
            user=user, valid_until__lt=today - timedelta(days=InsightState.KEEP_DAYS)).delete()

    learning = bool(result.skipped_for_history) and not (to_create or to_update)
    cache.delete(_dirty_key(user.pk))
    # Only the engine's own key is written, on a fresh read: a change the person made to their settings (or a
    # digest unsubscribe) while this run was going must not be undone.
    prefs_module.stamp_run(profile, learning=learning, now=now)
    touch(user.pk)
    return result


# ----------------------------------------------------------------------------------------------- read path

def _sort_key(insight: Insight):
    return (-insight.priority, KIND_ORDER[insight.kind], insight.valid_until, insight.rule_id, insight.dedupe_key)


def candidates(user, surface: Surface, *, modules=None, profile=None, now=None, exclude=(), skip_rules=(), also=()):
    """The stored insights that may be shown on `surface` now, best first, as (Insight, InsightState) pairs:
    not dismissed, snoozed, cooling down, muted or above the person's plan. One query. `also` lists more surfaces
    that qualify (the digest reads the dashboard's and the pages' insights in one pass)."""
    from ..models import InsightState

    profile = profile if profile is not None else user.profile
    prefs = prefs_module.get(profile)
    if not prefs['enabled']:
        return []
    now = now or timezone.now()
    today = timezone.localdate(now)
    plan = plan_of(profile)
    horizon = now - timedelta(days=LOOKBACK_DAYS)

    rows = list(InsightState.objects.filter(user=user).filter(
        Q(valid_until__gte=today) | Q(dismissed_at__gte=horizon) | Q(acted_at__gte=horizon)).order_by('id'))

    resolved: dict[str, object] = {}
    for row in rows:
        when = max((t for t in (row.dismissed_at, row.acted_at) if t), default=None)
        if when and when > resolved.get(row.rule_id, when - timedelta(seconds=1)):
            resolved[row.rule_id] = when

    found = []
    for row in rows:
        rule = registry.get(row.rule_id)
        if (rule is None or row.valid_until < today or row.dismissed_at or row.acted_at
                or (row.snoozed_until and row.snoozed_until > now)
                or (row.rule_id, row.dedupe_key) in exclude or row.rule_id in skip_rules
                or not prefs_module.module_on(prefs, rule.module) or prefs_module.is_muted(prefs, row.rule_id, today)):
            continue
        done = resolved.get(row.rule_id)
        if done and row.created_at > done and now - done < timedelta(days=rule.cooldown_days):
            continue            # dismissed or acted on lately: the rule rests a while before it speaks again
        if row.locked:
            if rule_allowed(rule.min_plan, plan) or (row.last_shown and now - row.last_shown < LOCKED_GAP):
                continue
        elif not rule_allowed(rule.min_plan, plan):
            continue
        try:
            insight = Insight.from_payload(row.payload)
        except Exception:               # noqa: BLE001 - a row from an older shape is skipped, never shown half-made
            continue
        if {surface, *also}.isdisjoint(insight.surfaces) or (modules is not None and insight.module not in modules):
            continue
        found.append((insight, row))
    found.sort(key=lambda pair: _sort_key(pair[0]))
    return _weekly_pool(found, plan, now, rows)


def _weekly_pool(found, plan, now, rows=()):
    """On the Free plan the rules in the "weekly" pool share one observation a week: the one first shown in the last
    seven days stays; otherwise the best one nobody has seen yet; the rest wait. Plus and Pro see them all.

    "The one shown this week" is looked up among all the person's rows, not only those this page may show, so a
    different page (or a dismissal) cannot bring a second observation into the same week."""
    if plan != Plan.FREE:
        return found
    pooled = [pair for pair in found if registry.get(pair[1].rule_id).pool == 'weekly']
    if not pooled:
        return found
    shown = [r for r in rows if r.first_shown and now - r.first_shown < POOL_WINDOW
             and (rule := registry.get(r.rule_id)) is not None and rule.pool == 'weekly']
    if shown:
        latest = max(shown, key=lambda r: r.first_shown)
        keep = [p for p in pooled if p[1].pk == latest.pk]
    else:
        keep = [p for p in pooled if not p[1].first_shown][:1]
    return [pair for pair in found if pair not in pooled or pair in keep]


def generate(user, surface: Surface, *, modules=None, limit: int | None = None, profile=None, now=None,
             exclude=(), skip_rules=()) -> list[Presented]:
    """The insights to show on `surface` right now, ranked and capped, with at most two from one module on the
    dashboard. One query, plus one write the first time something is shown on a given day.

    `modules` narrows to some modules (a module page); `exclude` is a set of (rule_id, dedupe_key) already shown
    elsewhere in the same view, so one insight never appears twice on a page."""
    from ..models import InsightState

    now = now or timezone.now()
    today = timezone.localdate(now)
    found = candidates(user, surface, modules=modules, profile=profile, now=now, exclude=exclude, skip_rules=skip_rules)
    cap = limit or CAPS.get(surface, 3)
    per_module, picked, locked_shown = {}, [], 0
    for insight, row in found:
        if len(picked) >= cap:
            break
        if row.locked:
            if locked_shown:
                continue
            locked_shown += 1
        limit_for_module = PER_MODULE.get(surface)
        if limit_for_module and per_module.get(insight.module, 0) >= limit_for_module:
            continue
        per_module[insight.module] = per_module.get(insight.module, 0) + 1
        picked.append((insight, row))

    fresh = [row for _insight, row in picked if row.last_shown is None or timezone.localdate(row.last_shown) < today]
    if fresh:
        for row in fresh:
            row.first_shown = row.first_shown or now
            row.last_shown = now
            row.times_shown += 1
        InsightState.objects.bulk_update(fresh, ['first_shown', 'last_shown', 'times_shown'])
        telemetry.event(user, 'shown', surface=surface.value, count=len(fresh))
    return [Presented(insight, str(row.uuid), row.locked) for insight, row in picked]


def inline(ctx, rule_ids, *, limit: int | None = None) -> list[Insight]:
    """Run inline rules (the sentences on a page) against `ctx` right now: best first, never stored, nothing shown
    to a plan below a rule's minimum. A rule that raises is skipped and logged by id and class, like a stored one."""
    found = []
    for rule_id in rule_ids:
        rule = registry.get(rule_id)
        if rule is None or rule.mode != 'inline' or not rule_allowed(rule.min_plan, ctx.plan):
            continue
        try:
            with ctx.declared(rule.needs):
                found.extend(rule.evaluate(ctx))
        except Exception as exc:         # noqa: BLE001 - one bad sentence must not break a tab
            logger.warning('inline insight rule failed rule_id=%s type=%s', rule.id, type(exc).__name__)
    found.sort(key=_sort_key)
    return found[:limit] if limit else found


def compose(user, surface: Surface, *, today=None, profile=None) -> list[Insight]:
    """Everything the stored rules would say on `surface` for the budget month containing `today`, worked out now
    and not stored: how the monthly report, the Year in Review and the digest look at a month that is over. Plans
    below a rule's minimum get nothing (there is no preview in an email), and only rules that list `surface` run."""
    profile = profile if profile is not None else user.profile
    prefs = prefs_module.get(profile)
    plan = plan_of(profile)
    today = today or timezone.localdate()
    if not prefs['enabled']:
        return []
    found = []
    with translation.override(profile.language or 'en'):
        ctx = InsightContext(user, today=today, profile=profile)
        for rule in registry.all_rules():
            if (surface not in rule.surfaces or rule.mode != 'stored' or not rule_allowed(rule.min_plan, plan)
                    or not prefs_module.module_on(prefs, rule.module)):
                continue
            try:
                if rule.requires and not all(ctx.inventory[k] for k in rule.requires):
                    continue
                if rule.min_history and ctx.history_cycles() < rule.min_history:
                    continue
                with ctx.declared(rule.needs):
                    found.extend(i for i in rule.evaluate(ctx) if surface in i.surfaces)
            except Exception as exc:     # noqa: BLE001
                logger.warning('insight rule failed rule_id=%s type=%s', rule.id, type(exc).__name__)
    found.sort(key=_sort_key)
    return found


def pick(insights, limit: int = 3, *, positive_first: bool = True, allow_sensitive: bool = True) -> list[Insight]:
    """Up to `limit` insights from different modules where possible, the good news first (when asked), then by rank."""
    pool = sorted((i for i in insights if allow_sensitive or not i.sensitive),
                  key=lambda i: ((i.tone != Tone.POSITIVE,) if positive_first else ()) + _sort_key(i))
    picked, seen = [], set()
    for i in pool:
        if i.module not in seen and len(picked) < limit:
            picked.append(i)
            seen.add(i.module)
    for i in pool:
        if i not in picked and len(picked) < limit:
            picked.append(i)
    return picked


def is_learning(profile) -> bool:
    return bool(prefs_module.get(profile)['learning'])


# ------------------------------------------------------------------------------------------ person's actions

def _row(user, state_uuid):
    from ..models import InsightState
    return InsightState.objects.filter(user=user, uuid=state_uuid).first()


def dismiss(user, state_uuid) -> bool:
    row = _row(user, state_uuid)
    if row is None:
        return False
    row.dismissed_at = timezone.now()
    row.save(update_fields=['dismissed_at'])
    touch(user.pk)
    telemetry.event(user, 'action', rule_id=row.rule_id, action='dismiss')
    return True


def snooze(user, state_uuid, days: int = SNOOZE_DAYS) -> bool:
    row = _row(user, state_uuid)
    if row is None:
        return False
    row.snoozed_until = timezone.now() + timedelta(days=days)
    row.save(update_fields=['snoozed_until'])
    touch(user.pk)
    telemetry.event(user, 'action', rule_id=row.rule_id, action='snooze')
    return True


def acted(user, state_uuid) -> str | None:
    """The person followed the insight's next step: it counts as resolved. Returns the rule id."""
    row = _row(user, state_uuid)
    if row is None:
        return None
    if row.acted_at is None:
        row.acted_at = timezone.now()
        row.save(update_fields=['acted_at'])
        touch(user.pk)
        telemetry.event(user, 'action', rule_id=row.rule_id, action='acted')
    return row.rule_id


def feedback(user, rule_id: str, helpful: bool, *, profile=None) -> bool:
    """"Was this helpful?" Yes or no per rule, no free text. A second "no" mutes the rule for 90 days."""
    from ..models import InsightFeedback

    if registry.get(rule_id) is None:
        return False
    row, _created = InsightFeedback.objects.get_or_create(user=user, rule_id=rule_id, defaults={'helpful': helpful})
    row.helpful = helpful
    if helpful:
        row.not_helpful_count = 0
    else:
        row.not_helpful_count += 1
        if row.not_helpful_count >= 2:
            prefs_module.mute(profile if profile is not None else user.profile, rule_id, timezone.localdate())
            row.not_helpful_count = 0
    row.save()
    touch(user.pk)
    telemetry.event(user, 'action', rule_id=rule_id, action='helpful' if helpful else 'not_helpful')
    return True
