"""A fixed deposit that pays its interest out: set up in Guided Setup or on the account form, in a browser.

The deposit stays worth its principal, the payouts are recorded as a recurring income in the receiving account, and a
deposit that does not pay out is unchanged.
"""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('fd-payout')
        self.funding = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 3000000)
        self.interest_account = make_account(self.user, 'Interest Account', 'SAVINGS_ACCOUNT', 1000)
        self.today = timezone.localdate()

    def next_step(self, page):
        page.locator('#tmr-flow-form-fd button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(400)

    def fill_fd(self, page, payout=None, *, payout_account=None, amount=None, start=None, backfill=False):
        """Fill the FD setup from its first step to its review step."""
        page.fill('input[name="name"]', 'Big FD')
        page.fill('input[name="principal"]', '2500000')
        page.fill('input[name="annual_rate"]', '7.1')
        self.set_select(page, 'select[name="from_account"]', str(self.funding.pk))
        self.next_step(page)
        start = start or self.today
        page.fill('input[name="deposit_start_date"]', start.isoformat())
        page.fill('input[name="maturity_date"]', (start + timedelta(days=3 * 365)).isoformat())
        self.next_step(page)
        if payout:
            self.set_select(page, 'select[name="interest_payout"]', payout)
        if payout_account:
            self.set_select(page, 'select[name="payout_account"]', str(payout_account.pk))
        if amount:
            page.fill('input[name="payout_amount"]', amount)
        if backfill:
            page.check('input[name="create_historical_entries"]')
        self.next_step(page)                                  # -> Reporting
        self.next_step(page)                                  # -> Review
        page.wait_for_selector('#tmr-review-fd', state='visible')

    def confirm(self, page):
        page.locator('[data-confirm-btn] >> visible=true').first.click()      # (the phone has its own copy of the button)


class FlowTests(Base):
    def created_fd(self):
        from expenses.models import Account
        return Account.objects.get(user=self.user, name='Big FD')

    def payout_schedules(self):
        from expenses.models import RecurringTransaction
        return RecurringTransaction.objects.filter(user=self.user, transaction_type='INCOME', source='Interest Income')

    def test_a_monthly_payout_is_set_up_from_the_first_step_to_the_confirmed_records(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account)
        review = page.inner_text('#tmr-review-fd')
        self.assertIn('14,792', review)
        self.assertIn('Interest Account', review)
        self.assertIn('every month', review)
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)

        schedule = self.payout_schedules().get()
        self.assertEqual((schedule.frequency, schedule.amount, schedule.account),
                         ('MONTHLY', Decimal('14791.67'), self.interest_account))
        self.assertEqual(schedule.end_date, self.today + timedelta(days=3 * 365))
        fd = self.created_fd()
        self.assertEqual(fd.deposit_payout, 'MONTHLY')

    def test_the_deposit_stays_at_its_principal_on_the_accounts_page(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY')
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)
        from expenses.account_valuation import get_current
        self.assertEqual(get_current(self.created_fd()), Decimal('2500000.00'))
        page.wait_for_url(lambda url: '/flows/' not in url)        # (the setup sends you on to the dashboard)
        self.open(page, reverse('account-list'))
        self.assertIn('Big FD', page.inner_text('body'))

    def test_an_amount_typed_in_the_setup_is_the_amount_recorded(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'QUARTERLY', amount='44000')
        self.assertIn('44,000', page.inner_text('#tmr-review-fd'))
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)
        schedule = self.payout_schedules().get()
        self.assertEqual((schedule.frequency, schedule.amount, schedule.account), ('QUARTERLY', Decimal('44000.00'), self.funding))

    def test_without_a_payout_no_schedule_is_created_and_the_deposit_still_accrues(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page)
        self.assertNotIn('every month', page.inner_text('#tmr-review-fd'))
        self.confirm(page)
        from expenses.models import Account
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Big FD').exists(),
                        'the deposit was not created', seconds=15)
        self.assertIsNone(self.created_fd().deposit_payout)
        self.assertFalse(self.payout_schedules().exists())

    def interest_entries(self):
        from expenses.models import Income
        return Income.objects.filter(user=self.user, account=self.interest_account, source='Interest Income')

    def test_an_old_deposit_can_record_the_payouts_it_has_already_made(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account, start=self.today - timedelta(days=100),
                     backfill=True)
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)
        page.wait_for_url(lambda url: '/flows/' not in url)        # (the setup sends you on to the dashboard)
        self.open(page, reverse('account-list'))                 # opening a page records what has fallen due
        self.wait_until(lambda: self.interest_entries().count() == 3, 'the past payouts were not recorded', seconds=15)

    def test_an_old_deposit_without_the_option_records_no_past_payout(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account, start=self.today - timedelta(days=100))
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)
        page.wait_for_url(lambda url: '/flows/' not in url)        # (the setup sends you on to the dashboard)
        self.open(page, reverse('account-list'))
        page.wait_for_timeout(1000)
        self.assertEqual(self.interest_entries().count(), 0)

    def test_the_whole_setup_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account)
        self.assertIn('14,792', page.inner_text('#tmr-review-fd'))
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)


class AccountFormTests(Base):
    def open_form(self, page, kind):
        """Open the Add account page on a type, name the account and go on to the second step (the deposit terms)."""
        self.open(page, reverse('account-create') + f'?type={kind}')
        page.fill('#id_name', 'Manual FD')
        page.click('#nextBtn')
        page.wait_for_timeout(300)

    def test_the_choice_shows_for_a_fixed_deposit_and_not_for_a_savings_account(self):
        for kind, shown in (('FD', True), ('SAVINGS_ACCOUNT', False), ('RD', False)):
            page = self.new_page(self.user)
            self.open_form(page, kind)
            self.assertEqual(page.locator('label[for="id_deposit_payout"]').is_visible(), shown, kind)

    def test_saving_a_deposit_that_pays_out(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open_form(page, 'FD')
        page.fill('#id_deposit_principal', '100000')
        page.fill('#id_deposit_rate', '7')
        page.fill('#id_deposit_start_date', self.today.isoformat())
        page.fill('#id_deposit_maturity_date', (self.today + timedelta(days=365)).isoformat())
        self.set_select(page, '#id_deposit_payout', 'MONTHLY')
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Manual FD').exists(),
                        'the account was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Manual FD').deposit_payout, 'MONTHLY')

    def test_the_maturity_estimate_shows_the_principal_when_the_interest_is_paid_out(self):
        page = self.new_page(self.user)
        self.open_form(page, 'FD')
        page.fill('#id_deposit_principal', '100000')
        page.fill('#id_deposit_rate', '8')
        page.fill('#id_deposit_start_date', '2026-01-01')
        page.fill('#id_deposit_maturity_date', '2027-01-01')
        page.wait_for_timeout(300)
        accruing = page.inner_text('.maturity-estimate-card')
        self.assertNotIn('1,00,000.00', accruing.split('Interest')[0])       # grows past the principal
        self.set_select(page, '#id_deposit_payout', 'MONTHLY')
        page.wait_for_timeout(300)
        paying = page.inner_text('.maturity-estimate-card')
        self.assertIn('1,00,000.00', paying)
        self.assertIn('7,99', paying)                                        # 7,994.52 of interest over the year, paid out
