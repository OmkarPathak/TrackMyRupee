"""The "What kind of account is it?" step of the Add account page."""
import re

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import translation

from expenses.account_types import ACCOUNT_TYPES, type_categories
from expenses.models import Account, UserProfile


def current_codes():
    return [code for group, items in ACCOUNT_TYPES if group != 'Legacy' for code, _label in items]


class CategoryTests(SimpleTestCase):
    def test_every_current_type_is_in_exactly_one_category(self):
        placed = [code for c in type_categories() for code in c['codes']]
        self.assertEqual(sorted(placed), sorted(set(current_codes())), 'a type is missing from, or repeated in, the kinds')
        self.assertEqual(len(placed), len(set(placed)))

    def test_no_legacy_type_is_offered(self):
        legacy = {code for group, items in ACCOUNT_TYPES if group == 'Legacy' for code, _label in items} - set(current_codes())
        placed = {code for c in type_categories() for code in c['codes']}
        self.assertFalse(placed & legacy)

    def test_there_are_six_kinds_each_with_something_to_pick(self):
        categories = type_categories()
        self.assertEqual(len(categories), 6)
        self.assertEqual(len({c['key'] for c in categories}), 6)
        for c in categories:
            self.assertTrue(c['title'] and c['hint'] and c['icon'], c['key'])
            self.assertGreaterEqual(len(c['codes']), 2, c['key'])

    def test_the_words_are_translated(self):
        english = [(c['title'], c['hint']) for c in type_categories()]
        for lang in ('hi', 'mr'):
            with translation.override(lang):
                translated = [(c['title'], c['hint']) for c in type_categories()]
            for (en_title, en_hint), (title, hint) in zip(english, translated):
                self.assertNotEqual(title, en_title, (lang, en_title))
                self.assertNotEqual(hint, en_hint, (lang, en_hint))
                self.assertTrue(re.search('[ऀ-ॿ]', title + hint), (lang, en_title))


class AddAccountPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('chooser-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        self.client.force_login(self.user)

    def add_page(self, query=''):
        response = self.client.get(reverse('account-create') + query)
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_new_account_starts_with_the_six_kinds(self):
        response = self.add_page()
        html = response.content.decode()
        self.assertTrue(response.context['type_chooser'])
        self.assertIn('id="typeChooser"', html)
        self.assertEqual(len(re.findall(r'class="type-card" data-category="', html)), 6)
        self.assertIn('What kind of account is it?', html)
        self.assertIn('id="type-categories"', html)
        self.assertIn('js/account_type_chooser.js', html)

    def test_the_type_dropdown_is_still_in_the_page_as_the_one_source_of_truth(self):
        html = self.add_page().content.decode()
        self.assertIn('name="account_type"', html)
        self.assertIn('id="accountBasicsFields"', html)

    def test_a_type_named_in_the_address_skips_the_chooser(self):
        response = self.add_page('?type=PPF')
        self.assertFalse(response.context['type_chooser'])
        self.assertEqual(response.context['form']['account_type'].value(), 'PPF')
        self.assertNotIn('id="typeChooser"', response.content.decode())

    def test_an_unknown_or_legacy_type_in_the_address_is_ignored(self):
        for wanted in ('NOT_A_TYPE', 'BANK', ''):
            response = self.add_page(f'?type={wanted}')
            self.assertTrue(response.context['type_chooser'], wanted)

    def test_after_a_mistake_the_form_comes_back_without_the_chooser(self):
        response = self.client.post(reverse('account-create'), {'name': '', 'account_type': 'SAVINGS_ACCOUNT',
                                                                'balance': '0', 'currency': '₹'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['type_chooser'])
        self.assertNotIn('id="typeChooser"', response.content.decode())

    def test_editing_an_account_shows_its_type_not_the_chooser(self):
        account = Account.objects.create(user=self.user, name='Old', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        response = self.client.get(reverse('account-edit', kwargs={'pk': account.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="typeChooser"', response.content.decode())

    def test_choosing_a_kind_and_type_still_creates_the_account(self):
        response = self.client.post(reverse('account-create'), {
            'name': 'HDFC Savings', 'account_type': 'SAVINGS_ACCOUNT', 'balance': '2500', 'currency': '₹'})
        self.assertRedirects(response, reverse('account-list'), fetch_redirect_response=False)
        self.assertEqual(Account.objects.get(user=self.user).account_type, 'SAVINGS_ACCOUNT')

    def test_every_element_the_script_looks_for_is_in_the_page(self):
        js = open(finders.find('js/account_type_chooser.js'), encoding='utf-8').read()
        html = self.add_page().content.decode()
        for ident in set(re.findall(r"getElementById\('([^']+)'\)", js)):
            if ident in ('id_account_type', 'id_name', 'nextBtn'):
                continue          # the form's own fields and button
            self.assertIn(f'id="{ident}"', html, ident)
        for ident in ('id_account_type', 'id_name', 'nextBtn'):
            self.assertIn(f'id="{ident}"', html, ident)
