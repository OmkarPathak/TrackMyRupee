"""Daily 9 pm reminder opt-in: where it appears and what the browser-side logic decides."""
import json
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

JS = Path(settings.BASE_DIR, 'static/js/reminder_optin.js')
STATES = ['ask', 'on', 'blocked', 'ios-install', 'unsupported']


def run_js(expression):
    """Evaluate an expression against the script's pure logic with node."""
    code = f"const L = require({json.dumps(str(JS))}); console.log(JSON.stringify({expression}));"
    out = subprocess.run(['node', '-e', code], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


class ReminderPlacementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('remind-me', 'remind@example.com', 'pw')
        profile = self.user.profile
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        self.client.force_login(self.user)

    def test_dashboard_has_the_card_with_every_state(self):
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('data-reminder-optin data-variant="card"', html)
        for state in STATES:
            self.assertIn(f'data-reminder-when="{state}"', html)

    def test_demo_user_is_not_asked(self):
        demo = User.objects.create_user('demo', 'demo@example.com', 'pw')
        demo.profile.consent_granted = True
        demo.profile.has_seen_tutorial = True
        demo.profile.save()
        self.client.force_login(demo)
        self.assertNotIn('data-reminder-optin', self.client.get(reverse('home')).content.decode())

    def test_settings_shows_status_and_no_longer_says_install_is_required(self):
        html = self.client.get(reverse('profile-settings')).content.decode()
        self.assertIn('data-reminder-optin data-variant="settings"', html)
        self.assertNotIn('Requires App Installation', html)
        self.assertIn('9 pm', html)

    def test_onboarding_final_step_offers_the_reminder(self):
        profile = self.user.profile
        profile.has_seen_tutorial = False
        profile.save()
        html = self.client.get(reverse('onboarding')).content.decode()
        self.assertIn('data-reminder-optin data-variant="onboarding"', html)

    def test_page_loads_the_script_and_the_save_url(self):
        html = self.client.get(reverse('home')).content.decode()
        self.assertRegex(html, r'src="[^"]*js/reminder_optin[^"]*\.js"')
        self.assertIn(f'window.TMR_SAVE_PUSH_URL = "{reverse("save_webpush_info")}"', html)

    def test_the_prompt_only_follows_a_tap(self):
        """The card never starts the browser prompt itself: it needs a button with the enable hook."""
        html = self.client.get(reverse('home')).content.decode()
        self.assertIn('data-reminder-enable', html)
        source = JS.read_text()
        self.assertEqual(source.count('requestPermission('), 1)
        self.assertIn("event.target.closest('[data-reminder-enable]')", source)

    def test_script_handles_every_state_the_template_shows(self):
        source = JS.read_text()
        for state in STATES:
            self.assertIn(f"'{state}'", source)


class ReminderLogicTests(TestCase):
    def setUp(self):
        if not shutil.which('node'):
            self.skipTest('node is not installed')

    def state(self, **env):
        base = {'supported': True, 'ios': False, 'standalone': False, 'permission': 'default', 'serverSubscribed': False}
        base.update(env)
        return run_js(f'L.computeState({json.dumps(base)})')

    def test_can_subscribe(self):
        self.assertEqual(self.state(), 'ask')

    def test_already_subscribed_anywhere(self):
        self.assertEqual(self.state(serverSubscribed=True), 'on')
        self.assertEqual(self.state(serverSubscribed=True, permission='granted'), 'on')

    def test_granted_here_but_not_saved_asks_again_without_a_prompt(self):
        self.assertEqual(self.state(permission='granted'), 'ask')

    def test_blocked(self):
        self.assertEqual(self.state(permission='denied'), 'blocked')
        self.assertEqual(self.state(permission='denied', serverSubscribed=True), 'blocked')

    def test_iphone_browser_must_install_first(self):
        self.assertEqual(self.state(supported=False, ios=True), 'ios-install')

    def test_installed_iphone_without_support_is_unsupported_not_install(self):
        self.assertEqual(self.state(supported=False, ios=True, standalone=True), 'unsupported')

    def test_other_unsupported_browsers(self):
        self.assertEqual(self.state(supported=False), 'unsupported')

    def test_dismissals_back_off_then_stop(self):
        day = 24 * 60 * 60 * 1000
        self.assertTrue(run_js('L.nextAskAllowed(null, 0)'))
        waits = run_js('L.DISMISS_WAITS_DAYS')
        self.assertEqual(waits, [3, 7, 14, 30])
        record = run_js('L.recordDismissal(null, 1000)')
        self.assertEqual(record, {'n': 1, 'until': 1000 + 3 * day})
        self.assertFalse(run_js(f'L.nextAskAllowed({json.dumps(record)}, {1000 + 2 * day})'))
        self.assertTrue(run_js(f'L.nextAskAllowed({json.dumps(record)}, {1000 + 3 * day})'))
        record = run_js('L.recordDismissal({"n": 3, "until": 0}, 0)')
        self.assertEqual(record['n'], 4)
        self.assertFalse(run_js(f'L.nextAskAllowed({json.dumps(record)}, {10 ** 15})'))
