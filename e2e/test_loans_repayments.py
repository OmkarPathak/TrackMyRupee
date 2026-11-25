"""Paying a loan: the Record Repayment window and its automatic split between principal and interest, deleting a
repayment, paying a loan off, making the repayment recurring, and the one-tap Pay EMI page."""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import confirm_delete, make_loan, make_repayment

D = Decimal
MODAL = '#repaymentModal'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('payer')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()
        self.loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)

    def balance(self, account=None):
        account = account or self.bank
        account.refresh_from_db()
        return account.balance

    def fresh(self, loan=None):
        loan = loan or self.loan
        loan.refresh_from_db()
        return loan

    def open_loan(self, loan=None):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-detail', kwargs={'pk': (loan or self.loan).uuid}))
        page.wait_for_selector('#tab-overview-btn')
        return page

    def open_modal(self, page):
        page.click('button[data-bs-target="#repaymentModal"]')
        page.wait_for_selector(MODAL + '.show')
        page.wait_for_timeout(400)

    def field(self, page, name):
        return page.input_value(f'{MODAL} input[name="{name}"]')

    def number(self, page, name):
        return D(self.field(page, name).replace(',', '') or '0')

    def type_amount(self, page, value, name='amount'):
        page.fill(f'{MODAL} input[name="{name}"]', value)
        page.press(f'{MODAL} input[name="{name}"]', 'Tab')
        page.wait_for_timeout(300)

    def post_repayment(self, page, **fields):
        """Send the form straight to the server, as another tool would (the window cannot make a split that does not
        add up), with the page's own CSRF token."""
        token = page.input_value(f'{MODAL} input[name="csrfmiddlewaretoken"]')
        data = {'csrfmiddlewaretoken': token, 'date': self.today.isoformat(), **fields}
        return page.request.post(self.live_server_url + reverse('loan-repayment-create', kwargs={'pk': self.loan.uuid}), form=data)

    def submit(self, page):
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)


