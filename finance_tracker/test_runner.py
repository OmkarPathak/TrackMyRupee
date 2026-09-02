import copyreg
import sys
import traceback
import types
from django.test.runner import DiscoverRunner


def _clean_exception(exc):
    """Recursively strip unpicklable __traceback__, __context__, and __cause__ objects."""
    if exc is None:
        return
    try:
        exc.__traceback__ = None
    except Exception:
        pass

    ctx = getattr(exc, '__context__', None)
    if ctx is not None and ctx is not exc:
        try:
            _clean_exception(ctx)
        except Exception:
            pass
        try:
            exc.__context__ = None
        except Exception:
            pass

    cause = getattr(exc, '__cause__', None)
    if cause is not None and cause is not exc:
        try:
            _clean_exception(cause)
        except Exception:
            pass
        try:
            exc.__cause__ = None
        except Exception:
            pass


def _reduce_exception(exc):
    """PEP 307 reducer that allows pickle to serialize any Exception across multiprocessing worker processes."""
    _clean_exception(exc)
    return (exc.__class__, getattr(exc, 'args', ()))


def _reduce_traceback(tb):
    """Convert raw traceback objects to formatted string lines for IPC pickling."""
    formatted = ''.join(traceback.format_tb(tb))
    return (str, (formatted,))


def _reduce_frame(frame):
    """Convert raw frame objects to string representation for IPC pickling."""
    return (str, (f'<frame {frame.f_code.co_name} at {frame.f_code.co_filename}:{frame.f_lineno}>',))


copyreg.pickle(Exception, _reduce_exception)
copyreg.pickle(BaseException, _reduce_exception)
copyreg.pickle(types.TracebackType, _reduce_traceback)
copyreg.pickle(types.FrameType, _reduce_frame)


class ParallelTestRunner(DiscoverRunner):
    """
    Custom Django test runner with robust multiprocessing Exception, Traceback, and Frame pickling support
    for parallel test execution (`--parallel`) on Python 3.9 / macOS.
    """
    pass
