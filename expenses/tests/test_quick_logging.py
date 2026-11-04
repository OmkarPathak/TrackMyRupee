"""Quicker logging on a phone: a link that opens the new-expense page with a line already typed, the Share target
(Android), the paste button's markup, and the install shortcuts."""
import json
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from django.urls import reverse

from expenses.models import Expense
from expenses.views.expenses import PREFILL_MAX_LENGTH

from .test_expense_composer import ComposerBase


class PrefillLinkTests(ComposerBase):
    def open(self, **params):
        return self.client.get(reverse('expense-create'), params)

    def test_text_is_typed_into_the_quick_add_box(self):
        self.assertContains(self.open(text='swiggy 320'), 'data-prefill="swiggy 320"')

    def test_no_text_means_nothing_is_prefilled(self):
        self.assertContains(self.open(), 'data-prefill=""')
        self.assertContains(self.open(text=''), 'data-prefill=""')
        self.assertContains(self.open(text='   \n  '), 'data-prefill=""')

    def test_a_shared_message_becomes_one_line(self):
        self.assertContains(self.open(text='  Rs.320 debited\nat   SWIGGY\r\n'), 'data-prefill="Rs.320 debited at SWIGGY"')

    def test_it_is_cut_to_what_the_box_takes(self):
        page = self.open(text='a' * (PREFILL_MAX_LENGTH + 200)).content.decode()
        self.assertEqual(re.search(r'data-prefill="([^"]*)"', page).group(1), 'a' * PREFILL_MAX_LENGTH)

    def test_the_title_is_used_only_when_there_is_no_text(self):
        self.assertContains(self.open(title='uber 150'), 'data-prefill="uber 150"')
        self.assertContains(self.open(title='the title', text='the text'), 'data-prefill="the text"')

    def test_a_shared_link_is_ignored(self):
        page = self.open(url='https://shared-link.invalid/item?id=1').content.decode()
        self.assertEqual(re.search(r'data-prefill="([^"]*)"', page).group(1), '')

    def test_markup_in_the_text_cannot_break_out_of_the_page(self):
        page = self.open(text='"><script>alert(1)</script>').content.decode()
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;', page)

    def test_opening_the_link_saves_nothing(self):
        self.open(text='swiggy 320')
        self.assertEqual(Expense.objects.count(), 0)

    def test_a_signed_out_visitor_is_sent_to_sign_in_and_comes_back_with_the_text(self):
        self.client.logout()
        response = self.open(text='swiggy 320')
        self.assertEqual(response.status_code, 302)
        back = parse_qs(urlparse(response['Location']).query)['next'][0]
        self.assertEqual(urlparse(back).path, reverse('expense-create'))
        self.assertEqual(parse_qs(urlparse(back).query)['text'], ['swiggy 320'])

    def test_the_existing_entry_names_still_work(self):
        self.assertContains(self.open(source='pwa'), 'data-entry="pwa"')
        self.assertContains(self.open(), 'data-entry=""')

    def test_next_still_has_to_be_local_with_a_prefill(self):
        self.assertContains(self.open(text='x', next='https://evil.example/'), 'data-next="%s"' % reverse('expense-list'))

    def test_the_script_takes_the_text_out_of_the_address_and_never_saves_by_itself(self):
        script = open('static/js/expense_composer.js', encoding='utf-8').read()
        self.assertIn("searchParams.delete(k)", script)
        start = script.index('if (prefill) {')
        self.assertIn('parse();', script[start:])
        self.assertNotIn('attemptAdd', script[start:])        # it reads the line; the person taps Add


