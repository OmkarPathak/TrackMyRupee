"""Optional Server-Timing header: total time, DB time and query count per request.

Disabled unless settings.ENABLE_SERVER_TIMING is true (Django then skips the middleware
entirely, so the disabled cost is zero). Exposes durations and a count only - never SQL,
parameters, user data or cache keys.

State is per-request and lives in locals (the DB wrapper is installed per-thread via
connection.execute_wrapper), so concurrent gthread requests cannot mix counters.
"""
import time

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.db import connection


class ServerTimingMiddleware:
    def __init__(self, get_response):
        if not getattr(settings, 'ENABLE_SERVER_TIMING', False):
            raise MiddlewareNotUsed
        self.get_response = get_response

    def __call__(self, request):
        stats = {'db': 0.0, 'queries': 0}

        def wrapper(execute, sql, params, many, context):
            start = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                stats['db'] += time.perf_counter() - start
                stats['queries'] += 1

        start = time.perf_counter()
        with connection.execute_wrapper(wrapper):
            response = self.get_response(request)
        total = time.perf_counter() - start
        response['Server-Timing'] = (
            f'total;dur={total * 1000:.1f}, db;dur={stats["db"] * 1000:.1f}, '
            f'queries;desc="{stats["queries"]}"'
        )
        return response
