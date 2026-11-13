"""Adding an expense the way a person does: type it, check it, add it, find it in the list."""
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user


class AddExpenseTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('spender')
        self.account = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 25000)

    def type_it(self, page, text):
        """Open the form, type the entry, let the app read it, and make sure a category is chosen."""
        self.open(page, reverse('expense-create'))
        page.fill('#tmr-quick-input', text)
        page.click('#tmr-go')
        page.wait_for_selector('#tmr-add', state='visible')
        if not page.input_value('#tmr-category'):        # (the app picks one when it can; otherwise the person does)
            self.set_select(page, '#tmr-category', 'Food')

    def expenses(self):
        from expenses.models import Expense
        return Expense.objects.filter(user=self.user)

    def test_a_typed_expense_is_saved_and_shows_in_the_list_and_on_the_account(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.type_it(page, 'tea 40')
        page.click('#tmr-add')
        page.wait_for_url('**/expenses/')                         # saving takes the person to the list
        expense = self.expenses().get()
        self.assertEqual((expense.amount, expense.account_id), (Decimal('40.00'), self.account.pk))
        self.assertEqual(Account.objects.get(pk=self.account.pk).balance, Decimal('24960.00'))
        self.assertIn('Tea', page.inner_text('body'))

    def test_it_also_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.type_it(page, 'auto 120')
        page.click('#tmr-add')
        page.wait_for_url('**/expenses/')
        self.assertEqual(self.expenses().filter(amount=Decimal('120')).count(), 1)
        self.assertIn('Auto', page.inner_text('body'))

    def test_add_another_keeps_the_form_and_the_expense_can_be_undone(self):
        page = self.new_page(self.user)
        self.type_it(page, 'snack 60')
        page.click('#tmr-add-another')
        undo = page.locator('.tmr-toast__btn', has_text='Undo')   # the confirmation toast's Undo button
        undo.wait_for()
        self.assertEqual(self.expenses().count(), 1)
        undo.click()
        self.wait_until(lambda: self.expenses().count() == 0, 'the expense was not taken back')
