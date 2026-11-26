"""Adding a loan from the plain Add loan form: the switch that puts the loan amount into an account, and how it follows the
start date (a loan taken a while ago starts with it off)."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('loan-adder')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 40000)
        self.cash = make_account(self.user, 'Cash', 'CASH_WALLET', 3000)
        self.today = timezone.localdate()

    def open_form(self, phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('loan-create'))
        page.wait_for_selector('#received-block')
        return page

    def fill(self, page, start=None, name='Car Loan'):
        page.fill('input[name="name"]', name)
        page.fill('input[name="initial_principal"]', '260000')
        page.fill('input[name="interest_rate"]', '9')
        page.fill('input[name="duration_months"]', '60')
        page.fill('input[name="start_date"]', (start or self.today).isoformat())

    def save(self, page):
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/loans/add' not in url)

    def balance(self, account):
        account.refresh_from_db()
        return account.balance


class SwitchTests(Base):
    def test_the_form_starts_with_it_on_and_the_account_to_choose(self):
        page = self.open_form()
        self.assertTrue(page.locator('#id_record_received').is_checked())
        self.assertTrue(page.locator('#received-account-row').is_visible())

    def test_the_loan_amount_reaches_the_chosen_account(self):
        page = self.open_form()
        self.fill(page)
        self.set_select(page, 'select[name="received_account"]', str(self.bank.pk))
        self.save(page)
        self.assertEqual(self.balance(self.bank), D('300000.00'))
        self.assertIn('not counted as income', page.inner_text('body'))

    def test_the_money_shows_in_the_accounts_activity_as_money_in(self):
        page = self.open_form()
        self.fill(page)
        self.set_select(page, 'select[name="received_account"]', str(self.bank.pk))
        self.save(page)
        self.open(page, reverse('account-detail', kwargs={'pk': self.bank.uuid}))
        self.assertIn('LOAN RECEIVED', page.inner_text('body').upper())
        from expenses.models import Income
        self.assertFalse(Income.objects.filter(user=self.user).exists())

    def test_switched_off_the_account_choice_goes_and_nothing_is_added(self):
        page = self.open_form()
        self.fill(page)
        page.locator('#id_record_received').click(force=True)
        self.assertFalse(page.locator('#received-account-row').is_visible())
        self.save(page)
        self.assertEqual(self.balance(self.bank), D('40000.00'))
        self.assertEqual(self.balance(self.cash), D('3000.00'))

    def test_a_start_date_long_ago_switches_it_off_by_itself(self):
        page = self.open_form()
        page.fill('input[name="start_date"]', (self.today - timedelta(days=200)).isoformat())
        page.dispatch_event('input[name="start_date"]', 'change')
        self.assertFalse(page.locator('#id_record_received').is_checked())
        self.assertFalse(page.locator('#received-account-row').is_visible())

    def test_a_recent_start_date_leaves_it_on_and_a_change_back_switches_it_on_again(self):
        page = self.open_form()
        page.fill('input[name="start_date"]', (self.today - timedelta(days=200)).isoformat())
        page.dispatch_event('input[name="start_date"]', 'change')
        page.fill('input[name="start_date"]', (self.today - timedelta(days=3)).isoformat())
        page.dispatch_event('input[name="start_date"]', 'change')
        self.assertTrue(page.locator('#id_record_received').is_checked())

    def test_a_choice_made_by_hand_is_not_overridden_by_the_date(self):
        page = self.open_form()
        page.locator('#id_record_received').click(force=True)               # off, by hand
        page.fill('input[name="start_date"]', (self.today - timedelta(days=3)).isoformat())
        page.dispatch_event('input[name="start_date"]', 'change')
        self.assertFalse(page.locator('#id_record_received').is_checked())

    def test_an_old_loan_added_with_it_switched_on_by_hand_is_dated_then(self):
        from expenses.models import LoanDisbursement
        page = self.open_form()
        old = self.today - timedelta(days=200)
        self.fill(page, start=old)
        page.dispatch_event('input[name="start_date"]', 'change')
        page.locator('#id_record_received').click(force=True)
        self.set_select(page, 'select[name="received_account"]', str(self.bank.pk))
        self.save(page)
        self.assertEqual(LoanDisbursement.objects.get().date, old)

    def test_editing_a_loan_has_no_switch(self):
        from e2e.loan_helpers import make_loan
        loan = make_loan(self.user, 'Existing', 100000, 12)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-edit', kwargs={'pk': loan.uuid}))
        self.assertEqual(page.locator('#received-block').count(), 0)

    def test_it_works_on_a_phone(self):
        page = self.open_form(phone=True)
        self.fill(page)
        self.set_select(page, 'select[name="received_account"]', str(self.cash.pk))
        self.save(page)
        self.assertEqual(self.balance(self.cash), D('263000.00'))
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - window.innerWidth'), 2)
