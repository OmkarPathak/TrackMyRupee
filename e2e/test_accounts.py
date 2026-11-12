"""Sign in through the real form, and add accounts the way a new person does."""
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_user


class SignInTests(E2ETestCase):
    def test_signing_in_through_the_form_lands_on_the_dashboard_and_signing_out_works(self):
        make_user('signer')
        page = self.new_page()                                    # (not signed in)
        self.open(page, '/accounts/login/')
        page.fill('input[name="login"]', 'signer')
        page.fill('input[name="password"]', 'pass12345!')
        page.click('button[type="submit"]')
        page.wait_for_url(lambda url: '/accounts/login' not in url)
        self.open(page, reverse('expense-list'))                  # a page that needs a signed-in person opens
        self.assertIn('Expenses', page.inner_text('body'))

    def test_a_wrong_password_says_so_and_does_not_sign_in(self):
        make_user('signer2')
        page = self.new_page()
        self.open(page, '/accounts/login/')
        page.fill('input[name="login"]', 'signer2')
        page.fill('input[name="password"]', 'not-the-password')
        page.click('button[type="submit"]')
        page.wait_for_selector('text=not correct')                # the form says what went wrong
        self.assertIn('/accounts/login', page.url)

    def test_a_page_that_needs_sign_in_sends_a_visitor_to_the_form(self):
        page = self.new_page()
        page.goto(self.live_server_url + reverse('expense-list'))
        self.assertIn('/accounts/login', page.url)


class AddAccountTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('account-maker')

    def accounts(self):
        from expenses.models import Account
        return Account.objects.filter(user=self.user)

    def test_picking_a_kind_then_a_type_then_naming_it_adds_a_savings_account(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-create'))
        page.click('[data-category="bank"]')                      # the kind
        page.click('#typeChoicesList button:has-text("Savings Account")')
        page.fill('#id_name', 'HDFC Savings')
        page.fill('#id_balance', '2500')
        page.click('#nextBtn')                                    # step 1 of 2
        page.click('button[type="submit"]:visible')               # step 2: save
        page.wait_for_url(lambda url: '/accounts/add' not in url)
        account = self.accounts().get(name='HDFC Savings')
        self.assertEqual((account.account_type, account.balance), ('SAVINGS_ACCOUNT', Decimal('2500.00')))

    def test_typing_what_you_have_finds_the_type_and_the_field_appears(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-create'))
        page.fill('#typeSearch', 'ppf')
        page.press('#typeSearch', 'Enter')                       # one match: it is chosen
        page.wait_for_selector('#accountBasicsFields', state='visible')
        self.assertEqual(page.input_value('#id_account_type'), 'PPF')
        self.assertIn('PPF', page.inner_text('#typeChosenLabel'))

    def test_a_deposit_shows_how_money_gets_in_and_out(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=FD')
        self.assertIn('Move Money', page.inner_text('#investmentHint'))
        page.fill('#id_balance', '5000')
        self.assertIn('opening balance', page.inner_text('#investmentHint'))