class ShareTargetTests(ComposerBase):
    def test_a_share_opens_the_same_page_with_its_own_entry_name(self):
        page = self.client.get(reverse('expense-share'), {'text': 'zomato 410', 'title': 'Zomato', 'url': 'https://z.invalid'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-prefill="zomato 410"')
        self.assertContains(page, 'data-entry="share"')
        self.assertContains(page, 'id="tmr-composer"')

    def test_sharing_without_words_still_opens_an_empty_form(self):
        page = self.client.get(reverse('expense-share'))
        self.assertContains(page, 'data-prefill=""')
        self.assertContains(page, 'data-entry="share"')

    def test_it_needs_a_sign_in_and_only_reads(self):
        self.assertEqual(self.client.post(reverse('expense-share'), {'text': 'x'}).status_code, 405)
        self.client.logout()
        response = self.client.get(reverse('expense-share'), {'text': 'swiggy 320'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login', response['Location'])

    def test_a_share_saves_nothing(self):
        self.client.get(reverse('expense-share'), {'text': 'zomato 410'})
        self.assertEqual(Expense.objects.count(), 0)

    def test_the_share_page_is_not_cached_for_the_next_share(self):
        first = self.client.get(reverse('expense-share'), {'text': 'one 1'})
        second = self.client.get(reverse('expense-share'), {'text': 'two 2'})
        self.assertContains(first, 'data-prefill="one 1"')
        self.assertContains(second, 'data-prefill="two 2"')


class ManifestTests(ComposerBase):
    def manifest(self):
        return json.loads(self.client.get(reverse('manifest')).content)

    def test_it_asks_to_receive_shared_text(self):
        target = self.manifest()['share_target']
        self.assertEqual(target['action'], reverse('expense-share'))
        self.assertEqual(target['method'], 'GET')
        self.assertEqual(target['params'], {'title': 'title', 'text': 'text', 'url': 'url'})

    def test_the_share_page_and_every_shortcut_are_inside_the_apps_scope(self):
        manifest = self.manifest()
        scope = manifest['scope']
        self.assertEqual(scope, '/')
        for url in [manifest['share_target']['action']] + [s['url'] for s in manifest['shortcuts']]:
            self.assertTrue(urlparse(url).path.startswith(scope), url)

    def test_there_are_shortcuts_for_expense_and_income(self):
        urls = [s['url'] for s in self.manifest()['shortcuts']]
        self.assertEqual(urls[0], reverse('expense-create') + '?source=pwa')       # still first
        self.assertIn(reverse('income-create') + '?source=pwa', urls)

    def test_every_shortcut_has_a_name_a_description_and_an_icon(self):
        for shortcut in self.manifest()['shortcuts']:
            self.assertTrue(shortcut['name'] and shortcut['short_name'] and shortcut['description'], shortcut)
            self.assertTrue(shortcut['icons'][0]['src'], shortcut)

    def test_the_launch_path_is_unchanged(self):
        self.assertEqual(self.manifest()['start_url'], reverse('home'))

    @override_settings(LANGUAGE_CODE='en')
    def test_shortcut_names_are_translated(self):
        profile = self.user.profile
        profile.language = 'hi'
        profile.save()
        names = [s['name'] for s in json.loads(self.client.get(reverse('manifest'), HTTP_ACCEPT_LANGUAGE='hi').content)['shortcuts']]
        self.assertNotIn('Add income', names)


class PasteButtonTests(ComposerBase):
    def test_the_button_is_in_the_page_but_hidden_until_the_browser_can_read_the_clipboard(self):
        page = self.client.get(reverse('expense-create')).content.decode()
        tag = page[page.index('id="tmr-paste"') - 60:page.index('id="tmr-paste"') + 260]
        self.assertIn('hidden', tag)
        self.assertIn('aria-label="Paste from clipboard"', tag)

    def test_its_messages_are_on_the_page_for_the_script(self):
        page = self.client.get(reverse('expense-create'))
        self.assertContains(page, 'data-k="pasteEmpty"')
        self.assertContains(page, 'data-k="pasteBlocked"')

    def test_the_script_reads_the_clipboard_only_when_the_button_is_tapped(self):
        script = open('static/js/expense_composer.js', encoding='utf-8').read()
        self.assertEqual(script.count('readText()'), 1)
        tap = script.index("el.paste.addEventListener('click'")
        self.assertLess(tap, script.index('readText()'))
        self.assertIn('isSecureContext', script)

    def test_the_messages_are_translated(self):
        profile = self.user.profile
        profile.language = 'hi'
        profile.save()
        page = self.client.get(reverse('expense-create'))
        self.assertNotContains(page, 'Paste from clipboard')
        self.assertContains(page, 'क्लिपबोर्ड से चिपकाएँ')


class EntryEventTests(ComposerBase):
    def send(self, event, props=None):
        return self.post_json('expense-composer-event', {'event': event, 'props': props or {}})

    def test_the_new_entry_names_are_counted_as_themselves(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            self.send('expense_form_opened', {'entry': 'share_target'})
            self.send('expense_form_opened', {'entry': 'prefill_link'})
        self.assertEqual([c.args[2] for c in capture.call_args_list], [{'entry': 'share_target'}, {'entry': 'prefill_link'}])

    def test_pasting_is_counted_without_the_text(self):
        with patch('expenses.views.composer.ph_capture') as capture:
            response = self.send('paste_used', {'text': 'swiggy 320', 'amount': '320'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(capture.call_args.args[1:], ('paste_used', {}))
