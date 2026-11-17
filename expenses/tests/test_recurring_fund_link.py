"""On the recurring list, a transfer that buys funds shows a "3 funds" link that opens them (modal on a computer,
bottom sheet on a phone), instead of writing every fund name into the Type column."""
import re
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses import holdings_service as hs
from expenses.models import Account, RecurringFundSplit, RecurringTransaction, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='funds-list', password='password')
        UserProfile.objects.filter(user=self.user).update(
            tier='PRO', has_seen_tutorial=True, consent_granted=True, currency='₹')
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('900000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Mutual Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')
        self.alpha = hs.create_holding(self.funds, name='Alpha Small Cap Fund', units='10', price='40', scheme_code='111')
        self.beta = hs.create_holding(self.funds, name='Beta ELSS Tax Saver Fund', units='10', price='40', scheme_code='222')
        self.gamma = hs.create_holding(self.funds, name='Gamma Large & Midcap Fund', units='10', price='40', scheme_code='333')

    def transfer(self, splits, amount='45000', description='Monthly SIP', **kw):
        values = dict(user=self.user, transaction_type='TRANSFER', amount=D(amount), currency='₹',
                      description=description, frequency='MONTHLY', start_date=date(2026, 8, 5),
                      from_account=self.bank, to_account=self.funds, is_active=True)
        values.update(kw)
        rt = RecurringTransaction.objects.create(**values)
        for holding, part in splits:
            RecurringFundSplit.objects.create(recurring=rt, holding=holding, amount=D(part))
        return rt

    def three_funds(self, **kw):
        return self.transfer([(self.alpha, '15000'), (self.beta, '15000'), (self.gamma, '15000')], **kw)

    def page(self, language=None):
        if language:
            UserProfile.objects.filter(user=self.user).update(language=language)
        response = self.client.get(reverse('recurring-list'))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class LinkTests(Base):
    def test_a_transfer_with_funds_shows_how_many(self):
        self.three_funds()
        self.assertIn('3 funds', self.page())

    def test_one_fund_reads_in_the_singular(self):
        self.transfer([(self.alpha, '45000')])
        html = self.page()
        self.assertIn('1 fund', html)
        self.assertNotIn('1 funds', html)

    def test_the_link_is_on_the_computer_row_and_the_phone_card(self):
        rt = self.three_funds(is_active=False)         # (one list: an active one is also in "renewing soon")
        self.assertEqual(self.page().count(f'data-fund-open="{rt.pk}"'), 2)

    def test_an_active_schedule_has_the_link_in_every_list_it_is_in(self):
        rt = self.three_funds()
        self.assertGreaterEqual(self.page().count(f'data-fund-open="{rt.pk}"'), 2)

    def test_a_transfer_without_funds_has_no_link(self):
        self.transfer([], description='Move to savings')
        self.assertNotIn('data-fund-open', self.page())

    def test_an_expense_has_no_link(self):
        RecurringTransaction.objects.create(user=self.user, transaction_type='EXPENSE', amount=D('500'), currency='₹',
                                            description='Netflix', category='Entertainment', frequency='MONTHLY',
                                            start_date=date(2026, 8, 5), account=self.bank, is_active=True)
        self.assertNotIn('data-fund-open', self.page())

    def test_the_fund_names_are_no_longer_written_into_the_type_column(self):
        self.three_funds()
        html = self.page()
        self.assertNotIn('Alpha Small Cap Fund, Beta ELSS Tax Saver Fund', html)
        self.assertNotIn('Fund, Beta', html)

    def test_paused_schedules_get_the_link_too(self):
        rt = self.three_funds(is_active=False)
        self.assertIn(f'data-fund-open="{rt.pk}"', self.page())


