"""Optional Server-Timing header: total time, DB time and query count per request.

Disabled unless settings.ENABLE_SERVER_TIMING is true (Django then skips the middleware
entirely, so the disabled cost is zero). Exposes durations and a count only - never SQL,
parameters, user data or cache keys.

Optional slow-query log: set the SLOW_QUERY_LOG_MS env var (e.g. 40) and each query at or above
that many ms is logged at WARNING on the `finance_tracker.slow_query` logger with its duration, the
first project file:line that issued it, and the SQL *template* (placeholders only, never parameter
values). Use it to find the real hot spots of a cold dashboard render in production.

State is per-request and lives in locals (the DB wrapper is installed per-thread via
connection.execute_wrapper), so concurrent gthread requests cannot mix counters.
"""
import logging
import os
import time
import traceback

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.db import connection

slow_query_logger = logging.getLogger('finance_tracker.slow_query')


def _slow_query_threshold_ms():
    try:
        return float(os.environ.get('SLOW_QUERY_LOG_MS', '') or 0)
    except ValueError:
        return 0.0


def _project_call_site():
    root = str(settings.BASE_DIR)
    for frame in reversed(traceback.extract_stack()):
        if frame.filename.startswith(root) and '/env/' not in frame.filename and 'server_timing' not in frame.filename:
            return f'{os.path.relpath(frame.filename, root)}:{frame.lineno}'
    return '?'


class ServerTimingMiddleware:
    def __init__(self, get_response):
        if not getattr(settings, 'ENABLE_SERVER_TIMING', False):
            raise MiddlewareNotUsed
        self.get_response = get_response
        self.slow_ms = _slow_query_threshold_ms()

    def __call__(self, request):
        stats = {'db': 0.0, 'queries': 0}

        def wrapper(execute, sql, params, many, context):
            start = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                elapsed = time.perf_counter() - start
                stats['db'] += elapsed
                stats['queries'] += 1
                if self.slow_ms and elapsed * 1000 >= self.slow_ms:
                    slow_query_logger.warning(
                        'slow_query ms=%.1f at=%s path=%s sql=%s',
                        elapsed * 1000, _project_call_site(), request.path, ' '.join(str(sql).split())[:300],
                    )

        start = time.perf_counter()
        with connection.execute_wrapper(wrapper):
            response = self.get_response(request)
        total = time.perf_counter() - start
        response['Server-Timing'] = (
            f'total;dur={total * 1000:.1f}, db;dur={stats["db"] * 1000:.1f}, '
            f'queries;desc="{stats["queries"]}"'
        )
        return response
