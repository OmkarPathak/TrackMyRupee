"""An account name on the All Transactions list leads to that account's page, on a computer and on a phone."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense, Transfer
        self.user = make_user('tx-links')
        self.bank = make_account(self.user, 'SBI Savings Account', 'SAVINGS_ACCOUNT', 50000)
        self.cash = make_account(self.user, 'Cash Wallet', 'CASH_WALLET', 2000)
        today = timezone.localdate()
        Expense.objects.create(user=self.user, category='Food', amount=Decimal('200'), base_amount=Decimal('200'),
                               currency='₹', date=today, account=self.bank, description='Lunch out')
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.cash, amount=Decimal('500'),
                                converted_amount=Decimal('500'), date=today, description='Pocket money')

    def detail_path(self, account):
        return reverse('account-detail', args=[account.uuid])


class ComputerTests(Base):
    def test_clicking_the_account_of_an_expense_opens_that_account(self):
        page = self.new_page(self.user)
        self.open(page, reverse('all-transactions'))
        row = page.locator('tr', has_text='Lunch out').first
        row.locator('a.tmr-account-link').click()
        page.wait_for_url(lambda url: self.detail_path(self.bank) in url)
        self.assertIn('SBI Savings Account', page.inner_text('body'))

    def test_a_transfer_links_each_of_its_accounts(self):
        page = self.new_page(self.user)
        self.open(page, reverse('all-transactions'))
        row = page.locator('tr', has_text='Pocket money').first
        self.assertEqual(row.locator('a.tmr-account-link').count(), 2)
        row.locator('a.tmr-account-link', has_text='Cash Wallet').click()
        page.wait_for_url(lambda url: self.detail_path(self.cash) in url)

    def test_the_rows_actions_menu_still_opens(self):
        page = self.new_page(self.user)
        self.open(page, reverse('all-transactions'))
        row = page.locator('tr', has_text='Lunch out').first
        row.locator('button[data-bs-toggle="dropdown"]').first.click()
        self.assertTrue(row.locator('.dropdown-menu.show').is_visible())


class PhoneTests(Base):
    def test_tapping_the_account_on_a_card_opens_that_account(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('all-transactions'))
        card = page.locator('.expense-card', has_text='Lunch out').first
        card.locator('a.tmr-account-link').tap()
        page.wait_for_url(lambda url: self.detail_path(self.bank) in url)

    def test_a_transfer_card_links_both_accounts(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('all-transactions'))
        card = page.locator('.expense-card', has_text='Pocket money').first
        self.assertEqual(card.locator('a.tmr-account-link').count(), 2)
        card.locator('a.tmr-account-link', has_text='Cash Wallet').tap()
        page.wait_for_url(lambda url: self.detail_path(self.cash) in url)
