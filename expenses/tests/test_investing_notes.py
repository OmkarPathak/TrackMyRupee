"""The notes that teach how investing, maturity and renewals count."""
import json
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('invest-notes', password='pass12345!')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.today = timezone.localdate()
        self.bank = self.account('ICICI Savings', 'SAVINGS_ACCOUNT')
        self.fd = self.account('ICICI FD', 'FD')
        # a Pro plan that lasts (signing in downgrades Pro without an end date), so the deposits below are not locked
        UserProfile.objects.filter(user=self.user).update(tier='PRO', subscription_end_date=timezone.now() + timedelta(days=30))
        self.user = User.objects.get(pk=self.user.pk)       # (a stale cached profile would be saved back on sign-in)
        self.client.force_login(self.user)

    def account(self, name, kind, **extra):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D('100000'), currency='₹', **extra)

    def set_language(self, lang):
        UserProfile.objects.filter(user=self.user).update(language=lang)


class MoveMoneyNoteTests(Base):
    def page(self):
        response = self.client.get(reverse('transfer-create'))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_page_knows_which_accounts_are_investments(self):
        html = self.page().content.decode()
        ids = json.loads(re.search(r'data-investment-ids="([^"]*)"', html).group(1).replace('&quot;', '"'))
        self.assertEqual(ids, [self.fd.pk])

    def test_the_three_notes_are_in_the_page(self):
        html = self.page().content.decode()
        self.assertIn('id="transferInvestNote"', html)
        for text in ('This counts as <b>money invested</b>.', '<b>lowers Invested</b>',
                     'is <b>not counted as new investing</b>'):
            self.assertIn(text, html)

    def test_every_element_the_note_script_looks_for_is_in_the_page(self):
        html = self.page().content.decode()
        for ident in ('transferInvestNote', 'id_from_account', 'id_to_account'):
            self.assertIn(f'id="{ident}"', html, ident)
        self.assertIn('tmr-invest-note__icon', html)
        self.assertIn('tmr-invest-note__text', html)

    def test_the_notes_are_translated(self):
        for lang, marker in (('hi', 'निवेश'), ('mr', 'गुंतवणूक')):
            self.set_language(lang)
            html = self.page().content.decode()
            note = re.search(r'id="transferInvestNote".*?</div>', html, re.S).group(0)
            self.assertNotIn('money invested', note, lang)
            self.assertNotIn('lowers Invested', note, lang)
            self.assertIn(marker, note, lang)


class MaturityCardTests(Base):
    def detail(self, account):
        response = self.client.get(reverse('account-detail', kwargs={'pk': account.uuid}))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def deposit(self, days=None, **extra):
        when = self.today + timedelta(days=days) if days is not None else None
        self.made = getattr(self, 'made', 0) + 1
        return self.account(f'FD {self.made}', 'FD', deposit_maturity_date=when, **extra)

    def test_days_to_maturity(self):
        self.assertEqual(self.deposit(5).days_to_maturity, 5)
        self.assertEqual(self.deposit(-2).days_to_maturity, -2)
        self.assertIsNone(self.fd.days_to_maturity)                                     # no date set
        self.assertIsNone(self.bank.days_to_maturity)                                   # not a deposit
        self.assertIsNone(self.deposit(5, deposit_closed_date=self.today).days_to_maturity)

    def test_the_card_opens_with_the_steps_when_maturity_is_near(self):
        html = self.detail(self.deposit(3))
        card = re.search(r'<details class="tmr-maturity[^>]*>', html).group(0)
        self.assertIn(' open', card)
        for text in ('When it matures', 'Matures in 3 days', 'Add the interest you earned', 'Move the money out',
                     "doesn't count as new investing"):
            self.assertIn(text, html)

    def test_a_matured_deposit_says_so(self):
        html = self.detail(self.deposit(-1))
        self.assertIn('Matured', html)
        self.assertIn(' open', re.search(r'<details class="tmr-maturity[^>]*>', html).group(0))

    def test_the_card_is_folded_away_when_maturity_is_far(self):
        html = self.detail(self.deposit(60))
        self.assertNotIn(' open', re.search(r'<details class="tmr-maturity[^>]*>', html).group(0))
        self.assertIn('What happens when it matures?', html)
        self.assertNotIn('Matures in', html)

    def test_it_is_there_without_a_maturity_date_too(self):
        self.assertIn('id="maturity-card"', self.detail(self.fd))

    def test_the_buttons_open_the_right_forms(self):
        html = self.detail(self.deposit(3))
        fd = Account.objects.get(name='FD 1')
        self.assertIn(f'{reverse("income-create")}?account={fd.uuid}&amp;source_type=Investment%20Returns', html)
        self.assertIn(f'{reverse("transfer-create")}?from_account={fd.uuid}', html)

    def test_not_on_accounts_that_do_not_mature(self):
        self.assertNotIn('id="maturity-card"', self.detail(self.bank))
        self.assertNotIn('id="maturity-card"', self.detail(self.account('Funds', 'MUTUAL_FUND')))

    def test_not_on_a_closed_deposit(self):
        self.assertNotIn('id="maturity-card"', self.detail(self.deposit(3, deposit_closed_date=self.today)))

    def test_the_card_is_translated(self):
        self.set_language('hi')
        html = self.detail(self.deposit(3))
        self.assertNotIn('Add the interest you earned', html)
        self.assertNotIn('Matures in', html)
        self.assertIn('परिपक्व', html)
        self.set_language('mr')
        self.assertIn('मॅच्युर', self.detail(self.deposit(3)))


