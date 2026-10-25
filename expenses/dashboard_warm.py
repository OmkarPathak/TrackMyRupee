"""Have the Dashboard ready before the person gets there.

Saving an expense retires the cached Dashboard, and the page the person lands on next then builds it from scratch
(the slowest thing after an add or an edit). `schedule_dashboard_warm` rebuilds it right after the save has
committed, on a background thread, so the landing page is served from the cache.

Nothing here can show stale numbers: every write bumps a version (see `bump_dashboard_version`), and a build only
stores its result when the version is the one it started with. A page that is asked for while a rebuild is under
way waits for it (a couple of seconds at most) instead of doing the same work again.
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone, translation

from .background import run_after_response

logger = logging.getLogger(__name__)

WARM_FLAG_SECONDS = 60       # the longest a rebuild is assumed to take
WAIT_SECONDS = 2.5           # the longest a page waits for a rebuild already under way
WAIT_STEP = 0.08


def version_key(user_id) -> str:
    return f'dash_ver_{user_id}'


def warm_flag_key(user_id) -> str:
    return f'dash_warm_{user_id}'


def dashboard_version(user_id) -> int:
    return cache.get(version_key(user_id), 0)


def bump_dashboard_version(user_id) -> None:
    """Called on every write that changes what the Dashboard shows: a build that started before it must not be kept."""
    key = version_key(user_id)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, 7 * 86400)


def schedule_dashboard_warm(user_id) -> None:
    """Rebuild this person's Dashboard after the current save has committed. Cheap to call: it only sets a flag."""
    if not getattr(settings, 'DASHBOARD_WARM_ENABLED', False) or not user_id:
        return
    if not cache.add(warm_flag_key(user_id), 1, WARM_FLAG_SECONDS):
        return                                      # one rebuild at a time per person
    tz, lang = timezone.get_current_timezone_name(), translation.get_language()
    transaction.on_commit(lambda: run_after_response(_warm, user_id, tz, lang, name='dashboard-warm', if_busy='skip',
                                                     on_skip=lambda: cache.delete(warm_flag_key(user_id))))


def wait_for_warm(user_id) -> bool:
    """Let a rebuild that is already running finish. True when it did and left the Dashboard cached."""
    if not cache.get(warm_flag_key(user_id)):
        return False
    deadline = time.monotonic() + WAIT_SECONDS
    while time.monotonic() < deadline and cache.get(warm_flag_key(user_id)):
        time.sleep(WAIT_STEP)
    return True


def _warm(user_id, tz_name, language) -> None:
    import zoneinfo

    from django.contrib.auth import get_user_model
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.contrib.sessions.backends.db import SessionStore
    from django.test import RequestFactory

    from .insights import is_engine_on
    from .views.dashboard import home_view
    from .views.dashboard_networth import (
        NETWORTH_CACHE_SECONDS,
        cached_net_worth_card,
        net_worth_card,
        networth_cache_key,
    )

    try:
        user = get_user_model().objects.select_related('profile').filter(pk=user_id).first()
        if user is None:
            return
        try:
            zone = zoneinfo.ZoneInfo(tz_name)
        except Exception:      # noqa: BLE001
            zone = timezone.get_default_timezone()
        with timezone.override(zone), translation.override(language):
            request = RequestFactory().get('/dashboard/')
            request.user = user
            request.LANGUAGE_CODE = language
            request.is_dashboard_warm = True               # this IS the rebuild: it must not wait for itself
            request.session = SessionStore()               # nothing is saved: the response is thrown away
            request._messages = FallbackStorage(request)
            home_view(request)                             # builds the page and caches what it computed
            if is_engine_on(user) and cached_net_worth_card(user.pk) is None:
                version = dashboard_version(user.pk)
                card = net_worth_card(user, currency_symbol=user.profile.currency)
                if dashboard_version(user.pk) == version:
                    cache.set(networth_cache_key(user.pk), card, NETWORTH_CACHE_SECONDS)
    except Exception as exc:       # noqa: BLE001 - nobody is waiting: the page builds itself as it always did
        logger.warning('dashboard warm failed type=%s', type(exc).__name__)
    finally:
        cache.delete(warm_flag_key(user_id))
