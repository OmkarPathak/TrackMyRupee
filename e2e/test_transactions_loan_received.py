"""The Transactions list shows loan amounts received as money in that is neither income nor spending."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import make_loan

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense, Income, LoanDisbursement
        self.user = make_user('tx-lister')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 40000)
        self.loan = make_loan(self.user, 'Car Loan', 260000, 60, '9', days_ago=1, loan_type='CAR')
        self.received = LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D('260000'), date=timezone.localdate())
        today = timezone.localdate()
        Income.objects.create(user=self.user, account=self.bank, amount=D('50000'), base_amount=D('50000'), source='Salary', source_type='Salary',
                              date=today, currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('12000'), base_amount=D('12000'), description='Rent', category='Rent',
                               date=today, currency='₹')

    def open_list(self, phone=False, **query):
        page = self.new_page(self.user, phone=phone)
        suffix = ''.join(f'&{k}={v}' for k, v in query.items())
        self.open(page, reverse('all-transactions') + '?time_period=all' + suffix)
        page.wait_for_function("['Car Loan', 'Salary', 'Rent', 'No transactions'].some(t => document.body.innerText.includes(t))")
        return page

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance


class ListTests(Base):
    def test_it_is_a_row_with_its_own_label_loan_and_account(self):
        page = self.open_list()
        row = page.locator('tbody tr', has_text='Loan received').first
        text = row.inner_text()
        self.assertIn('Car Loan', text)
        self.assertIn('HDFC Savings', text)
        self.assertRegex(text.replace(',', ''), r'\+\s*₹\s*(2\.6L|260000)')

    def test_it_is_not_counted_as_income_or_spending(self):
        page = self.open_list()
        text = page.inner_text('body')
        self.assertNotRegex(text, r'Income\s*₹\s*(3\.1L|3,10,000)')        # (50,000 of salary only, never 3,10,000)

    def test_the_type_filter_shows_only_it(self):
        page = self.open_list(type='LOAN_RECEIVED')
        self.assertEqual(page.locator('tbody tr').count(), 1)
        self.assertIn('Loan received', page.inner_text('tbody'))

    def test_another_type_leaves_it_out(self):
        page = self.open_list(type='INCOME')
        self.assertNotIn('Loan received', page.inner_text('tbody'))

    def test_searching_for_the_loan_finds_it(self):
        page = self.open_list(search='Car')
        self.assertEqual(page.locator('tbody tr').count(), 1)

    def test_it_shows_on_a_phone_with_its_actions(self):
        page = self.open_list(phone=True)
        card = page.locator('.expense-card', has_text='Car Loan').first
        self.assertTrue(card.is_visible())
        self.assertRegex(card.inner_text().replace(',', ''), r'\+\s*₹\s*(2\.6L|260000)')
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - window.innerWidth'), 2)


class ActionTests(Base):
    def test_the_menu_opens_the_loan(self):
        page = self.open_list()
        row = page.locator('tbody tr', has_text='Loan received').first
        row.locator('button.no-caret').click()
        row.locator('a:has-text("View Loan")').click()
        page.wait_for_url(lambda url: f'/loans/{self.loan.uuid.hex}' in url or f'/loans/{self.loan.uuid}' in url)

    def test_deleting_it_from_the_list_takes_the_amount_out_of_the_account_again(self):
        page = self.open_list()
        self.assertEqual(self.balance(), D('338000.00'))             # 40,000 + 50,000 - 12,000 + 2,60,000 (balances were set directly)
        row = page.locator('tbody tr', has_text='Loan received').first
        row.locator('button.no-caret').click()
        row.locator('button:has-text("Delete")').click()
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        from expenses.models import LoanDisbursement
        self.wait_until(lambda: not LoanDisbursement.objects.filter(pk=self.received.pk).exists())
        self.assertEqual(self.balance(), D('78000.00'))