class ContentTests(Base):
    def content(self, rt, html=None):
        html = html or self.page()
        match = re.search(rf'<template data-fund-content="{rt.pk}".*?</template>', html, re.S)
        self.assertIsNotNone(match, 'no funds were written for this schedule')
        return match.group(0)

    def test_the_funds_are_written_once_per_schedule(self):
        for active in (True, False):
            rt = self.three_funds(is_active=active, description=f'SIP {active}')
            self.assertEqual(self.page().count(f'data-fund-content="{rt.pk}"'), 1, f'active={active}')

    def test_every_fund_is_listed_with_its_share(self):
        content = self.content(self.three_funds())
        for name in ('Alpha Small Cap Fund', 'Beta ELSS Tax Saver Fund'):
            self.assertIn(name, content)
        self.assertIn('Gamma Large &amp; Midcap Fund', content)
        self.assertEqual(content.count('₹15,000'), 3)

    def test_the_total_is_the_whole_instalment(self):
        content = self.content(self.three_funds())
        self.assertIn('₹45,000', content)
        self.assertIn('Monthly', content)
        self.assertNotIn('Stays as cash', content)

    def test_what_the_funds_leave_out_is_shown_as_cash(self):
        content = self.content(self.transfer([(self.alpha, '20000'), (self.beta, '10000')]))
        self.assertIn('Stays as cash in the account', content)
        self.assertIn('₹15,000', content)

    def test_it_names_the_accounts_and_the_next_date(self):
        content = self.content(self.three_funds())
        self.assertIn('From HDFC Bank to Mutual Funds', content)
        self.assertIn('Next on', content)

    def test_it_links_to_the_form_that_changes_the_funds(self):
        rt = self.three_funds()
        self.assertIn(reverse('recurring-edit', args=[rt.uuid]), self.content(rt))

    def test_the_schedule_name_is_the_title(self):
        rt = self.three_funds(description='Index <SIP>')
        self.assertIn('data-title="Index &lt;SIP&gt;"', self.page())

    def test_paise_are_kept(self):
        content = self.content(self.transfer([(self.alpha, '14791.67')], amount='14791.67'))
        self.assertIn('₹14,791.67', content)


class ModelTests(Base):
    def test_the_total_is_the_sum_of_the_rows(self):
        self.assertEqual(self.three_funds().fund_split_total, D('45000.00'))

    def test_the_cash_remainder_is_what_the_funds_leave(self):
        rt = self.transfer([(self.alpha, '20000')])
        self.assertEqual(rt.fund_cash_remainder, D('25000.00'))

    def test_the_remainder_is_never_negative(self):
        rt = self.transfer([(self.alpha, '30000'), (self.beta, '30000')], amount='45000')
        self.assertEqual(rt.fund_cash_remainder, D('0.00'))

    def test_a_schedule_without_funds_has_a_zero_total(self):
        self.assertEqual(self.transfer([]).fund_split_total, D('0.00'))


class PageTests(Base):
    def test_the_modal_and_the_sheet_are_on_the_page(self):
        html = self.page()
        self.assertIn('id="fundSplitsModal"', html)
        self.assertIn('id="fundSplitsSheet"', html)
        self.assertIn('js/recurring_funds.js', html)

    def test_the_sheet_is_for_phones_only(self):
        html = self.page()
        sheet = html[html.index('id="fundSplitsSheet"') - 200:html.index('id="fundSplitsSheet"')]
        self.assertIn('d-md-none', sheet)

    def test_more_schedules_with_funds_cost_no_more_queries(self):
        later = timezone.localdate() + timedelta(days=40)      # (nothing is due, so loading the page posts nothing)
        self.three_funds(description='SIP 1', start_date=later)
        with CaptureQueriesContext(connection) as one:
            self.page()
        for number in range(2, 7):
            self.three_funds(description=f'SIP {number}', start_date=later)
        with CaptureQueriesContext(connection) as many:
            self.page()
        self.assertEqual(len(many), len(one))


class TranslationTests(Base):
    def test_the_link_reads_in_hindi_and_marathi(self):
        self.three_funds()
        for language, word in (('hi', 'फंड'), ('mr', 'फंड')):
            html = self.page(language)
            link = re.search(r'data-fund-open="\d+".*?</button>', html, re.S).group(0)
            self.assertIn(word, link, language)
            self.assertNotIn('funds', link, language)

    def test_the_funds_text_is_translated(self):
        rt = self.three_funds()
        for language in ('hi', 'mr'):
            content = re.search(rf'<template data-fund-content="{rt.pk}".*?</template>', self.page(language), re.S).group(0)
            self.assertNotIn('From HDFC Bank to', content, language)
            self.assertNotIn('Next on', content, language)
            self.assertNotIn('Change funds', content, language)
