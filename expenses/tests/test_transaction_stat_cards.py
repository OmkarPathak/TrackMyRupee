"""The figures at the top of All Transactions on a phone: six cards, two to a row, each a label, a figure and a note."""
import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, Expense, Income


class StatCardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cards', 'c@example.com', 'pw')
        self.client.force_login(self.user)
        account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('1000'), currency='₹')
        Expense.objects.create(user=self.user, date=timezone.localdate(), amount=Decimal('450'), description='Lunch',
                               category='Food', account=account, base_amount=Decimal('450'))
        self.html = self.client.get(reverse('all-transactions')).content.decode()

    def test_there_are_six_cards_in_two_columns(self):
        block = self.html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        self.assertEqual(block.count('tmr-stat-card'), 6)
        self.assertIn('row row-cols-2 g-2', block)
        self.assertNotIn('row-cols-3', block)

    def test_each_card_has_a_label_a_figure_and_a_note(self):
        block = self.html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        for label in ('Income', 'Expenses', 'Transfers', 'Capital', 'Loans', 'Net Remaining'):
            self.assertRegex(block, rf'tmr-stat-label">(<span class="tmr-stat-dot [^"]*" aria-hidden="true"></span>)?{label}</div>')
        self.assertEqual(block.count('tmr-stat-value'), 6)
        self.assertEqual(block.count('tmr-stat-note'), 6)

    def test_the_figures_are_still_the_totals_with_their_exact_amount_on_tap(self):
        block = self.html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        value = re.search(r'tmr-stat-value[^>]*>(.*?)</div>', block, re.S).group(1)
        self.assertIn('₹', value)
        self.assertIn('1 entry', block)

    def test_the_net_remaining_note_is_the_savings_rate(self):
        block = self.html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        self.assertRegex(block, r'tmr-stat-note fw-semibold amount-expense[^>]*>\s*[-\d.,]+% rate')      # (this test person spent more than they earned)

    def test_each_label_has_a_dot_in_the_colour_of_its_rows_below(self):
        block = self.html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        for label, colour in (('Income', 'amount-income'), ('Expenses', 'amount-expense'), ('Transfers', 'amount-transfer'),
                              ('Capital', 'amount-capital_event'), ('Loans', 'amount-loan')):
            self.assertIn(f'<span class="tmr-stat-dot {colour}" aria-hidden="true"></span>{label}', block)
        self.assertEqual(block.count('tmr-stat-dot'), 5)               # (Net Remaining is worked out, so it has none)

    def test_net_remaining_is_green_when_there_is_money_left(self):
        Income.objects.create(user=self.user, date=timezone.localdate(), amount=Decimal('5000'), source='Salary',
                              account=Account.objects.get(user=self.user), base_amount=Decimal('5000'))
        block = self.client.get(reverse('all-transactions')).content.decode().split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        self.assertRegex(block, r'tmr-stat-note fw-semibold amount-income[^>]*>\s*[-\d.,]+% rate')

    def test_computers_keep_their_own_summary(self):
        self.assertIn('d-none d-md-', self.html)


class NegativeNetRemainingTests(TestCase):
    """When more went out than came in, the figure reads -₹80,000, not ₹-80,000."""

    def setUp(self):
        self.user = User.objects.create_user('overspent', 'o@example.com', 'pw')
        self.client.force_login(self.user)
        account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('1000'), currency='₹')
        today = timezone.localdate()
        Income.objects.create(user=self.user, date=today, amount=Decimal('20000'), source='Salary', account=account, base_amount=Decimal('20000'))
        Expense.objects.create(user=self.user, date=today, amount=Decimal('100000'), description='Big', category='Food',
                               account=account, base_amount=Decimal('100000'))

    def net_remaining_figure(self):
        html = self.client.get(reverse('all-transactions')).content.decode()
        block = html.split('<div class="d-md-none">', 1)[1].split('tmr-toolbar', 1)[0]
        card = block.split('Net Remaining', 1)[1]
        return re.search(r'tmr-stat-value[^>]*>(.*?)</div>', card, re.S).group(1)

    def test_the_minus_sign_comes_before_the_currency(self):
        figure = self.net_remaining_figure()
        self.assertNotIn('₹-', figure)
        self.assertNotIn('₹−', figure)
        self.assertRegex(figure, r'[-−]\s*(<[^>]+>)*₹')

    def test_the_amount_is_still_the_shortfall(self):
        self.assertIn('80,000', self.net_remaining_figure().replace('&#x27;', ''))