class RecordRepaymentTests(Base):
    def test_the_window_opens_with_the_emi_and_its_split_filled_in(self):
        page = self.open_loan()
        self.open_modal(page)
        amount, principal, interest = (self.number(page, n) for n in ('amount', 'principal_portion', 'interest_portion'))
        self.assertGreater(amount, 0)
        self.assertEqual(principal + interest, amount)
        self.assertEqual(interest, D('9000.00'))                   # 12,00,000 at 9% a year: a month's interest
        self.assertEqual(page.input_value(f'{MODAL} select[name="from_account"]'), str(self.bank.pk))

    def test_recording_it_takes_the_money_out_and_lowers_what_is_owed(self):
        page = self.open_loan()
        self.open_modal(page)
        self.type_amount(page, '20000')
        self.submit(page)
        repayment = self.loan.repayments.get()
        self.assertEqual((repayment.amount, repayment.interest_portion, repayment.principal_portion),
                         (D('20000.00'), D('9000.00'), D('11000.00')))
        self.assertEqual(self.balance(), D('480000.00'))
        self.assertEqual(self.fresh().remaining_principal, D('1189000.00'))
        self.assertIn('Repayment recorded successfully', page.inner_text('body'))

    def test_changing_the_amount_recalculates_the_split(self):
        page = self.open_loan()
        self.open_modal(page)
        self.type_amount(page, '30000')
        self.assertEqual(self.number(page, 'interest_portion') + self.number(page, 'principal_portion'), D('30000'))
        self.assertEqual(self.number(page, 'interest_portion'), D('9000'))
        self.type_amount(page, '4000')            # less than a month's interest: it all counts as interest
        self.assertEqual(self.number(page, 'interest_portion'), D('4000'))
        self.assertEqual(self.number(page, 'principal_portion'), D('0'))

    def test_editing_one_part_of_the_split_fills_in_the_other(self):
        page = self.open_loan()
        self.open_modal(page)
        self.type_amount(page, '25000')
        self.type_amount(page, '20000', 'principal_portion')
        self.assertEqual(self.number(page, 'interest_portion'), D('5000'))
        self.type_amount(page, '7000', 'interest_portion')
        self.assertEqual(self.number(page, 'principal_portion'), D('18000'))
        self.submit(page)
        repayment = self.loan.repayments.get()
        self.assertEqual((repayment.principal_portion, repayment.interest_portion), (D('18000.00'), D('7000.00')))

    def test_a_split_that_does_not_add_up_is_refused(self):
        page = self.open_loan()
        response = self.post_repayment(page, from_account=str(self.bank.pk), amount='25000', principal_portion='20000',
                                       interest_portion='9000')
        self.assertLess(response.status, 400)
        self.assertEqual(self.loan.repayments.count(), 0)
        self.assertEqual(self.balance(), D('500000.00'))

    def test_a_repayment_of_nothing_is_refused(self):
        page = self.open_loan()
        self.open_modal(page)
        self.type_amount(page, '0')
        self.submit(page)
        self.assertEqual(self.loan.repayments.count(), 0)
        self.assertEqual(self.balance(), D('500000.00'))

    def test_a_repayment_dated_in_the_future_is_refused(self):
        page = self.open_loan()
        self.open_modal(page)
        page.fill(f'{MODAL} input[name="date"]', (self.today + timedelta(days=10)).isoformat())
        self.submit(page)
        self.assertEqual(self.loan.repayments.count(), 0)
        self.assertIn('cannot be in the future', page.inner_text('body'))

    def test_the_repayment_can_be_dated_in_the_past(self):
        page = self.open_loan()
        self.open_modal(page)
        past = self.today - timedelta(days=20)
        page.fill(f'{MODAL} input[name="date"]', past.isoformat())
        self.submit(page)
        self.assertEqual(self.loan.repayments.get().date, past)

    def test_a_second_click_on_add_does_not_record_it_twice(self):
        page = self.open_loan()
        self.open_modal(page)
        button = page.locator(f'{MODAL} button[type="submit"]')
        button.click()
        try:
            button.click(timeout=500)
        except Exception:
            pass
        page.wait_for_load_state('load')
        page.wait_for_timeout(800)
        self.assertEqual(self.loan.repayments.count(), 1)
        self.assertEqual(self.balance(), D('500000') - self.loan.repayments.get().amount)

    def test_paying_more_than_is_owed_only_takes_what_is_owed_and_closes_the_loan(self):
        small = make_loan(self.user, 'Nearly done', 30000, 12, '12', days_ago=100)
        page = self.open_loan(small)
        self.open_modal(page)
        self.type_amount(page, '500000')
        self.submit(page)
        repayment = small.repayments.get()
        self.assertEqual(repayment.principal_portion, D('30000.00'))
        self.assertLess(repayment.amount, D('40000'))
        self.assertFalse(self.fresh(small).is_active)
        self.assertEqual(self.balance(), D('500000') - repayment.amount)

    def test_the_account_of_another_person_cannot_pay_it(self):
        other = make_user('other-payer')
        theirs = make_account(other, 'Their account', 'SAVINGS_ACCOUNT', 1000)
        page = self.open_loan()
        response = self.post_repayment(page, from_account=str(theirs.pk), amount='5000', principal_portion='4000',
                                       interest_portion='1000')
        self.assertLess(response.status, 500)
        self.assertEqual(self.loan.repayments.count(), 0)
        self.assertEqual(self.balance(theirs), D('1000.00'))

    def test_the_balance_in_a_dollar_account_is_reduced_by_the_converted_amount(self):
        from expenses.models import FXRate
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=self.today,
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        dollars = make_account(self.user, 'USD Account', 'SAVINGS_ACCOUNT', 1000)
        dollars.currency = '$'
        dollars.save()
        page = self.open_loan()
        self.open_modal(page)
        self.type_amount(page, '16000')
        page.evaluate(f"document.querySelector('{MODAL} select[name=\"from_account\"]').value = '{dollars.pk}'")
        self.submit(page)
        self.assertEqual(self.balance(dollars), D('800.00'))       # ₹16,000 is $200


class DeleteRepaymentTests(Base):
    def delete_first(self, page):
        page.click('#tab-history-btn')
        page.wait_for_selector('#tab-history-pane table tbody tr')
        page.click('#tab-history-pane table button.no-caret >> nth=0')
        confirm_delete(self, page, '#tab-history-pane table .dropdown-item.text-danger >> nth=0')

    def test_deleting_a_repayment_gives_the_money_back_and_raises_what_is_owed(self):
        make_repayment(self.loan, self.bank, 8000, 9000, days_ago=40)
        self.assertEqual(self.balance(), D('483000.00'))
        page = self.open_loan()
        self.delete_first(page)
        self.wait_until(lambda: self.loan.repayments.count() == 0)
        self.assertEqual(self.balance(), D('500000.00'))
        self.assertEqual(self.fresh().remaining_principal, D('1200000.00'))

    def test_deleting_the_payment_that_closed_a_loan_reopens_it(self):
        small = make_loan(self.user, 'Closed one', 20000, 12, '12', days_ago=100)
        make_repayment(small, self.bank, 20000, 500, days_ago=2)
        self.assertFalse(self.fresh(small).is_active)
        page = self.open_loan(small)
        self.delete_first(page)
        self.wait_until(lambda: small.repayments.count() == 0)
        self.assertTrue(self.fresh(small).is_active)

    def test_a_cancelled_delete_keeps_the_repayment(self):
        make_repayment(self.loan, self.bank, 8000, 9000, days_ago=40)
        page = self.open_loan()
        page.click('#tab-history-btn')
        page.wait_for_selector('#tab-history-pane table tbody tr')
        page.click('#tab-history-pane table button.no-caret >> nth=0')
        page.click('#tab-history-pane table .dropdown-item.text-danger >> nth=0')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModal button[data-bs-dismiss="modal"]')
        page.wait_for_timeout(500)
        self.assertEqual(self.loan.repayments.count(), 1)


