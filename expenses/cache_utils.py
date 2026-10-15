import functools
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from django.views.decorators.cache import cache_page

logger = logging.getLogger(__name__)

# Per-thread/per-task flag. The old implementation disconnected the signal receivers globally,
# which silently disabled invalidation for every other request running in the same process.
_dashboard_invalidation_suppressed = ContextVar('dashboard_invalidation_suppressed', default=False)


def dashboard_invalidation_suppressed():
    return _dashboard_invalidation_suppressed.get()


@contextmanager
def suppress_dashboard_cache_invalidation(user_id=None, invalidate_on_exit=True):
    """
    Context manager to temporarily suppress dashboard cache invalidation signals
    during bulk operations (such as TMR Flows commit or recurring transaction processing).
    If invalidate_on_exit is True and user_id is provided, invalidate_dashboard_cache is called
    once upon exiting the block.
    """
    from .signals import invalidate_dashboard_cache

    token = _dashboard_invalidation_suppressed.set(True)
    try:
        yield
    finally:
        _dashboard_invalidation_suppressed.reset(token)
        if invalidate_on_exit and user_id is not None:
            invalidate_dashboard_cache(user_id=user_id, force=True)


def anonymous_cache_page(timeout):
    """
    Decorator that applies cache_page only for anonymous (unauthenticated) visitors.
    Authenticated requests bypass the cache completely to avoid serving stale or
    cross-user account data.
    """
    def decorator(view_func):
        cached_view = cache_page(timeout)(view_func)

        @functools.wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not getattr(request, 'user', None) or not request.user.is_authenticated:
                return cached_view(request, *args, **kwargs)
            return view_func(request, *args, **kwargs)

        return _wrapped_view

    return decorator
