import json
import re
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

User = get_user_model()

POLICY_PAGES = ('privacy-policy', 'terms-of-service', 'refund-policy', 'security', 'about')


class PolicyPageCachingTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_signed_in_users_never_get_a_cached_copy(self):
        user = User.objects.create_user('p1', 'p1@example.com', 'pw12345!x')
        self.client.force_login(user)
        first = self.client.get(reverse('privacy-policy'))
        user.username  # noqa: B018
        self.assertEqual(first.status_code, 200)
        # Nothing was stored for the signed-in request, so the anonymous visitor builds their own page
        self.assertNotIn('p1', self.client_class().get(reverse('privacy-policy')).content.decode())

    def test_anonymous_visitors_are_still_cached(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for name in POLICY_PAGES:
            self.client_class().get(reverse(name))
            with CaptureQueriesContext(connection) as ctx:
                response = self.client_class().get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            self.assertEqual(len(ctx), 0, name)


class DemoLoginTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_visitors_after_the_first_do_not_reseed(self):
        calls = []

        def fake_setup(name, *a, **kw):
            if name != 'setup_demo_user':
                return
            calls.append(1)
            from datetime import date

            from expenses.models import Account, Expense, Income
            user, _ = User.objects.get_or_create(username='demo')
            acc = Account.objects.create(user=user, name='A', account_type='CASH_WALLET', balance=0)
            Expense.objects.create(user=user, date=date.today(), amount=1, category='Food', account=acc)
            Income.objects.create(user=user, date=date.today(), amount=1, source='x', account=acc)

        with patch('expenses.views.auth.call_command', side_effect=fake_setup):
            for _ in range(3):
                response = self.client_class().get(reverse('demo_login'))
                self.assertEqual(response.status_code, 302)
        self.assertEqual(len(calls), 1)

    def test_demo_account_has_no_usable_password(self):
        from django.core.management import call_command
        call_command('setup_demo_user')
        demo = User.objects.get(username='demo')
        self.assertFalse(demo.has_usable_password())
        self.assertFalse(self.client.login(username='demo', password='demo_password_123'))

    def test_demo_link_still_logs_in(self):
        from django.core.management import call_command
        call_command('setup_demo_user')
        response = self.client.get(reverse('demo_login'))
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)


class PublicPagesTest(TestCase):
    def test_landing_does_not_advertise_a_missing_resource(self):
        response = self.client.get(reverse('landing'))
        for target in re.findall(r'<([^>]+)>', response['Link']):
            if target.startswith('/'):
                self.assertEqual(self.client.get(target).status_code, 200, target)

    def test_sitemap_pages_all_render(self):
        from finance_tracker.sitemaps import StaticViewSitemap
        for name in StaticViewSitemap().items():
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)  # no redirects or side-effect URLs in a sitemap

    def test_internal_links_on_public_pages_resolve(self):
        pages = ['landing', 'features', 'about', 'contact', 'privacy-policy', 'terms-of-service',
                 'refund-policy', 'security', 'axio-alternative', 'walnut-alternative-no-sms',
                 'indmoney-alternative-privacy', 'loan-emi-calculator']
        broken = []
        for name in pages:
            html = self.client.get(reverse(name)).content.decode()
            for href in set(re.findall(r'href="(/[^"#?]*)', html)):
                if href.startswith(('/static/', '/media/', '//')):
                    continue
                if self.client.get(href).status_code == 404:
                    broken.append((name, href))
        self.assertEqual(broken, [])

    def test_doh_json_survives_odd_input(self):
        url = reverse('dns-query-wellknown')
        response = self.client.post(url, data=json.dumps({'name': 123, 'type': 5}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        response = self.client.get(url, {'name': '_x._agents.example.com', 'type': 'SVCB'})
        self.assertEqual(response.status_code, 200)


class TranslationFilesTest(TestCase):
    """Active (non-fuzzy) translations must keep the placeholders and tags of the English text,
    otherwise formatting the message raises KeyError/TypeError and the page returns a 500."""

    TOKEN = re.compile(r'\{[a-zA-Z_0-9]*\}|%\([a-zA-Z_]+\)[sdif]|%[sd]')
    TAG = re.compile(r'</?[a-zA-Z][^>]*>')

    def test_placeholders_and_tags_match(self):
        try:
            import polib
        except ImportError:
            self.skipTest('polib is not installed')
        problems = []
        for lang in ('hi', 'mr'):
            po = polib.pofile(str(Path(settings.BASE_DIR, 'locale', lang, 'LC_MESSAGES', 'django.po')))
            for entry in po:
                if entry.obsolete or 'fuzzy' in entry.flags or not entry.msgstr:
                    continue
                if sorted(self.TOKEN.findall(entry.msgid)) != sorted(self.TOKEN.findall(entry.msgstr)):
                    problems.append((lang, entry.msgid[:60]))
                if sorted(self.TAG.findall(entry.msgid)) != sorted(self.TAG.findall(entry.msgstr)):
                    problems.append((lang, 'tags', entry.msgid[:60]))
        self.assertEqual(problems, [])
