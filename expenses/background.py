"""Small jobs that should not hold up a response.

`run_after_response(fn, *args)` runs `fn` on a short-lived thread once the request has gone, so a save does not wait
for housekeeping nobody is waiting for (learning a keyword, warming a cache). The thread gets its own database
connection and closes it. Only a few run at once per process; when they are all busy (and under test) the job runs
right away instead, so it is never dropped.
"""
import logging
import threading

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

_slots = threading.BoundedSemaphore(4)


def run_after_response(fn, *args, name='after-response', **kwargs) -> None:
    if not getattr(settings, 'BACKGROUND_TASKS_ENABLED', True) or not _slots.acquire(blocking=False):
        _run(fn, args, kwargs, name)
        return
    threading.Thread(target=_run_in_thread, args=(fn, args, kwargs, name), daemon=True, name=name).start()


def _run(fn, args, kwargs, name) -> None:
    try:
        fn(*args, **kwargs)
    except Exception as exc:       # noqa: BLE001 - nobody is waiting: log the class only
        logger.warning('background job %s failed type=%s', name, type(exc).__name__)


def _run_in_thread(fn, args, kwargs, name) -> None:
    try:
        _run(fn, args, kwargs, name)
    finally:
        connection.close()
        _slots.release()
