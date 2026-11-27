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

    def fill_fd(self, page, payout=None, *, payout_account=None, amount=None, start=None, backfill=False, rd=None):
        """Fill the FD setup from its first step to its review step. `rd` = (rate, tenure months) puts the payouts into an RD."""
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
        self.next_step(page)                                  # -> the RD step (monthly payouts only) or Reporting
        if payout == 'MONTHLY':
            if rd:
                page.check('input[name="payout_to_rd"]')
                page.fill('input[name="rd_rate"]', str(rd[0]))
                page.fill('input[name="rd_tenure_months"]', str(rd[1]))
            self.next_step(page)                              # -> Reporting
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
        review = self.review_text(page, 'fd', '14,792', 'Interest Account', 'every month')
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
        self.assertIn('44,000', self.review_text(page, 'fd', '44,000'))
        self.confirm(page)
        self.wait_until(lambda: self.payout_schedules().exists(), 'no interest schedule was created', seconds=15)
        schedule = self.payout_schedules().get()
        self.assertEqual((schedule.frequency, schedule.amount, schedule.account), ('QUARTERLY', Decimal('44000.00'), self.funding))

    def test_without_a_payout_no_schedule_is_created_and_the_deposit_still_accrues(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page)
        self.assertNotIn('every month', self.review_text(page, 'fd', 'Fixed deposit account'))
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
        self.assertIn('14,792', self.review_text(page, 'fd', '14,792'))
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
        # 8,000.00 of interest over the year, paid out: the form counts a year the way the account page does (exactly one
        # year from 1 Jan to 1 Jan), not as 365 / 365.25 of one, which gave 7,994.52
        self.assertIn('8,000.00', paying)


class ClosingTheDepositTests(Base):
    """Closing or renewing an FD on the account form moves the end of its payout schedule."""

    def setUp(self):
        super().setUp()
        from expenses.models import Account, RecurringTransaction
        start = self.today - timedelta(days=60)
        self.fd = Account.objects.create(
            user=self.user, name='Paying FD', account_type='FD', balance=Decimal('0'), currency='₹',
            deposit_principal=Decimal('1200000'), deposit_rate=Decimal('7.5'), deposit_start_date=start,
            deposit_maturity_date=start + timedelta(days=730), deposit_compounding='QUARTERLY', deposit_payout='MONTHLY')
        self.schedule = RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=Decimal('7500'), currency='₹',
            account=self.interest_account, payout_for=self.fd, source='Interest Income', description='Interest payout: Paying FD',
            frequency='MONTHLY', start_date=start + timedelta(days=30), end_date=start + timedelta(days=730), is_active=True,
            last_processed_date=start + timedelta(days=30))

    def edit_terms(self, page, **fields):
        self.open(page, reverse('account-edit', kwargs={'pk': self.fd.pk}))
        page.click('#nextBtn')
        page.wait_for_timeout(300)
        for name, value in fields.items():
            page.fill(f'#id_{name}', value)
        page.locator('form button[type="submit"]:visible').first.click()
        page.wait_for_url(lambda url: '/edit/' not in url)

    def test_closing_the_deposit_on_the_account_form_ends_its_schedule_that_day(self):
        page = self.new_page(self.user)
        closed = self.today + timedelta(days=20)
        self.edit_terms(page, deposit_closed_date=closed.isoformat())
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.end_date, closed)

    def test_renewing_for_longer_moves_the_end_of_the_schedule(self):
        page = self.new_page(self.user)
        later = self.today + timedelta(days=900)
        self.edit_terms(page, deposit_maturity_date=later.isoformat())
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.end_date, later)

    def test_choosing_to_have_the_interest_added_instead_stops_the_schedule(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-edit', kwargs={'pk': self.fd.pk}))
        page.click('#nextBtn')
        page.wait_for_timeout(300)
        self.set_select(page, '#id_deposit_payout', '')
        page.locator('form button[type="submit"]:visible').first.click()
        page.wait_for_url(lambda url: '/edit/' not in url)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)


class RdFromPayoutTests(Base):
    """Each monthly payout can go straight into a recurring deposit."""

    def rd_records(self):
        from expenses.models import Account, RecurringTransaction
        rd = Account.objects.filter(user=self.user, account_type='RD').first()
        transfer = RecurringTransaction.objects.filter(user=self.user, transaction_type='TRANSFER').first()
        return rd, transfer

    def test_the_payouts_go_into_a_new_rd_from_the_first_step_to_the_confirmed_records(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account, rd=('6.5', 36))
        review = self.review_text(page, 'fd', 'Recurring deposit', 'for 36 months')
        self.assertIn('₹14,792 for 36 months', review)
        self.confirm(page)
        self.wait_until(lambda: self.rd_records()[1] is not None, 'the RD transfer was not created', seconds=15)
        rd, transfer = self.rd_records()
        self.assertEqual((rd.name, rd.deposit_rate, rd.account_type), ('Big FD interest RD', Decimal('6.50'), 'RD'))
        self.assertEqual((transfer.from_account, transfer.to_account, transfer.frequency, transfer.amount),
                         (self.interest_account, rd, 'MONTHLY', Decimal('14791.67')))

    def test_the_rd_step_appears_only_for_monthly_payouts(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        total = lambda: int(__import__('re').search(r'of (\d+)', page.inner_text('#stepCounterBadge')).group(1))
        without = total()
        self.set_select(page, 'select[name="interest_payout"]', 'QUARTERLY')
        page.wait_for_timeout(300)
        self.assertEqual(total(), without)
        self.set_select(page, 'select[name="interest_payout"]', 'MONTHLY')
        page.wait_for_timeout(300)
        self.assertEqual(total(), without + 1)

    def test_without_choosing_an_rd_none_is_created(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account)
        self.confirm(page)
        self.wait_until(lambda: self.rd_records()[0] is None and self.payout_schedule_exists(), 'no payout schedule', seconds=15)
        self.assertIsNone(self.rd_records()[1])

    def payout_schedule_exists(self):
        from expenses.models import RecurringTransaction
        return RecurringTransaction.objects.filter(user=self.user, source='Interest Income').exists()

    def test_the_rd_setup_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.fill_fd(page, 'MONTHLY', payout_account=self.interest_account, rd=('6.5', 24))
        self.assertIn('for 24 months', self.review_text(page, 'fd', 'Recurring deposit'))
        self.confirm(page)
        self.wait_until(lambda: self.rd_records()[1] is not None, 'the RD transfer was not created', seconds=15)

    def test_the_phone_circles_gain_one_for_the_rd_step_and_the_numbers_stay_in_order(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        DOTS = '.wizard-dot:visible'
        before = page.locator(DOTS).count()
        self.set_select(page, 'select[name="interest_payout"]', 'MONTHLY')
        page.wait_for_timeout(300)
        self.assertEqual(page.locator(DOTS).count(), before + 1)
        numbers = [page.locator(DOTS).nth(i).inner_text().strip() for i in range(before + 1)]
        self.assertEqual(numbers, [str(i + 1) for i in range(before + 1)])

    def test_six_circles_fit_inside_a_phone_screen(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.set_select(page, 'select[name="interest_payout"]', 'MONTHLY')
        page.wait_for_timeout(300)
        box = page.locator('.wizard-dots-row').bounding_box()
        self.assertLessEqual(box['x'] + box['width'], page.viewport_size['width'])
        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth'))
