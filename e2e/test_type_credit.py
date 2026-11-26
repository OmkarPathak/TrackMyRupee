"""Short-term credit (credit card, BNPL, overdraft): what is owed, the limit, paying it off, and the bill date.

The balance is negative while money is owed. It lowers net worth, and the limit gives "available" and "% used".
"""
import calendar
from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import balance_on, net_worth, open_account, save_account_form

D = Decimal
TODAY = timezone.localdate()
KINDS = ('CREDIT_CARD', 'BNPL', 'OVERDRAFT')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('credit-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)

    def card(self, kind='CREDIT_CARD', name='Card', limit=50000, billing_day=None, balance=0):
        return make_account(self.user, name, kind, balance, credit_limit=None if limit is None else D(str(limit)),
                            credit_card_billing_day=billing_day)

    def spend(self, account, amount):
        from expenses.models import Expense
        return Expense.objects.create(user=self.user, account=account, amount=D(str(amount)), base_amount=D(str(amount)), currency='₹',
                                      date=TODAY, description='Edge spend', category='Shopping')

    def pay(self, card, amount, source=None):
        from expenses.models import Transfer
        return Transfer.objects.create(user=self.user, from_account=source or self.bank, to_account=card, amount=D(str(amount)),
                                       converted_amount=D(str(amount)), date=TODAY)

    def facts(self, page):
        """(available, limit, used %) as the page shows them."""
        text = page.inner_text('body').replace('\n', ' ')
        import re
        available = re.search(r'Available:\s*₹([\d,]+)', text)
        limit = re.search(r'Limit:\s*₹([\d,]+)', text)
        used = re.search(r'([\d.]+)% used', text)
        return (D(available.group(1).replace(',', '')) if available else None,
                D(limit.group(1).replace(',', '')) if limit else None,
                D(used.group(1)) if used else None)


class OwedTests(Base):
    def test_spending_makes_the_balance_negative_and_lowers_net_worth_for_every_kind(self):
        page = self.new_page(self.user)
        owed_before = net_worth(self, page)
        for i, kind in enumerate(KINDS, start=1):
            with self.subTest(kind):
                account = self.card(kind, f'Credit {kind}')
                self.spend(account, 1000 * i)
                open_account(self, page, account)
                self.assertEqual(balance_on(page), D(-1000 * i))
        self.assertEqual(net_worth(self, page), owed_before - D('6000'))

    def test_paying_from_the_bank_brings_the_balance_up_and_the_bank_down_by_the_same_amount(self):
        card = self.card()
        self.spend(card, 8000)
        self.pay(card, 3000)
        page = self.new_page(self.user)
        self.assertEqual(self.balance(page, card), D('-5000.00'))
        self.assertEqual(net_worth(self, page), D('100000') - D('8000'))          # paying moves money, it does not change net worth

    def test_paying_it_all_leaves_exactly_zero(self):
        card = self.card()
        self.spend(card, 4321.55)
        self.pay(card, '4321.55')
        page = self.new_page(self.user)
        self.assertEqual(self.balance(page, card), D('0.00'))

    def balance(self, page, account):
        open_account(self, page, account)
        return balance_on(page)

    def test_paying_more_than_is_owed_leaves_a_credit_that_counts_in_net_worth(self):
        card = self.card()
        self.spend(card, 1000)
        self.pay(card, 1500)
        page = self.new_page(self.user)
        self.assertEqual(self.balance(page, card), D('500.00'))
        self.assertEqual(net_worth(self, page), D('100000') - D('1000'))


class LimitTests(Base):
    def test_available_and_percent_used_follow_what_is_owed(self):
        card = self.card(limit=50000)
        self.spend(card, 12500)
        page = self.new_page(self.user)
        open_account(self, page, card)
        self.assertEqual(self.facts(page), (D('37500'), D('50000'), D('25.0')))

    def test_the_bar_changes_colour_at_forty_and_seventy_five_percent(self):
        page = self.new_page(self.user)
        for used, colour in ((20000, 'bg-success'), (20100, 'bg-warning'), (37500, 'bg-warning'), (37600, 'bg-danger')):
            with self.subTest(used=used):
                card = self.card(name=f'Card {used}', limit=50000)
                self.spend(card, used)
                open_account(self, page, card)
                self.assertIn(colour, page.locator('.cc-utilization-bar').get_attribute('class'))

    def test_going_over_the_limit_shows_nothing_available_and_over_one_hundred_percent(self):
        card = self.card(limit=50000)
        self.spend(card, 60000)
        page = self.new_page(self.user)
        open_account(self, page, card)
        self.assertEqual(self.facts(page), (D('0'), D('50000'), D('120.0')))
        box = page.locator('.cc-utilization-progress').bounding_box()
        fill = page.locator('.cc-utilization-bar').bounding_box()
        self.assertLessEqual(fill['x'] + fill['width'], box['x'] + box['width'] + 1)      # the bar stays inside its track

    def test_with_no_limit_the_page_says_so_and_offers_to_set_one(self):
        for label, limit in (('unset', None), ('zero', 0)):
            with self.subTest(label):
                card = self.card(name=f'Card {label}', limit=limit)
                page = self.new_page(self.user)
                open_account(self, page, card)
                self.assertIn('Not Set', page.inner_text('body'))
                self.assertGreater(page.locator('a:has-text("Set Limit")').count(), 0)
                self.assertNotIn('% used', page.inner_text('body'))

    def test_a_card_in_credit_is_not_counted_as_used_credit(self):
        """Overpaying a card leaves a positive balance (the bank owes you), which uses none of the limit."""
        card = self.card(limit=50000)
        self.spend(card, 1000)
        self.pay(card, 1500)
        page = self.new_page(self.user)
        open_account(self, page, card)
        self.assertEqual(self.facts(page), (D('50000'), D('50000'), D('0.0')))


class BillDateTests(Base):
    @staticmethod
    def next_bill(day):
        """The next time `day` of the month comes round (the last day of a short month), counting today."""
        def clamp(y, m):
            return date(y, m, min(day, calendar.monthrange(y, m)[1]))
        this = clamp(TODAY.year, TODAY.month)
        if this >= TODAY:
            return this
        return clamp(TODAY.year + (TODAY.month == 12), TODAY.month % 12 + 1)

    def test_the_next_bill_date_is_this_month_or_next_for_every_day_of_the_month(self):
        page = self.new_page(self.user)
        for day in (1, 15, 28, 29, 30, 31, TODAY.day, max(1, TODAY.day - 1), min(31, TODAY.day + 1)):
            with self.subTest(day=day):
                card = self.card(name=f'Bill day {day}', billing_day=day)
                open_account(self, page, card)
                self.assertIn(self.next_bill(day).strftime('%d %b %Y'), page.inner_text('body').replace('\n', ' '))

    def test_no_billing_day_means_no_next_bill_line(self):
        card = self.card(billing_day=None)
        page = self.new_page(self.user)
        open_account(self, page, card)
        self.assertNotIn('Next Bill', page.inner_text('body'))


class FormTests(Base):
    def fill(self, page, kind, name, *, limit=None, day=None, balance='0'):
        self.open(page, reverse('account-create') + f'?type={kind}')
        page.fill('#id_name', name)
        page.fill('#id_balance', balance)
        page.click('#nextBtn')
        page.wait_for_selector('#id_credit_limit', state='visible')
        if limit is not None:
            page.fill('#id_credit_limit', limit)
        if day is not None:
            page.fill('#id_credit_card_billing_day', day)
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()

    def test_each_kind_is_created_with_a_limit_and_a_billing_day(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                self.fill(page, kind, f'New {kind}', limit='75000.50', day='12')
                self.wait_until(lambda k=kind: Account.objects.filter(user=self.user, name=f'New {k}').exists(), f'{kind} was not saved', seconds=15)
                saved = Account.objects.get(user=self.user, name=f'New {kind}')
                self.assertEqual((saved.credit_limit, saved.credit_card_billing_day), (D('75000.50'), 12))

    def test_a_negative_limit_is_refused(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'CREDIT_CARD', 'Negative limit', limit='-5')
        page.wait_for_timeout(1000)
        self.assertFalse(Account.objects.filter(user=self.user, name='Negative limit').exists())

    def test_the_billing_day_must_be_a_day_of_the_month(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for day, saved in (('0', False), ('32', False), ('1', True), ('31', True)):
            with self.subTest(day=day):
                self.fill(page, 'CREDIT_CARD', f'Day {day}', limit='1000', day=day)
                page.wait_for_timeout(1200)
                self.assertEqual(Account.objects.filter(user=self.user, name=f'Day {day}').exists(), saved)

    def test_a_card_can_be_created_with_nothing_owed_and_no_limit(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'CREDIT_CARD', 'Bare card')
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Bare card').exists(), 'the card was not saved', seconds=15)

    def test_the_limit_can_be_set_later_from_the_account_page(self):
        card = self.card(limit=None)
        self.spend(card, 5000)
        page = self.new_page(self.user)
        open_account(self, page, card)
        page.click('a:has-text("Set Limit")')
        page.wait_for_selector('#id_credit_limit', state='attached')
        if page.locator('#nextBtn').is_visible():
            page.click('#nextBtn')
        page.fill('#id_credit_limit', '20000')
        save_account_form(page)
        page.wait_for_url(lambda url: '/edit' not in url)
        open_account(self, page, card)
        self.assertEqual(self.facts(page), (D('15000'), D('20000'), D('25.0')))