class AddInterestLinkTests(Base):
    def form(self, query):
        response = self.client.get(reverse('income-create') + query)
        self.assertEqual(response.status_code, 200)
        return response.context['form']

    def test_the_form_opens_on_that_account_as_interest(self):
        form = self.form(f'?account={self.fd.uuid}&source_type=Investment%20Returns')
        self.assertEqual(form['account'].value(), self.fd.pk)
        self.assertEqual(form['source_type'].value(), 'Investment Returns')
        self.assertEqual(form['description'].value(), 'Interest earned on ICICI FD')

    def test_someone_elses_account_is_ignored(self):
        other = User.objects.create_user('someone-else', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='FD', balance=D('1'), currency='₹')
        form = self.form(f'?account={theirs.uuid}&source_type=Investment%20Returns')
        self.assertNotEqual(form['account'].value(), theirs.pk)

    def test_an_unknown_source_is_ignored(self):
        form = self.form(f'?account={self.fd.uuid}&source_type=Made%20Up')
        self.assertNotEqual(form['source_type'].value(), 'Made Up')


class AddAccountHintTests(Base):
    def page(self, query='?type=FD'):
        response = self.client.get(reverse('account-create') + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_hint_and_its_data_are_in_the_page(self):
        html = self.page()
        self.assertIn('id="investmentHint"', html)
        self.assertIn('id="investment-types"', html)
        self.assertIn('js/investment_hint.js', html)
        types = json.loads(re.search(r'<script id="investment-types" type="application/json">(.*?)</script>', html, re.S).group(1))
        self.assertIn('FD', types['deposit'])
        self.assertIn('MUTUAL_FUND', types['market'])
        self.assertNotIn('SAVINGS_ACCOUNT', types['deposit'] + types['market'])

    def test_every_element_the_script_looks_for_is_in_the_page(self):
        js = open(finders.find('js/investment_hint.js'), encoding='utf-8').read()
        html = self.page()
        for ident in set(re.findall(r"getElementById\('([^']+)'\)", js)):
            self.assertIn(f'id="{ident}"', html, ident)

    def test_the_words_are_there_and_translated(self):
        html = self.page()
        self.assertIn('use <b>Move Money</b> from your bank account', html)
        self.assertIn('An opening balance is not counted as invested.', html)
        for lang in ('hi', 'mr'):
            self.set_language(lang)
            hint = re.search(r'id="investmentHint".*?</div>', self.page(), re.S).group(0)
            self.assertNotIn('Move Money', hint, lang)
            self.assertNotIn('opening balance', hint, lang)
