"""Changing a loan's interest rate, and paying it down with a big purchase (a down payment or a prepayment linked to it);
paying a loan off, and what closing and reopening it does."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import confirm_delete, make_loan, make_repayment

D = Decimal
RATE = '#rateModal'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('ratepayer')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()
        self.loan = make_loan(self.user, 'Home Loan', 600000, 60, '9', days_ago=100)

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance

    def fresh(self, loan=None):
        loan = loan or self.loan
        loan.refresh_from_db()
        return loan

    def open_loan(self, query='', loan=None):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-detail', kwargs={'pk': (loan or self.loan).uuid}) + query)
        page.wait_for_selector('#tab-overview-btn')
        return page

    def update_rate(self, page, rate, effective=None):
        page.click('button[data-bs-target="#rateModal"]')
        page.wait_for_selector(RATE + '.show')
        page.wait_for_timeout(400)
        page.fill(f'{RATE} input[name="interest_rate"]', rate)
        if effective:
            page.fill(f'{RATE} input[name="effective_date"]', effective.isoformat())
        page.click(f'{RATE} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)


class RateChangeTests(Base):
    def test_a_new_rate_from_today_becomes_the_current_one(self):
        page = self.open_loan()
        self.update_rate(page, '10.5')
        self.assertEqual([str(r.interest_rate) for r in self.loan.interest_rates.order_by('effective_date')], ['9.00', '10.50'])
        self.assertIn('Interest rate updated successfully', page.inner_text('body'))
        self.assertIn('10.50%', page.inner_text('h4.text-primary'))

    def test_the_schedule_follows_the_new_rate(self):
        page = self.open_loan()
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        before = D(page.inner_text('#tab-schedule-pane table tbody tr:first-child td:nth-child(4)').replace('₹', '').replace(',', ''))
        self.update_rate(page, '12')
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        after = D(page.inner_text('#tab-schedule-pane table tbody tr:first-child td:nth-child(4)').replace('₹', '').replace(',', ''))
        self.assertGreater(after, before)
        self.assertAlmostEqual(float(after), 600000 * 0.12 / 12, delta=1)

    def test_a_rate_that_starts_later_does_not_change_today(self):
        page = self.open_loan()
        self.update_rate(page, '11', self.today + timedelta(days=45))
        self.assertEqual(self.loan.interest_rates.count(), 2)
        self.assertIn('9.00%', page.inner_text('h4.text-primary'))                # the rate in force today is still the first
        history = page.inner_text('#tab-overview-pane')
        self.assertIn('11.00%', history)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        first_interest = D(page.inner_text('#tab-schedule-pane table tbody tr:first-child td:nth-child(4)').replace('₹', '').replace(',', ''))
        self.assertAlmostEqual(float(first_interest), 600000 * 0.09 / 12, delta=1)

    def test_the_history_marks_the_rate_in_force_as_current_and_a_later_one_as_scheduled(self):
        page = self.open_loan()
        self.update_rate(page, '11', self.today + timedelta(days=45))
        card = page.locator('#tab-overview-pane .list-group-item')
        self.assertEqual(card.count(), 2)
        rows = {card.nth(i).inner_text(): i for i in range(card.count())}
        current = [text for text in rows if 'Current' in text]
        self.assertEqual(len(current), 1)
        self.assertIn('9.00%', current[0])

    def test_a_rate_outside_zero_to_a_hundred_is_refused(self):
        page = self.open_loan()
        self.update_rate(page, '101')
        self.assertEqual(self.loan.interest_rates.count(), 1)
        self.assertIn('Error updating interest rate', page.inner_text('body'))

    def test_changing_the_rate_leaves_repayments_already_made_alone(self):
        repayment = make_repayment(self.loan, self.bank, 8000, 4500, days_ago=30)
        page = self.open_loan()
        self.update_rate(page, '15')
        repayment.refresh_from_db()
        self.assertEqual((repayment.principal_portion, repayment.interest_portion), (D('8000.00'), D('4500.00')))
        self.assertEqual(self.balance(), D('487500.00'))

    def test_a_later_repayment_is_split_with_the_rate_in_force(self):
        page = self.open_loan()
        self.update_rate(page, '12')
        page.click('button[data-bs-target="#repaymentModal"]')
        page.wait_for_selector('#repaymentModal.show')
        page.wait_for_timeout(400)
        page.fill('#repaymentModal input[name="amount"]', '20000')
        page.press('#repaymentModal input[name="amount"]', 'Tab')
        page.click('#repaymentModal button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)
        self.assertEqual(self.loan.repayments.get().interest_portion, D('6000.00'))     # 6,00,000 at 12% for a month

    def test_the_rate_window_opens_on_todays_date(self):
        page = self.open_loan()
        page.click('button[data-bs-target="#rateModal"]')
        page.wait_for_selector(RATE + '.show')
        self.assertEqual(page.input_value(f'{RATE} input[name="effective_date"]'), self.today.isoformat())


class BigPurchaseTests(Base):
    def add_big_purchase(self, page, subtype, amount, loan=True, account=None):
        self.open(page, reverse('capital-event-create'))
        page.fill('input[name="amount"]', amount)
        self.set_select(page, 'select[name="subtype"]', subtype)
        if loan:
            self.set_select(page, 'select[name="linked_loan"]', str(self.loan.pk))
        if account:
            self.set_select(page, 'select[name="account"]', str(account.pk))
        page.click('form button[type="submit"]:has-text("Add")')
        page.wait_for_url(lambda url: '/capital-events/add' not in url)

    def test_a_down_payment_linked_to_the_loan_lowers_what_is_owed(self):
        from expenses.models import CapitalEvent
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_down_payment', '100000')
        event = CapitalEvent.objects.get(user=self.user)
        self.assertEqual((event.subtype, event.linked_loan_id), ('loan_down_payment', self.loan.pk))
        self.assertEqual(self.fresh().remaining_principal, D('500000.00'))
        self.assertEqual(self.balance(), D('400000.00'))
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
        text = page.inner_text('body')
        self.assertIn('Big purchases', text)
        self.assertIn('Loan Down Payment', text)
        self.assertIn('5,00,000', text)

    def test_a_prepayment_lowers_what_is_owed_and_the_balance_in_the_schedule(self):
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '200000')
        self.assertEqual(self.fresh().remaining_principal, D('400000.00'))
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}) + '?tab=schedule')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        first_balance = D(page.inner_text('#tab-schedule-pane table tbody tr:first-child td:last-child').replace('₹', '').replace(',', ''))
        self.assertLess(first_balance, D('400000'))

    def test_a_prepayment_is_not_counted_as_interest_or_a_repayment(self):
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '50000')
        self.assertEqual(self.loan.repayments.count(), 0)
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
        self.assertRegex(page.inner_text('body'), r'Interest Paid\s*₹0')

    def test_paying_off_the_whole_loan_with_a_prepayment_closes_it(self):
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '600000')
        self.assertFalse(self.fresh().is_active)
        self.open(page, reverse('loan-list') + '?status=inactive')
        page.wait_for_selector('#loans-shell')
        self.assertIn('Home Loan', page.inner_text('#loans-shell'))

    def test_a_prepayment_larger_than_the_balance_leaves_nothing_owed_and_never_less(self):
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '700000')
        loan = self.fresh()
        self.assertEqual(loan.remaining_principal, D('0.00'))
        self.assertFalse(loan.is_active)

    def test_deleting_the_prepayment_reopens_a_loan_it_had_closed(self):
        from expenses.models import CapitalEvent
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '600000')
        self.assertFalse(self.fresh().is_active)
        event = CapitalEvent.objects.get(user=self.user)
        self.open(page, reverse('capital-event-list'))
        page.click('button.dropdown-toggle.no-caret:visible >> nth=0')
        confirm_delete(self, page, f'button[data-url*="{reverse("capital-event-delete", kwargs={"pk": event.uuid})}"]:visible')
        self.wait_until(lambda: not CapitalEvent.objects.filter(pk=event.pk).exists())
        loan = self.fresh()
        self.assertTrue(loan.is_active)
        self.assertEqual(loan.remaining_principal, D('600000.00'))

    def test_editing_the_prepayment_changes_what_is_owed(self):
        from expenses.models import CapitalEvent
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'loan_prepayment', '100000')
        event = CapitalEvent.objects.get(user=self.user)
        self.open(page, reverse('capital-event-edit', kwargs={'pk': event.uuid}))
        page.fill('input[name="amount"]', '250000')
        page.click('form button[type="submit"]:has-text("Edit"), form button[type="submit"]:has-text("Save")')
        page.wait_for_url(lambda url: '/edit' not in url)
        self.assertEqual(self.fresh().remaining_principal, D('350000.00'))

    def test_a_big_purchase_of_another_kind_does_not_touch_the_loan(self):
        page = self.new_page(self.user)
        self.add_big_purchase(page, 'large_purchase', '90000', loan=False)
        self.assertEqual(self.fresh().remaining_principal, D('600000.00'))

    def test_the_loan_choice_appears_only_for_down_payments_and_prepayments(self):
        page = self.new_page(self.user)
        self.open(page, reverse('capital-event-create'))
        self.set_select(page, 'select[name="subtype"]', 'large_purchase')
        self.assertFalse(page.is_visible('#loan-link-row'))
        self.set_select(page, 'select[name="subtype"]', 'loan_prepayment')
        self.assertTrue(page.is_visible('#loan-link-row'))
        self.set_select(page, 'select[name="subtype"]', 'loan_down_payment')
        self.assertTrue(page.is_visible('#loan-link-row'))

    def test_only_the_persons_own_loans_can_be_linked(self):
        other = make_user('another-borrower')
        make_loan(other, 'Not yours')
        page = self.new_page(self.user)
        self.open(page, reverse('capital-event-create'))
        options = page.eval_on_selector_all('select[name="linked_loan"] option', 'els => els.map(e => e.textContent)')
        self.assertTrue(any('Home Loan' in text for text in options))
        self.assertFalse(any('Not yours' in text for text in options))


class PayOffTests(Base):
    def test_paying_the_last_of_it_moves_the_loan_to_paid_off_and_drops_the_active_count(self):
        second = make_loan(self.user, 'Second loan', 100000, 12, '10', days_ago=100)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        self.assertRegex(page.inner_text('#loans-shell'), r'Active\s*2')
        make_repayment(second, self.bank, 100000, 1000, days_ago=1)
        self.open(page, reverse('loan-list'))
        shell = page.inner_text('#loans-shell')
        self.assertRegex(shell, r'Active\s*1')
        self.assertRegex(shell, r'Paid Off\s*1')

    def test_a_paid_off_loan_can_be_reopened_by_raising_its_principal(self):
        done = make_loan(self.user, 'Reopen me', 50000, 12, '10', days_ago=100)
        make_repayment(done, self.bank, 50000, 500, days_ago=2)
        self.assertFalse(self.fresh(done).is_active)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-edit', kwargs={'pk': done.uuid}))
        page.fill('input[name="initial_principal"]', '80000')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)
        self.assertTrue(self.fresh(done).is_active)
        self.assertEqual(self.fresh(done).remaining_principal, D('30000.00'))

    def test_a_loan_that_is_paid_off_no_longer_offers_to_pay_its_emi(self):
        done = make_loan(self.user, 'Settled', 50000, 12, '10', days_ago=100)
        make_repayment(done, self.bank, 50000, 500, days_ago=2)
        page = self.new_page(self.user)
        self.open(page, reverse('pay-emi'))
        text = page.inner_text('body')
        self.assertIn('Home Loan', text)
        self.assertNotIn('Settled', text)
