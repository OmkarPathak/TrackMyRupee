import functools
import logging
from contextlib import contextmanager

from django.db.models.signals import post_delete, post_save
from django.views.decorators.cache import cache_page

logger = logging.getLogger(__name__)


@contextmanager
def suppress_dashboard_cache_invalidation(user_id=None, invalidate_on_exit=True):
    """
    Context manager to temporarily suppress dashboard cache invalidation signals
    during bulk operations (such as TMR Flows commit or recurring transaction processing).
    If invalidate_on_exit is True and user_id is provided, invalidate_dashboard_cache is called
    once upon exiting the block.
    """
    from .signals import _DASHBOARD_CACHE_MODELS, invalidate_dashboard_cache

    models_to_disconnect = _DASHBOARD_CACHE_MODELS
    for model in models_to_disconnect:
        post_save.disconnect(
            invalidate_dashboard_cache,
            sender=model,
            dispatch_uid=f'dashboard_cache_invalidate_save_{model.__name__}',
        )
        post_delete.disconnect(
            invalidate_dashboard_cache,
            sender=model,
            dispatch_uid=f'dashboard_cache_invalidate_delete_{model.__name__}',
        )
    try:
        yield
    finally:
        for model in models_to_disconnect:
            post_save.connect(
                invalidate_dashboard_cache,
                sender=model,
                dispatch_uid=f'dashboard_cache_invalidate_save_{model.__name__}',
            )
            post_delete.connect(
                invalidate_dashboard_cache,
                sender=model,
                dispatch_uid=f'dashboard_cache_invalidate_delete_{model.__name__}',
            )
        if invalidate_on_exit and user_id is not None:
            invalidate_dashboard_cache(user_id=user_id)


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
