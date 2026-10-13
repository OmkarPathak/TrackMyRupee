import copyreg
import sys
import traceback
import types
from unittest import mock
from urllib.parse import parse_qs, urlparse

import requests
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

    It also stubs the public exchange-rate APIs so the suite does not depend on internet access
    (or on those services being up). Tests that patch `requests.get` themselves still override this.
    """

    _FX_HOSTS = ('api.frankfurter.app', 'api.exchangerate-api.com')
    _FX_SUPPORTED = {'INR', 'USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CAD', 'CHF', 'CNY', 'KRW', 'SGD', 'AED', 'NZD', 'HKD', 'SEK', 'NOK', 'ZAR', 'THB', 'MYR'}
    _real_requests_get = staticmethod(requests.get)

    @classmethod
    def _fake_requests_get(cls, url, *args, **kwargs):
        parsed = urlparse(url)
        if parsed.hostname not in cls._FX_HOSTS:
            return cls._real_requests_get(url, *args, **kwargs)
        query = parse_qs(parsed.query)
        to_code = query.get('to', ['INR'])[0]
        from_code = query.get('from', [parsed.path.rsplit('/', 1)[-1]])[0]
        response = mock.Mock(status_code=200)
        if from_code not in cls._FX_SUPPORTED or to_code not in cls._FX_SUPPORTED:
            # Like the real services, reject currencies they do not know.
            response.status_code = 404
            response.raise_for_status.side_effect = requests.HTTPError('404 Not Found')
            return response
        rate = 83.0 if to_code == 'INR' else 1.0
        response.raise_for_status.return_value = None
        # Frankfurter shape ({"rates": {"INR": ..}}); the fallback API uses the same "rates" key.
        response.json.return_value = {'rates': {to_code: rate, 'INR': 83.0, 'USD': 1.0, 'EUR': 0.9}}
        return response

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._fx_patch = mock.patch('requests.get', side_effect=self._fake_requests_get)
        self._fx_patch.start()

    def teardown_test_environment(self, **kwargs):
        self._fx_patch.stop()
        super().teardown_test_environment(**kwargs)
