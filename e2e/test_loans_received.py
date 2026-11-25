"""The loan amount arriving in an account: recording it from the loan page, what it does to the account and to the
income figures, deleting it, and the Guided Setup loan flow that records it for a new loan."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import make_loan

D = Decimal
MODAL = '#disbursementModal'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('lender-paid')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 40000)
        self.cash = make_account(self.user, 'Cash', 'CASH_WALLET', 3000)
        self.today = timezone.localdate()

    def balance(self, account=None):
        account = account or self.bank
        account.refresh_from_db()
        return account.balance

    def open_loan(self, loan):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
        page.wait_for_selector('#money-received-card')
        return page

    def open_window(self, page, account='bank'):
        """Open the window and, unless told otherwise, pick the bank (with no entries yet it starts on the account called Cash)."""
        page.click('button[data-bs-target="#disbursementModal"]')
        page.wait_for_selector(MODAL + '.show')
        page.wait_for_timeout(400)
        if account == 'bank':
            self.set_select(page, f'{MODAL} select[name="account"]', str(self.bank.pk))

    def save_window(self, page):
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)


class RecordFromTheLoanPageTests(Base):
    def setUp(self):
        super().setUp()
        self.loan = make_loan(self.user, 'Car Loan (HDFC)', 500000, 60, '9.5', days_ago=60, loan_type='CAR')

    def test_the_window_starts_with_the_loan_amount_and_the_start_date(self):
        page = self.open_loan(self.loan)
        self.open_window(page, account=None)
        self.assertEqual(page.input_value(f'{MODAL} input[name="amount"]').replace(',', ''), '500000.00')
        self.assertEqual(page.input_value(f'{MODAL} input[name="date"]'), self.loan.start_date.isoformat())
        self.assertEqual(page.input_value(f'{MODAL} select[name="account"]'), str(self.cash.pk))     # (nothing recorded yet: the account called Cash)

    def test_recording_it_adds_the_amount_to_the_account_and_lists_it(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        self.save_window(page)
        received = self.loan.disbursements.get()
        self.assertEqual((received.account_id, received.amount), (self.bank.pk, D('500000.00')))
        self.assertEqual(self.balance(), D('540000.00'))
        card = page.inner_text('#money-received-card')
        self.assertIn('HDFC Savings', card)
        self.assertIn('+₹5,00,000', card)
        self.assertIn('Recorded', page.inner_text('body'))

    def test_it_shows_in_the_accounts_activity_as_money_in_and_not_as_income(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        self.save_window(page)
        self.open(page, reverse('account-detail', kwargs={'pk': self.bank.uuid}))
        text = page.inner_text('body')
        self.assertIn('LOAN RECEIVED', text.upper())
        self.assertIn('Car Loan (HDFC)', text)
        self.assertRegex(text, r'in\s*₹5,00,000')
        self.open(page, reverse('income-list'))
        self.assertNotIn('Car Loan', page.inner_text('body'))
        from expenses.models import Income
        self.assertFalse(Income.objects.filter(user=self.user).exists())

    def test_the_accounts_balance_on_the_accounts_page_includes_it(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        self.save_window(page)
        self.open(page, reverse('account-list'))
        self.assertIn('5,40,000', page.inner_text('body'))

    def test_a_part_can_be_recorded_and_the_window_then_offers_the_rest(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        page.fill(f'{MODAL} input[name="amount"]', '200000')
        self.save_window(page)
        self.assertEqual(self.balance(), D('240000.00'))
        self.open_window(page)
        self.assertEqual(page.input_value(f'{MODAL} input[name="amount"]').replace(',', ''), '300000.00')

    def test_it_can_go_into_another_account(self):
        page = self.open_loan(self.loan)
        self.open_window(page, account=None)
        self.set_select(page, f'{MODAL} select[name="account"]', str(self.cash.pk))
        self.save_window(page)
        self.assertEqual(self.balance(self.cash), D('503000.00'))
        self.assertEqual(self.balance(), D('40000.00'))

    def test_a_note_can_be_added(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        page.fill(f'{MODAL} input[name="note"]', 'Disbursed by HDFC on 3rd')
        self.save_window(page)
        self.assertIn('Disbursed by HDFC on 3rd', page.inner_text('#money-received-card'))

    def test_an_amount_of_nothing_or_a_future_date_is_refused(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        page.fill(f'{MODAL} input[name="amount"]', '0')
        self.save_window(page)
        self.assertEqual(self.loan.disbursements.count(), 0)
        self.assertIn('Could not record', page.inner_text('body'))
        self.open_window(page)
        page.fill(f'{MODAL} input[name="date"]', (self.today + timedelta(days=10)).isoformat())
        self.save_window(page)
        self.assertEqual(self.loan.disbursements.count(), 0)
        self.assertEqual(self.balance(), D('40000.00'))

    def test_a_second_click_records_it_once(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        button = page.locator(f'{MODAL} button[type="submit"]')
        button.click()
        try:
            button.click(timeout=400)
        except Exception:
            pass
        page.wait_for_load_state('load')
        page.wait_for_timeout(700)
        self.assertEqual(self.loan.disbursements.count(), 1)
        self.assertEqual(self.balance(), D('540000.00'))

    def test_the_loans_own_figures_are_not_changed_by_it(self):
        page = self.open_loan(self.loan)
        self.open_window(page)
        self.save_window(page)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.remaining_principal, D('500000.00'))
        self.assertEqual(self.loan.repayments.count(), 0)


class DeleteTests(Base):
    def setUp(self):
        super().setUp()
        self.loan = make_loan(self.user, 'Car Loan (HDFC)', 500000, 60, '9.5', days_ago=60, loan_type='CAR')
        from expenses.models import LoanDisbursement
        self.received = LoanDisbursement.objects.create(loan=self.loan, account=self.bank, amount=D('500000'), date=self.loan.start_date)

    def test_deleting_it_from_the_loan_page_takes_the_amount_out_again(self):
        self.assertEqual(self.balance(), D('540000.00'))
        page = self.open_loan(self.loan)
        page.click(f'#money-received-card button[data-url="{reverse("loan-disbursement-delete", kwargs={"pk": self.received.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        page.wait_for_load_state('load')
        self.wait_until(lambda: self.loan.disbursements.count() == 0)
        self.assertEqual(self.balance(), D('40000.00'))
        self.assertIn('No amount recorded as received yet', page.inner_text('body'))

    def test_deleting_it_from_the_accounts_activity_does_the_same(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-detail', kwargs={'pk': self.bank.uuid}))
        page.click('button.dropdown-toggle.no-caret:visible >> nth=0') if page.locator('button.no-caret:visible').count() == 0 else \
            page.click('button.no-caret:visible >> nth=0')
        page.click(f'button[data-url*="{reverse("loan-disbursement-delete", kwargs={"pk": self.received.uuid})}"]:visible')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        self.wait_until(lambda: self.loan.disbursements.count() == 0)
        self.assertEqual(self.balance(), D('40000.00'))

    def test_deleting_the_loan_takes_what_it_put_in_the_account_out_again(self):
        from expenses.models import Loan
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        page.click(f'button[data-url="{reverse("loan-delete", kwargs={"pk": self.loan.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        self.wait_until(lambda: not Loan.objects.filter(pk=self.loan.pk).exists())
        self.assertEqual(self.balance(), D('40000.00'))

    def test_an_account_that_received_it_has_its_currency_fixed(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-edit', kwargs={'pk': self.bank.uuid}))
        self.assertTrue(page.locator('select[name="currency"]').is_disabled() or
                        page.locator('select[name="currency"]').get_attribute('readonly') is not None or
                        page.locator('select[name="currency"]').count() == 0)


class GuidedLoanSetupTests(Base):
    def walk(self, page, *, received=True, account='bank', mid_tenure=False, down_payment=None, schedule=True, name='Home Loan'):
        """Fill the loan setup through its steps and stop on the review."""
        self.open(page, reverse('flow-detail', kwargs={'key': 'loan'}))
        page.wait_for_selector('#stepCounterBadge')
        form = page.locator('#tmr-flow-form-loan')
        form.locator('input[name="name"]').fill(name)
        form.locator('input[name="principal"]').fill('1200000')
        form.locator('input[name="annual_rate"]').fill('8.5')
        form.locator('input[name="tenure_months"]').fill('120')
        form.locator('input[name="start_date"]').fill((self.today - timedelta(days=1)).isoformat() if not mid_tenure else
                                                      (self.today - timedelta(days=400)).isoformat())
        switch = form.locator('input[name="record_loan_received"]')
        if switch.is_visible():
            if switch.is_checked() != received:
                switch.click()
        if received and account is not None and not mid_tenure:
            chosen = self.bank if account == 'bank' else account
            self.set_select(page, 'select[name="loan_received_account"]', str(chosen.pk))
        next_button = lambda: form.locator('button:has-text("Next") >> visible=true').first.click()
        next_button()
        page.wait_for_timeout(300)
        schedule_box = form.locator('input[name="create_repayment_schedule"]')
        if schedule_box.is_checked() != schedule:
            schedule_box.click()
        if schedule:
            self.set_select(page, 'select[name="payment_account"]', str(self.bank.pk))
        next_button()
        page.wait_for_timeout(300)
        if mid_tenure:
            form.locator('input[name="mid_tenure"]').check()
            form.locator('input[name="opening_paid_principal"]').fill('200000')
        if down_payment:
            form.locator('input[name="include_down_payment"]').check()
            form.locator('input[name="down_payment_amount"]').fill(str(down_payment))
            self.set_select(page, 'select[name="down_payment_account"]', str(self.cash.pk))
        next_button()
        page.wait_for_selector('#tmr-review-loan', state='visible')

    def confirm(self, page):
        page.locator('[data-confirm-btn]').first.click()
        page.wait_for_url(lambda url: '/flows/loan' not in url, timeout=20000)

    def loan_made(self, name='Home Loan'):
        from expenses.models import Loan
        return Loan.objects.get(user=self.user, name=name)

    def test_it_is_on_by_default_and_the_loan_amount_reaches_the_account(self):
        page = self.new_page(self.user)
        self.walk(page)
        self.confirm(page)
        loan = self.loan_made()
        received = loan.disbursements.get()
        self.assertEqual((received.account_id, received.amount), (self.bank.pk, D('1200000.00')))
        self.assertEqual(self.balance(), D('1240000.00'))

    def test_the_review_says_where_the_money_goes(self):
        page = self.new_page(self.user)
        self.walk(page)
        review = page.inner_text('#tmr-review-loan')
        self.assertIn('Add the loan amount to an account', review)
        self.assertRegex(review, r'Received in account\s*HDFC Savings')

    def test_turned_off_nothing_is_added(self):
        page = self.new_page(self.user)
        self.walk(page, received=False)
        self.assertNotIn('Received in account', page.inner_text('#tmr-review-loan'))
        self.confirm(page)
        self.assertEqual(self.loan_made().disbursements.count(), 0)
        self.assertEqual(self.balance(), D('40000.00'))

    def test_another_account_can_be_chosen(self):
        page = self.new_page(self.user)
        self.walk(page, account=self.cash)
        self.confirm(page)
        self.assertEqual(self.balance(self.cash), D('1203000.00'))
        self.assertEqual(self.balance(), D('40000.00'))

    def test_a_loan_taken_long_ago_hides_the_switch_and_adds_nothing(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'loan'}))
        page.wait_for_selector('#stepCounterBadge')
        form = page.locator('#tmr-flow-form-loan')
        self.assertTrue(form.locator('input[name="record_loan_received"]').is_visible())
        self.walk(page, mid_tenure=True)
        self.confirm(page)
        self.assertEqual(self.loan_made().disbursements.count(), 0)
        self.assertEqual(self.balance(), D('40000.00'))

    def test_the_switch_hides_when_mid_tenure_is_ticked_and_returns_when_it_is_not(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'loan'}))
        page.wait_for_selector('#stepCounterBadge')
        form = page.locator('#tmr-flow-form-loan')
        form.locator('input[name="name"]').fill('x')
        form.locator('input[name="principal"]').fill('1000')
        form.locator('input[name="annual_rate"]').fill('8')
        form.locator('input[name="tenure_months"]').fill('12')
        form.locator('input[name="start_date"]').fill(self.today.isoformat())
        for _ in range(2):
            form.locator('button:has-text("Next") >> visible=true').first.click()
            page.wait_for_timeout(300)
        form.locator('input[name="mid_tenure"]').check()
        form.locator('button:has-text("Back") >> visible=true').first.click()
        form.locator('button:has-text("Back") >> visible=true').first.click()
        page.wait_for_timeout(300)
        self.assertFalse(form.locator('input[name="record_loan_received"]').is_visible())
        self.assertFalse(form.locator('select[name="loan_received_account"]').is_visible())

    def test_it_works_together_with_a_down_payment(self):
        page = self.new_page(self.user)
        self.walk(page, down_payment=300000, name='Flat Loan')
        self.confirm(page)
        loan = self.loan_made('Flat Loan')
        self.assertEqual(loan.disbursements.get().amount, D('1200000.00'))
        self.assertEqual(self.balance(self.cash), D('3000') - D('300000'))
        self.assertEqual(loan.remaining_principal, D('900000.00'))

    def test_it_works_with_no_repayment_schedule(self):
        from expenses.models import RecurringTransaction
        page = self.new_page(self.user)
        self.walk(page, schedule=False, name='No schedule')
        self.confirm(page)
        loan = self.loan_made('No schedule')
        self.assertEqual(loan.disbursements.count(), 1)
        self.assertFalse(RecurringTransaction.objects.filter(loan=loan).exists())

    def test_the_new_loans_page_shows_what_was_received(self):
        page = self.new_page(self.user)
        self.walk(page)
        self.confirm(page)
        loan = self.loan_made()
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
        card = page.inner_text('#money-received-card')
        self.assertIn('HDFC Savings', card)
        self.assertIn('+₹12,00,000', card)

    def test_an_old_loan_added_before_this_is_left_alone(self):
        old = make_loan(self.user, 'Old loan', 300000, 36, '10', days_ago=300)
        page = self.open_loan(old)
        self.assertIn('No amount recorded as received yet', page.inner_text('#money-received-card'))
        self.assertEqual(self.balance(), D('40000.00'))
