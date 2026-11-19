"""On the All Transactions list, each account name leads to that account's page (a transfer links both its accounts)."""
import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, CapitalEvent, Expense, Income, Loan, LoanRepayment, Transfer, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('tx-links', password='pw')
        UserProfile.objects.filter(user=self.user).update(tier='PRO', has_seen_tutorial=True, consent_granted=True,
                                                          currency='₹')
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = self.account('SBI Savings Account')
        self.cash = self.account('Cash')

    def account(self, name, kind='SAVINGS_ACCOUNT'):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D('10000'), currency='₹')

    def expense(self, account=None, description='Lunch'):
        return Expense.objects.create(user=self.user, category='Food', amount=D('200'), base_amount=D('200'),
                                      currency='₹', date=self.today, account=account, description=description)

    def page(self, **headers):
        response = self.client.get(reverse('all-transactions'), **headers)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def link(self, account):
        return reverse('account-detail', args=[account.uuid])

    def links_to(self, html, account):
        return len(re.findall(rf'<a class="tmr-account-link" href="{re.escape(self.link(account))}">', html))


class LinkTests(Base):
    def test_an_expenses_account_is_a_link_to_its_page(self):
        self.expense(self.bank)
        html = self.page()
        self.assertRegex(html, rf'<a class="tmr-account-link" href="{re.escape(self.link(self.bank))}"><span class="tmr-account-name">SBI Savings Account</span></a>')

    def test_the_link_is_on_the_computer_row_and_the_phone_card(self):
        self.expense(self.bank)
        self.assertEqual(self.links_to(self.page(), self.bank), 2)

    def test_an_incomes_account_is_a_link(self):
        Income.objects.create(user=self.user, amount=D('5000'), base_amount=D('5000'), currency='₹', date=self.today,
                              account=self.bank, source_type='Salary', source='Salary')
        self.assertEqual(self.links_to(self.page(), self.bank), 2)

    def test_a_capital_events_account_is_a_link(self):
        CapitalEvent.objects.create(user=self.user, date=self.today, amount=D('50'), base_amount=D('50'), currency='₹',
                                    subtype='other', account=self.cash)
        self.assertEqual(self.links_to(self.page(), self.cash), 2)

    def test_a_loan_repayments_account_is_a_link(self):
        loan = Loan.objects.create(user=self.user, name='Test Loan', loan_type='PERSONAL', initial_principal=D('1000'),
                                   duration_months=10, start_date=self.today, currency='₹')
        LoanRepayment.objects.create(loan=loan, from_account=self.bank, date=self.today, amount=D('100'),
                                     base_amount=D('100'), principal_portion=D('90'), interest_portion=D('10'))
        self.assertEqual(self.links_to(self.page(), self.bank), 2)

    def test_a_transfer_links_both_accounts_in_order(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.cash, amount=D('200'),
                                converted_amount=D('200'), date=self.today)
        html = self.page()
        self.assertEqual(self.links_to(html, self.bank), 2)
        self.assertEqual(self.links_to(html, self.cash), 2)
        self.assertRegex(html, r'SBI Savings Account</span></a> <span class="tmr-account-to">&rarr; <a class="tmr-account-link"[^>]*><span class="tmr-account-name">Cash</span></a></span>')

    def test_an_expense_with_no_account_shows_no_link(self):
        self.expense(None)
        self.assertNotIn('tmr-account-link', self.page())

    def test_a_transfer_whose_account_was_deleted_shows_text_and_does_not_break(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.cash, amount=D('200'),
                                converted_amount=D('200'), date=self.today)
        self.cash.delete()
        html = self.page()
        self.assertNotIn(f'href="{self.link(self.bank)}"', html)        # (shown as plain text: its other half is gone)

    def test_the_link_is_in_the_partial_a_filter_reloads_too(self):
        self.expense(self.bank)
        html = self.page(HTTP_HX_REQUEST='true')
        self.assertEqual(self.links_to(html, self.bank), 2)

    def test_an_account_name_is_escaped(self):
        odd = self.account('A&B <b>Co</b>')
        self.expense(odd)
        html = self.page()
        self.assertIn('A&amp;B &lt;b&gt;Co&lt;/b&gt;</span></a>', html)
        self.assertNotIn('<b>Co</b>', html)

    def test_each_row_links_its_own_account(self):
        self.expense(self.bank, 'First')
        self.expense(self.cash, 'Second')
        html = self.page()
        self.assertEqual(self.links_to(html, self.bank), 2)
        self.assertEqual(self.links_to(html, self.cash), 2)


class CostTests(Base):
    def test_more_rows_cost_no_more_queries(self):
        for number in range(4):
            self.expense(self.bank if number % 2 else self.cash, f'Early {number}')
        cache.clear()                                    # (both loads start cold: writes clear caches, loads fill them)
        with CaptureQueriesContext(connection) as few:
            self.page()
        for number in range(16):
            self.expense(self.bank if number % 2 else self.cash, f'Later {number}')
        cache.clear()
        with CaptureQueriesContext(connection) as many:
            self.page()
        self.assertEqual(len(many), len(few))

    def test_the_link_leads_to_a_page_that_opens(self):
        self.expense(self.bank)
        response = self.client.get(self.link(self.bank))
        self.assertEqual(response.status_code, 200)


class ChipTests(Base):
    def test_the_phone_card_draws_the_links_as_chips_and_the_table_does_not(self):
        self.expense(self.bank)
        html = self.page()
        self.assertEqual(html.count('class="text-muted tmr-tx-accounts"'), 1)        # (the card)
        table_cell = html[html.index('tx-account"') - 60:html.index('tx-account"') + 400]
        self.assertNotIn('tmr-tx-accounts', table_cell)

    def test_a_transfer_card_puts_both_accounts_in_one_chip_row(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.cash, amount=D('200'),
                                converted_amount=D('200'), date=self.today)
        html = self.page()
        row = html[html.index('class="text-muted tmr-tx-accounts"'):]
        row = row[:row.index('</div>')]
        self.assertEqual(row.count('tmr-account-link'), 2)

    def test_there_is_no_icon_in_the_account_links(self):
        self.expense(self.bank)
        html = self.page()
        for link in re.findall(r'<a class="tmr-account-link".*?</a>', html):
            self.assertNotIn('<i ', link)
