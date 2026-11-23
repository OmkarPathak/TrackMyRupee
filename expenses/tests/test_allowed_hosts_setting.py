"""ALLOWED_HOSTS can be set from the environment (comma separated); without it the site accepts any host name, as it always has."""
import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]


def hosts(value):
    env = {k: v for k, v in os.environ.items() if k != 'ALLOWED_HOSTS'}
    env.update({'DJANGO_SETTINGS_MODULE': 'finance_tracker.settings', 'USE_SQLITE': 'True', 'SITE_URL': 'https://trackmyrupee.com'})
    if value is not None:
        env['ALLOWED_HOSTS'] = value
    out = subprocess.run([sys.executable, '-c', 'from django.conf import settings; print(",".join(settings.ALLOWED_HOSTS))'],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    return out.stdout.strip().split(',')


class AllowedHostsTests(SimpleTestCase):
    def test_without_the_setting_any_host_is_accepted_as_before(self):
        self.assertIn('*', hosts(None))

    def test_the_setting_lists_the_hosts_and_the_site_itself_is_always_one(self):
        found = hosts('trackmyrupee.com, www.trackmyrupee.com')
        self.assertEqual(sorted(found), ['trackmyrupee.com', 'www.trackmyrupee.com'])
        self.assertNotIn('*', found)

    def test_the_comment_no_longer_claims_what_the_code_does_not_do(self):
        text = (ROOT / 'finance_tracker' / 'settings.py').read_text()
        self.assertNotIn('Only our own hosts', text)
