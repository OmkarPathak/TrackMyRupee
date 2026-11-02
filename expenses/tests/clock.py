"""Pin the clock a test runs on, so it never depends on the day of the month the suite happens to run.

A test that builds data "two days ago" and then opens a page that shows "this month" breaks on the 1st and 2nd of
every month, because those dates fall in last month. Pin "today" to a day in the middle of a month instead.

``timezone.now()``, ``timezone.localtime()`` and ``timezone.localdate()`` all answer the pinned moment. Asking about
a particular moment (``localtime(some_datetime)``) still converts that moment as usual.
"""
from datetime import date, datetime, time
from unittest.mock import patch

from django.utils import timezone

# Well clear of both ends of a month, and in the past so nothing dated on it is ever "in the future"
MID_MONTH = date(2026, 10, 15)

_real_now = timezone.now
_real_localtime = timezone.localtime
_real_localdate = timezone.localdate


def _noon(day):
    return timezone.make_aware(datetime.combine(day, time(12, 0)))


def _clock(day, now, tick):
    base = now or _noon(day)
    started = _real_now()

    def current():
        # A ticking clock starts at the pinned moment and moves on, so two writes a moment apart still differ
        # (cache versions and "newer than" checks rely on that). A still clock answers the same moment every time.
        return base + (_real_now() - started) if tick else base

    def localtime(value=None, timezone=None):
        return _real_localtime(current() if value is None else value, timezone)

    def localdate(value=None, timezone=None):
        return _real_localtime(current(), timezone).date() if value is None else _real_localdate(value, timezone)

    return {'now': current, 'localtime': localtime, 'localdate': localdate}


def pin_today(day=MID_MONTH, now=None, tick=False):
    """The clock answers ``day`` (at noon, or at ``now``). Use as a decorator or a ``with`` block.
    ``tick=True`` lets it move on from there instead of standing still."""
    return patch.multiple('django.utils.timezone', **_clock(day, now, tick))


def pin_clock(test, today=MID_MONTH, now=None, tick=False):
    """Pin the clock until the test ends."""
    patcher = pin_today(today, now, tick)
    patcher.start()
    test.addCleanup(patcher.stop)
