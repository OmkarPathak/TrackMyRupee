"""The Hindi and Marathi catalogues stay complete and consistent, and translated pages render."""
import re
import unittest
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from expenses import holdings_service as hs
from expenses.models import Account, Transfer, UserProfile

LOCALE = Path(settings.BASE_DIR) / 'locale'


def compile_catalogues():
    """The .mo files are build products (git-ignored, compiled by the entrypoint): make sure they exist and
    match the .po files, or skip the tests that read them when gettext is not installed."""
    try:
        call_command('compilemessages', locale=['hi', 'mr'], ignore_patterns=['env/*', 'node_modules/*'], verbosity=0)
    except CommandError as exc:
        raise unittest.SkipTest(f'gettext tools are not installed: {exc}')
PLACEHOLDER = re.compile(r'%\([^)]*\)[sdifrx]|%[sdifrx]')
DEVANAGARI = re.compile(r'[ऀ-ॿ]')


def parse_po(path):
    """[(msgid, msgid_plural, [msgstr...], flags)] without the header; enough of the format for these checks."""
    entries, cur, field = [], None, None
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if line.startswith('#,'):
            cur = cur or {'id': '', 'plural': '', 'str': {}, 'flags': ''}
            cur['flags'] += line
            continue
        if line.startswith('msgctxt') or line.startswith('#'):
            continue
        m = re.match(r'(msgid_plural|msgid|msgstr(?:\[(\d)\])?)\s+"(.*)"$', line)
        if m:
            key = m.group(1)
            if key == 'msgid':
                if cur and (cur['id'] or cur['str']):
                    entries.append(cur)
                cur = {'id': '', 'plural': '', 'str': {}, 'flags': cur['flags'] if cur and not (cur['id'] or cur['str']) else ''}
                field = ('id',)
            elif key == 'msgid_plural':
                field = ('plural',)
            else:
                field = ('str', int(m.group(2) or 0))
                cur['str'][field[1]] = ''
            _append(cur, field, m.group(3))
        elif line.startswith('"') and cur is not None and field:
            _append(cur, field, line[1:-1])
        elif not line:
            continue
    if cur and (cur['id'] or cur['str']):
        entries.append(cur)
    return [e for e in entries if e['id']]


def _append(cur, field, text):
    text = text.replace('\\"', '"').replace('\\n', '\n')
    if field[0] == 'str':
        cur['str'][field[1]] += text
    else:
        cur[field[0]] += text


class CatalogueHealthTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        compile_catalogues()

    def catalogue(self, lang, domain):
        return parse_po(LOCALE / lang / 'LC_MESSAGES' / f'{domain}.po')

    def test_every_string_is_translated_and_none_is_fuzzy(self):
        for lang in ('hi', 'mr'):
            for domain in ('django', 'djangojs'):
                entries = self.catalogue(lang, domain)
                self.assertGreater(len(entries), 50, (lang, domain))
                missing = [e['id'][:60] for e in entries if not all(v.strip() for v in e['str'].values())]
                fuzzy = [e['id'][:60] for e in entries if 'fuzzy' in e['flags']]
                self.assertEqual((missing[:5], fuzzy[:5]), ([], []), f'{lang} {domain}')

    def test_placeholders_survive_translation(self):
        for lang in ('hi', 'mr'):
            for domain in ('django', 'djangojs'):
                for e in self.catalogue(lang, domain):
                    expected = sorted(PLACEHOLDER.findall(e['id']))
                    for value in e['str'].values():
                        if e['plural']:
                            continue
                        self.assertEqual(sorted(PLACEHOLDER.findall(value)), expected, f"{lang}: {e['id'][:60]}")

    def test_translations_are_in_the_right_script(self):
        for lang in ('hi', 'mr'):
            long_ones = [e for e in self.catalogue(lang, 'django') if len(e['id']) > 40 and re.search(r'[a-z]{4}', e['id'])]
            untranslated_looking = [e['id'][:50] for e in long_ones
                                    if not DEVANAGARI.search(e['str'].get(0, ''))]
            self.assertLessEqual(len(untranslated_looking), 12, (lang, untranslated_looking[:5]))

    def test_compiled_catalogues_are_up_to_date(self):
        """The .mo files (what the app really reads) carry the strings of the .po files."""
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                for text in ('Uninvested cash:', 'Buy more', 'Waiting for NAV'):
                    self.assertTrue(DEVANAGARI.search(translation.gettext(text)), (lang, text))


class TranslatedPagesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        compile_catalogues()

    def setUp(self):
        self.user = User.objects.create_user(username='polyglot', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.profile = self.user.profile
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('10000'))
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=Decimal('0'))
        self.fund = hs.create_holding(self.funds, name='Alpha', units='10', price='100', scheme_code='1')

    def pages(self):
        return [reverse('holding-list'), reverse('holding-detail', args=[self.fund.pk]),
                reverse('transfer-create'), reverse('recurring-create'), reverse('account-detail', args=[self.funds.uuid]),
                reverse('account-list')]

    def test_pages_render_in_hindi_and_marathi(self):
        for lang in ('hi', 'mr'):
            self.profile.language = lang
            self.profile.save()
            for url in self.pages():
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200, (lang, url))
                self.assertRegex(response.content.decode(), DEVANAGARI.pattern, (lang, url))

    def test_the_javascript_catalogue_is_translated(self):
        for lang, expected in (('hi', 'लागू करें'), ('mr', 'लागू करा')):
            self.profile.language = lang
            self.profile.save()
            response = self.client.get(reverse('tmr-javascript-catalog'))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(expected.encode('unicode_escape').decode() in response.content.decode())      # the catalogue is JS-escaped

    def test_the_catalogue_is_requested_per_language_and_cached_by_the_browser(self):
        page = self.client.get(self.pages()[0]).content.decode()
        self.assertIn('src="/jsi18n/?l=', page)
        response = self.client.get('/jsi18n/?l=en')
        self.assertIn('max-age=86400', response['Cache-Control'])