class RecurringRepaymentTests(Base):
    def test_a_repayment_can_be_made_recurring_from_the_window(self):
        from expenses.models import RecurringTransaction
        page = self.open_loan()
        self.open_modal(page)
        page.check(f'{MODAL} input[name="add_to_recurring"]')
        page.wait_for_selector(f'{MODAL} #repayment-frequency-container', state='visible')
        self.submit(page)
        schedule = RecurringTransaction.objects.get(user=self.user, transaction_type='LOAN')
        self.assertEqual((schedule.loan_id, schedule.account_id, schedule.frequency), (self.loan.pk, self.bank.pk, 'MONTHLY'))
        self.assertIn('Recurring loan repayment created', page.inner_text('body'))

    def test_a_second_identical_schedule_is_a_warning_not_a_crash(self):
        from expenses.models import RecurringTransaction
        page = self.open_loan()
        for _ in range(2):
            self.open_modal(page)
            self.type_amount(page, '20000')
            page.check(f'{MODAL} input[name="add_to_recurring"]')
            self.submit(page)
            self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='LOAN').count(), 1)

    def test_the_schedule_made_here_is_listed_with_the_subscriptions(self):
        page = self.open_loan()
        self.open_modal(page)
        page.check(f'{MODAL} input[name="add_to_recurring"]')
        self.submit(page)
        self.open(page, reverse('recurring-list'))
        self.assertIn('Loan EMI: Home Loan', page.inner_text('body'))


class PayEmiTests(Base):
    def test_one_tap_records_the_emi(self):
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        self.assertIn('Home Loan', page.inner_text('body'))
        self.assertIn('Outstanding', page.inner_text('body'))
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        repayment = self.loan.repayments.get()
        self.assertEqual(self.balance(), D('500000') - repayment.amount)

    def test_the_amount_can_be_changed_before_recording(self):
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        page.fill('input[name="amount"]', '25000')
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        self.assertEqual(self.loan.repayments.get().amount, D('25000.00'))

    def test_with_several_loans_the_person_picks_which_one(self):
        other = make_loan(self.user, 'Car Loan', 500000, 60, '10', days_ago=100)
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        page.click('a:has-text("Car Loan")')
        page.wait_for_url(lambda url: f'loan={other.uuid}' in url)
        self.assertIn('Car Loan', page.inner_text('main, .container, body'))
        self.assertIn(f'/loans/{other.uuid}/repayment/', page.get_attribute('form[action*="/repayment/"]', 'action'))
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        self.assertEqual(other.repayments.count(), 1)
        self.assertEqual(self.loan.repayments.count(), 0)

    def test_a_bad_amount_keeps_the_person_on_the_page(self):
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        page.fill('input[name="amount"]', '0')
        page.click('button:has-text("Record EMI")')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)
        self.assertEqual(self.loan.repayments.count(), 0)
        self.assertIn('/pay-emi', page.url)

    def test_with_no_loan_the_page_points_to_adding_one(self):
        from expenses.models import Loan
        Loan.objects.all().delete()
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        self.assertIn('You have not added a loan yet', page.inner_text('body'))
        self.assertIn('Add a loan manually', page.inner_text('body'))

    def test_a_double_tap_records_one_emi(self):
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        button = page.locator('button:has-text("Record EMI")')
        button.click()
        try:
            button.click(timeout=400)
        except Exception:
            pass
        page.wait_for_url(lambda url: '/pay-emi' not in url)
        self.assertEqual(self.loan.repayments.count(), 1)

    def test_it_returns_to_the_page_it_was_opened_from(self):
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi') + '?next=' + reverse('loan-list'))
        page.click('button:has-text("Record EMI")')
        page.wait_for_url(lambda url: url.rstrip('/').endswith('/loans'))
