"""The four metric cards at the top of the Dashboard (Net Worth, Income, Spent, Invested) draw their comparison lines the same
way: the currency symbol against the amount, a signed percentage, and the cards the same height."""
import re
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

D = Decimal
CARDS = ('networth', 'income', 'spent', 'invested')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense, Income, Transfer
        self.user = make_user('card-reader')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 325462)
        self.funds = make_account(self.user, 'Funds', 'MUTUAL_FUND', 0)
        today = timezone.localdate()
        last_month = (today.replace(day=1) - timedelta(days=5))
        for when, income, spent, invested in ((today, 300000, 45580, 65228), (last_month, 380000, 38000, 65428)):
            Income.objects.create(user=self.user, account=self.bank, amount=D(income), source='Salary', source_type='Salary', date=when, currency='₹')
            Expense.objects.create(user=self.user, account=self.bank, amount=D(spent), description='Rent', category='Rent', date=when, currency='₹')
            Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.funds, amount=D(invested), date=when)

    def open_home(self, phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        page.wait_for_timeout(1000)
        return page

    def lines(self, page, card):
        """[(text of the change, text of the percentage badge or '', label)] for a card, as drawn."""
        return page.evaluate("""(card) => {
            const root = document.querySelector('#bento-card-' + card);
            return Array.from(root.querySelectorAll('.bento-metric-delta')).map(row => ({
                change: (row.querySelector('span.d-inline-flex span') || {innerText: ''}).innerText.trim(),
                symbolGap: (() => { const s = row.querySelector('span.d-inline-flex'); return s ? s.children.length : -1; })(),
                badge: (row.querySelector('.badge') || {innerText: ''}).innerText.trim(),
                label: (row.querySelector('.bento-comparison-label:last-child') || {innerText: ''}).innerText.trim(),
            }));
        }""", card)


class ConsistencyTests(Base):
    def test_every_card_puts_the_symbol_against_the_amount(self):
        page = self.open_home()
        for card in CARDS:
            for line in self.lines(page, card):
                if line['change']:
                    self.assertRegex(line['change'], r'^₹\d', (card, line))
                    self.assertEqual(line['symbolGap'], 2, (card, line))          # the arrow and one piece: symbol and amount

    def test_every_percentage_is_signed_and_has_one_decimal(self):
        page = self.open_home()
        for card in CARDS:
            for line in self.lines(page, card):
                if line['badge']:
                    self.assertRegex(line['badge'], r'^([+-]\d+\.\d%|10x\+)$', (card, line))

    def test_spending_up_is_red_and_income_down_is_red(self):
        page = self.open_home()
        self.assertTrue(page.locator('#bento-card-spent .bento-metric-delta .text-danger').count() >= 1)       # spent 45,580 vs 38,000
        self.assertTrue(page.locator('#bento-card-income .bento-metric-delta .text-danger').count() >= 1)      # income 3,00,000 vs 3,80,000

    def test_the_comparison_wording_is_the_same_apart_from_net_worth(self):
        page = self.open_home()
        for card in ('income', 'spent', 'invested'):
            self.assertEqual(self.lines(page, card)[-1]['label'], 'vs last month', card)
        self.assertEqual(self.lines(page, 'networth')[0]['label'], 'this month')

    def test_the_cards_are_the_same_height_on_a_computer_and_on_a_phone(self):
        for phone in (False, True):
            page = self.open_home(phone=phone)
            heights = page.evaluate("() => Array.from(document.querySelectorAll('.bento-metric-card')).map(c => Math.round(c.getBoundingClientRect().height))")
            self.assertEqual(len(set(heights)), 1, (phone, heights))

    def test_a_card_with_no_change_says_so_in_the_same_words(self):
        from expenses.models import Expense
        Expense.objects.filter(user=self.user).update(amount=D('38000'), base_amount=D('38000'))      # the same this month as last
        page = self.open_home()
        spent = page.inner_text('#bento-card-spent .bento-metric-delta')
        self.assertIn('No change', spent)
        self.assertIn('vs last month', spent)
        self.assertNotIn('▲', spent)

    def test_the_page_in_hindi_has_the_same_shape(self):
        user = make_user('card-reader-hi', language='hi')
        make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 5000)
        page = self.new_page(user)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        for card in CARDS:
            for line in self.lines(page, card):
                self.assertNotRegex(line['change'], r'₹\s+\S', (card, line))
