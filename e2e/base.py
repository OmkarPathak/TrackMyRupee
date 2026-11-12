"""Foundations for the browser tests.

These drive a real Chromium against Django's live test server (its own throwaway database), so they catch what
unit tests cannot: a script that throws, a button that does nothing, a page that renders but cannot be used.

They are slow compared with the unit tests, so they only run when asked (RUN_E2E=1): see e2e/README.md.
"""
import os
import unittest
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

# Playwright's sync API runs an event loop on this thread; Django then refuses ORM calls made from it
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

from django.conf import settings  # noqa: E402
from django.contrib.auth.models import User  # noqa: E402
from django.contrib.staticfiles.testing import StaticLiveServerTestCase  # noqa: E402
from django.test import Client  # noqa: E402
from django.utils import timezone  # noqa: E402

ENABLED = os.environ.get('RUN_E2E') == '1'
ARTIFACTS = Path(__file__).resolve().parent / 'artifacts'

# Uncaught script errors that come from the sandbox or a third party, not from the app (blocked CDNs and so on)
IGNORED_ERRORS = ('driver is not defined', 'Driver is not defined', 'confetti is not defined')   # (libraries loaded from a CDN)

PHONE = {'viewport': {'width': 390, 'height': 844}, 'device_scale_factor': 2, 'is_mobile': True, 'has_touch': True}
DESKTOP = {'viewport': {'width': 1280, 'height': 800}}


def make_user(name='e2e-user', *, currency='₹', language='en', tour_pending=False):
    """A signed-in-able Pro user who has finished the tutorial and the consent step."""
    from expenses.models import UserProfile
    user = User.objects.create_user(name, f'{name}@example.com', 'pass12345!')
    UserProfile.objects.filter(user=user).update(
        tier='PRO', subscription_end_date=timezone.now() + timedelta(days=30), currency=currency, language=language,
        has_seen_tutorial=True, consent_granted=True, app_tour_pending=tour_pending)
    return User.objects.get(pk=user.pk)       # (a fresh copy: a stale cached profile would be saved back on sign-in)


def make_account(user, name, kind='SAVINGS_ACCOUNT', balance=100000, **extra):
    from expenses.models import Account
    return Account.objects.create(user=user, name=name, account_type=kind, balance=Decimal(balance), currency='₹', **extra)


@unittest.skipUnless(ENABLED, 'browser tests: set RUN_E2E=1 (see e2e/README.md)')
class E2ETestCase(StaticLiveServerTestCase):
    host = '127.0.0.1'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from playwright.sync_api import sync_playwright
        cls._playwright = sync_playwright().start()
        launch = {'args': ['--no-sandbox'] if os.geteuid() == 0 else []}      # (containers run as root)
        if os.environ.get('E2E_CHROMIUM_PATH'):
            launch['executable_path'] = os.environ['E2E_CHROMIUM_PATH']
        cls.browser = cls._playwright.chromium.launch(**launch)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self._contexts = []
        self.js_errors = []
        self.server_errors = []

    def tearDown(self):
        if self._test_failed():
            self._save_screenshots()
        for context in self._contexts:
            context.close()
        super().tearDown()
        # an uncaught script error, or a request the app answered with a server error, is a bug even when the page
        # looked right
        errors = [e for e in self.js_errors if not any(ignored in e for ignored in IGNORED_ERRORS)]
        if errors and not self._test_failed():
            self.fail('The page threw script errors:\n' + '\n'.join(errors))
        if self.server_errors and not self._test_failed():
            self.fail('The app answered with a server error:\n' + '\n'.join(self.server_errors))

    def _test_failed(self):
        outcome = getattr(self, '_outcome', None)
        result = getattr(outcome, 'result', None)
        if result is None:
            return False
        for failed, _traceback in list(result.failures) + list(result.errors):
            if failed is self:
                return True
        return False

    def _save_screenshots(self):
        ARTIFACTS.mkdir(exist_ok=True)
        for number, context in enumerate(self._contexts):
            for page_number, page in enumerate(context.pages):
                try:
                    page.screenshot(path=str(ARTIFACTS / f'{self.id()}-{number}-{page_number}.png'), full_page=True)
                except Exception:       # a page that has already closed
                    pass

    # ── helpers ────────────────────────────────────────────────────────────

    def new_page(self, user=None, *, phone=False):
        """A page in its own browser context, signed in as `user` (without going through the sign-in form)."""
        context = self.browser.new_context(**(PHONE if phone else DESKTOP))
        context.set_default_timeout(15000)
        # no cookie banner over the page
        context.add_init_script("try { localStorage.setItem('cookieConsent', 'declined'); } catch (e) {}")
        self._contexts.append(context)
        self._serve_cdn_files(context)
        if user is not None:
            client = Client()
            client.force_login(user)
            cookie = client.cookies[settings.SESSION_COOKIE_NAME]
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': cookie.value, 'url': self.live_server_url}])
        page = context.new_page()
        page.on('pageerror', lambda error: self.js_errors.append(str(error)))
        page.on('response', self._note_server_error)
        return page

    def _note_server_error(self, response):
        if response.status >= 500 and response.url.startswith(self.live_server_url):
            self.server_errors.append(f'{response.status} {response.request.method} {response.url}')

    def _serve_cdn_files(self, context):
        """Where the network is blocked, serve the CDN files some pages need (the tour) from a folder instead.

        Set E2E_CDN_DIR to a folder holding them (for driver.js: driver.js.iife.js and driver.css). Without it the
        pages use the real CDN, as in CI.
        """
        folder = os.environ.get('E2E_CDN_DIR')
        if not folder:
            return

        def serve(route):
            name = route.request.url.split('?')[0].rsplit('/', 1)[-1]
            local = Path(folder) / name
            if local.exists():
                route.fulfill(path=str(local))
            else:
                route.continue_()
        context.route('https://cdn.jsdelivr.net/**', serve)

    def open(self, page, path):
        """Go to a path on the live server; the status must be a success."""
        # (not "wait for the network to go quiet": a page may hold requests open, so tests wait for what they need)
        response = page.goto(self.live_server_url + path, wait_until='load')
        self.assertIsNotNone(response, path)
        self.assertLess(response.status, 400, f'{path} answered {response.status}')
        return response

    def set_select(self, page, selector, value):
        """Choose an option of a dropdown the app has restyled (the real <select> is hidden)."""
        page.evaluate(
            """([selector, value]) => { const s = document.querySelector(selector); s.value = value;
               s.dispatchEvent(new Event('change', { bubbles: true })); }""", [selector, value])

    def wait_until(self, check, message='the condition was never met', seconds=10):
        """Poll `check()` (it may read the database) until it is true: the app saves a moment after a click."""
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if check():
                return
            time.sleep(0.2)
        self.fail(message)
