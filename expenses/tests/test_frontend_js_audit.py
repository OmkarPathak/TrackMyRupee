import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from unittest import skipUnless

NODE = shutil.which('node')

HARNESS = r"""
const vm = require('vm');
const fs = require('fs');
const code = fs.readFileSync(process.argv[2], 'utf8');
const handlers = {};
const log = { shown: [], opened: [], focused: 0, navigated: [] };
const mkClient = (url) => ({
  url,
  focus() { log.focused++; return Promise.resolve(this); },
  navigate(u) { log.navigated.push(u); return Promise.resolve(this); },
});
let windowClients = [];
const sandbox = {
  URL, console, Promise, Date,
  self: {
    location: { origin: 'https://app.example' },
    addEventListener: (n, fn) => { handlers[n] = fn; },
    registration: { showNotification: (t, o) => { log.shown.push([t, o]); return Promise.resolve(); } },
    skipWaiting() {}, clients: {},
  },
  caches: {},
  clients: {
    matchAll: () => Promise.resolve(windowClients),
    openWindow: (u) => { log.opened.push(u); return Promise.resolve(); },
  },
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const run = async (scenario) => {
  const waits = [];
  const ev = Object.assign({ waitUntil: (p) => waits.push(p) }, scenario.event);
  if (scenario.event.notification) ev.notification = { close() {}, data: scenario.event.notification.data };
  windowClients = (scenario.clients || []).map(mkClient);
  handlers[scenario.type](ev);
  await Promise.all(waits);
};
(async () => {
  for (const sc of JSON.parse(process.argv[3])) { await run(sc); }
  console.log(JSON.stringify(log));
})();
"""


@skipUnless(NODE, 'node is not installed')
class ServiceWorkerBehaviourTest(TestCase):
    def run_sw(self, scenarios):
        js = self.client.get(reverse('service-worker')).content.decode()
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'sw.js').write_text(js)
            Path(tmp, 'h.js').write_text(HARNESS)
            out = subprocess.run([NODE, str(Path(tmp, 'h.js')), str(Path(tmp, 'sw.js')), json.dumps(scenarios)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout.strip().splitlines()[-1])

    def test_push_without_payload_still_shows_a_notification(self):
        log = self.run_sw([{'type': 'push', 'event': {}}])
        self.assertEqual(len(log['shown']), 1)
        self.assertEqual(log['shown'][0][0], 'TrackMyRupee Notification')

    def test_click_external_url_is_not_followed(self):
        log = self.run_sw([{'type': 'notificationclick',
                            'event': {'notification': {'data': {'url': 'https://evil.example/phish'}}}}])
        self.assertEqual(log['opened'], ['https://app.example/'])

    def test_click_same_origin_opens_target(self):
        log = self.run_sw([{'type': 'notificationclick',
                            'event': {'notification': {'data': {'url': '/expenses/?x=1'}}}}])
        self.assertEqual(log['opened'], ['https://app.example/expenses/?x=1'])

    def test_click_focuses_window_already_on_target(self):
        log = self.run_sw([{'type': 'notificationclick',
                            'event': {'notification': {'data': {'url': '/expenses/'}}},
                            'clients': ['https://app.example/expenses/']}])
        self.assertEqual(log['opened'], [])
        self.assertEqual(log['navigated'], [])
        self.assertEqual(log['focused'], 1)

    def test_click_navigates_other_open_window(self):
        log = self.run_sw([{'type': 'notificationclick',
                            'event': {'notification': {'data': {'url': '/goals/'}}},
                            'clients': ['https://app.example/']}])
        self.assertEqual(log['navigated'], ['https://app.example/goals/'])

    def test_click_without_data_goes_home(self):
        log = self.run_sw([{'type': 'notificationclick', 'event': {'notification': {}}}])
        self.assertEqual(log['opened'], ['https://app.example/'])

    def test_stale_duplicate_worker_file_is_gone(self):
        self.assertFalse(Path(settings.BASE_DIR, 'static', 'js', 'service-worker.js').exists())


class EscapingSourceTest(TestCase):
    """Guards against raw interpolation of user-entered labels in innerHTML templates."""

    def read(self, *parts):
        return Path(settings.BASE_DIR, *parts).read_text()

    def test_filter_toolbar_escapes_option_labels_and_values(self):
        js = self.read('static', 'js', 'tmr_filter.js')
        self.assertIn('function escapeHtml', js)
        for raw in ('${opt.label}', '${opt.value}', '${labelText}', '${valText}', '${f.label}', '${opt.key}'):
            self.assertNotIn(raw, js, raw)

    def test_home_tooltip_escapes_labels(self):
        html = self.read('templates', 'home.html')
        for raw in ('tooltip-label">${label}', 'tooltip-label">${ev.subtype}', 'tooltip-header">${title}'):
            self.assertNotIn(raw, html, raw)

    def test_fund_search_escapes_scheme_name(self):
        html = self.read('templates', 'expenses', 'holding_list.html')
        self.assertNotIn('">${item.scheme_name}', html)


@skipUnless(NODE, 'node is not installed')
class FilterEscapeHelperTest(TestCase):
    def test_escape_html_function(self):
        js = Path(settings.BASE_DIR, 'static', 'js', 'tmr_filter.js').read_text()
        fn = re.search(r'function escapeHtml\(value\) \{.*?\n\}', js, re.S).group(0)
        script = fn + "\nconsole.log(escapeHtml('<img src=x onerror=\"a\">&\\''));"
        out = subprocess.run([NODE, '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(out.stdout.strip(), "&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;")
