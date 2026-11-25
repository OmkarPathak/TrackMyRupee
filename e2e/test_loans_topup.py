"""Borrowing more on a loan: the Top up window and its live preview, what a top-up does to what is owed, the term, the rate,
the schedule and the account it was paid into, the EMI it suggests, deleting it, and a paid-off loan it reopens."""
import csv
import io
import zipfile
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import confirm_delete, emi, make_loan, make_repayment

D = Decimal
MODAL = '#topupModal'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('borrower-more')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)
        self.today = timezone.localdate()
        self.loan = make_loan(self.user, 'Home Loan', 1000000, 120, '9', days_ago=200)

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance

    def fresh(self):
        self.loan.refresh_from_db()
        return self.loan

    def open_loan(self, query='', phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}) + query)
        page.wait_for_selector('#topups-card', state='attached')      # (on the Schedule tab the Overview is hidden)
        return page

    def open_window(self, page):
        page.click('.col-lg-5 button[data-bs-target="#topupModal"]:visible')
        page.wait_for_selector(MODAL + '.show')
        page.wait_for_timeout(400)

    def fill(self, page, amount=None, account=None, months=None, rate=None, note=None, when=None):
        if amount is not None:
            page.fill(f'{MODAL} input[name="amount"]', str(amount))
        if account is not None:
            self.set_select(page, f'{MODAL} select[name="received_account"]', str(account.pk) if account else '')
        if months is not None:
            page.fill(f'{MODAL} input[name="extra_months"]', str(months))
        if rate is not None:
            page.fill(f'{MODAL} input[name="new_rate"]', str(rate))
        if note is not None:
            page.fill(f'{MODAL} input[name="note"]', note)
        if when is not None:
            page.fill(f'{MODAL} input[name="date"]', when.isoformat())

    def save(self, page):
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)

    def top_up(self, page, **fields):
        self.open_window(page)
        self.fill(page, **fields)
        self.save(page)

    def emi_schedule(self, amount='13000', active=True):
        from expenses.models import RecurringTransaction
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D(amount), base_amount=D(amount), currency='₹', account=self.bank,
            loan=self.loan, frequency='MONTHLY', start_date=self.loan.start_date, is_active=active,
            last_processed_date=self.today - timedelta(days=5), description='Loan EMI: Home Loan')


class WindowTests(Base):
    def test_the_window_opens_on_todays_date_with_no_months_added(self):
        page = self.open_loan()
        self.open_window(page)
        self.assertEqual(page.input_value(f'{MODAL} input[name="date"]'), self.today.isoformat())
        self.assertIn(page.input_value(f'{MODAL} input[name="extra_months"]'), ('0', ''))
        self.assertIn('Top up this loan', page.inner_text(MODAL))

    def test_the_preview_shows_what_would_be_owed_and_the_emi(self):
        page = self.open_loan()
        self.open_window(page)
        self.assertTrue(page.locator('#topup-preview').is_hidden())
        self.fill(page, amount=200000)
        page.wait_for_timeout(300)
        self.assertTrue(page.locator('#topup-preview').is_visible())
        self.assertEqual(page.inner_text('#topup-owed').replace(',', ''), '₹1200000')
        months_left = 120 - ((self.today.year - self.loan.start_date.year) * 12 + self.today.month - self.loan.start_date.month)
        expected = emi(1200000, 9, months_left)
        shown = float(page.inner_text('#topup-emi').replace('₹', '').replace(',', ''))
        self.assertAlmostEqual(shown, expected, delta=1)

    def test_added_months_and_a_new_rate_change_the_preview(self):
        page = self.open_loan()
        self.open_window(page)
        self.fill(page, amount=200000)
        base = float(page.inner_text('#topup-emi').replace('₹', '').replace(',', ''))
        self.fill(page, months=60)
        longer = float(page.inner_text('#topup-emi').replace('₹', '').replace(',', ''))
        self.assertLess(longer, base)
        self.fill(page, months=0, rate=12)
        dearer = float(page.inner_text('#topup-emi').replace('₹', '').replace(',', ''))
        self.assertGreater(dearer, base)

    def test_a_loan_that_does_not_amortise_has_no_emi_in_the_preview(self):
        self.loan.repayment_type = 'BULLET'
        self.loan.save()
        page = self.open_loan()
        self.open_window(page)
        self.fill(page, amount=100000)
        self.assertTrue(page.locator('#topup-preview').is_visible())
        self.assertTrue(page.locator('#topup-emi-row').is_hidden())

    def test_the_preview_hides_again_when_the_amount_is_cleared(self):
        page = self.open_loan()
        self.open_window(page)
        self.fill(page, amount=100000)
        self.assertTrue(page.locator('#topup-preview').is_visible())
        self.fill(page, amount='')
        self.assertTrue(page.locator('#topup-preview').is_hidden())


class RecordTests(Base):
    def test_the_amount_alone_adds_to_what_is_owed_and_nothing_else(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=None)
        self.assertEqual(self.fresh().remaining_principal, D('1200000.00'))
        self.assertEqual(self.balance(), D('100000.00'))
        text = page.inner_text('body')
        self.assertIn('Top-up of ₹2,00,000 added', text)
        self.assertIn('You now owe ₹12,00,000', text)
        self.assertIn('+₹2,00,000', page.inner_text('#topups-card'))
        self.assertIn('Not added to an account', page.inner_text('#topups-card'))

    def test_the_loan_pages_figures_follow(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=None)
        text = page.inner_text('body')
        self.assertRegex(text, r'Remaining Principal\s*₹12,00,000')
        self.assertIn('₹10,00,000', page.inner_text('#tab-overview-pane'))      # the original principal is untouched
        self.assertIn('+₹2,00,000', page.inner_text('#summary-topups'))

    def test_paid_into_an_account_it_raises_the_balance_and_shows_as_money_received(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank, note='Renovation')
        self.assertEqual(self.balance(), D('300000.00'))
        received = page.inner_text('#money-received-card')
        self.assertIn('HDFC Savings', received)
        self.assertIn('+₹2,00,000', received)
        self.assertIn('Top-up', received)
        self.assertEqual(page.locator('#money-received-card button[data-url*="/received/"]').count(), 0)
        self.assertIn('Renovation', page.inner_text('#topups-card'))

    def test_it_shows_in_the_account_as_money_in_and_not_as_income(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank)
        self.open(page, reverse('account-detail', kwargs={'pk': self.bank.uuid}))
        self.assertIn('LOAN RECEIVED', page.inner_text('body').upper())
        from expenses.models import Income
        self.assertFalse(Income.objects.filter(user=self.user).exists())

    def test_a_new_rate_starts_on_the_day_of_the_top_up(self):
        page = self.open_loan()
        self.top_up(page, amount=100000, rate=11)
        self.assertEqual(sorted(str(r.interest_rate) for r in self.loan.interest_rates.all()), ['11.00', '9.00'])
        self.assertIn('11.00%', page.inner_text('h4.text-primary'))
        self.assertIn('Current', page.inner_text('#tab-overview-pane .list-group-item >> nth=0'))

    def test_added_months_lengthen_the_term_and_the_schedule(self):
        page = self.open_loan('?tab=schedule')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        before = page.locator('#tab-schedule-pane table tbody tr').count()
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
        self.top_up(page, amount=100000, months=24)
        self.assertIn('144 Months', page.inner_text('#tab-overview-pane'))
        self.assertIn('+24 months', page.inner_text('#topups-card'))
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}) + '?tab=schedule')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        page.wait_for_timeout(500)
        self.assertEqual(page.locator('#tab-schedule-pane table tbody tr').count(), before + 24)

    def test_the_schedule_starts_from_the_larger_balance(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=None)
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}) + '?tab=schedule')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        first_emi = float(page.inner_text('#tab-schedule-pane table tbody tr:first-child td:nth-child(2)').replace('₹', '').replace(',', ''))
        months_left = 120 - ((self.today.year - self.loan.start_date.year) * 12 + self.today.month - self.loan.start_date.month)
        self.assertAlmostEqual(first_emi, emi(1200000, 9, months_left), delta=1)

    def test_a_repayment_after_it_is_split_on_the_larger_balance(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=None)
        page.click('button[data-bs-target="#repaymentModal"]')
        page.wait_for_selector('#repaymentModal.show')
        page.wait_for_timeout(400)
        page.fill('#repaymentModal input[name="amount"]', '20000')
        page.press('#repaymentModal input[name="amount"]', 'Tab')
        page.click('#repaymentModal button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)
        self.assertEqual(self.loan.repayments.get().interest_portion, D('9000.00'))      # 9% a year on 12,00,000, for a month

    def test_a_second_click_records_it_once(self):
        page = self.open_loan()
        self.open_window(page)
        self.fill(page, amount=100000, account=self.bank)
        button = page.locator(f'{MODAL} button[type="submit"]')
        button.click()
        try:
            button.click(timeout=400)
        except Exception:
            pass
        page.wait_for_load_state('load')
        page.wait_for_timeout(800)
        self.assertEqual(self.loan.topups.count(), 1)
        self.assertEqual(self.balance(), D('200000.00'))

    def test_what_makes_no_sense_is_refused_and_nothing_is_saved(self):
        page = self.open_loan()
        for fields in (dict(amount=0), dict(amount=100000, when=self.today + timedelta(days=10)),
                       dict(amount=100000, when=self.loan.start_date - timedelta(days=1)), dict(amount=100000, months=900)):
            with self.subTest(fields=fields):
                self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
                self.open_window(page)
                self.fill(page, **fields)
                self.save(page)
                self.assertEqual(self.loan.topups.count(), 0)
                self.assertIn('Could not add the top-up', page.inner_text('body'))
        self.assertEqual(self.balance(), D('100000.00'))

    def test_a_rate_above_a_hundred_is_stopped_before_it_is_sent(self):
        page = self.open_loan()
        self.open_window(page)
        self.fill(page, amount=100000, rate=150)
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_timeout(600)
        self.assertTrue(page.is_visible(MODAL))                 # (the browser's own check keeps the window open)
        self.assertEqual(self.loan.topups.count(), 0)


class EmiTests(Base):
    def test_there_is_no_emi_card_without_a_schedule(self):
        page = self.open_loan()
        self.top_up(page, amount=200000)
        self.assertEqual(page.locator('#emi-card').count(), 0)

    def test_after_a_top_up_the_new_emi_is_suggested_and_not_applied(self):
        schedule = self.emi_schedule('13000')
        page = self.open_loan()
        self.top_up(page, amount=300000)
        self.assertIn('the EMI works out to about', page.inner_text('body'))
        self.assertTrue(page.locator('#emi-suggestion').is_visible())
        schedule.refresh_from_db()
        self.assertEqual(schedule.amount, D('13000.00'))

    def test_the_card_before_any_top_up_only_shows_the_schedule(self):
        self.emi_schedule('13000')
        page = self.open_loan()
        self.assertIn('₹13,000', page.inner_text('#emi-card'))
        self.assertEqual(page.locator('#emi-suggestion').count(), 0)

    def test_the_button_sets_the_schedule_to_the_suggested_emi(self):
        schedule = self.emi_schedule('13000')
        page = self.open_loan()
        self.top_up(page, amount=300000)
        suggested = float(page.inner_text('#emi-suggestion strong').replace('₹', '').replace(',', ''))
        page.click('#emi-suggestion button')
        page.wait_for_load_state('load')
        page.wait_for_timeout(500)
        schedule.refresh_from_db()
        self.assertAlmostEqual(float(schedule.amount), suggested, delta=1)
        self.assertIn('Your EMI schedule now posts', page.inner_text('body'))
        self.assertEqual(page.locator('#emi-suggestion').count(), 0)

    def test_a_loan_paid_off_and_reopened_offers_to_restart_its_schedule_without_catching_up(self):
        from expenses.models import RecurringTransaction
        make_repayment(self.loan, self.bank, 1000000, 0, days_ago=150)
        schedule = self.emi_schedule('13000', active=False)
        RecurringTransaction.objects.filter(pk=schedule.pk).update(last_processed_date=self.today - timedelta(days=150))
        page = self.open_loan()
        self.top_up(page, amount=300000, when=self.today)
        self.assertIn('Stopped', page.inner_text('#emi-card'))
        self.assertIn('Restart schedule at this EMI', page.inner_text('#emi-card'))
        before = self.loan.repayments.count()
        page.click('#emi-suggestion button')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)
        self.open(page, reverse('home'))
        page.wait_for_timeout(800)
        self.assertEqual(self.loan.repayments.count(), before)

    def test_a_bullet_loan_gets_no_suggestion(self):
        self.loan.repayment_type = 'BULLET'
        self.loan.save()
        self.emi_schedule('3000')
        page = self.open_loan()
        self.top_up(page, amount=100000)
        self.assertEqual(page.locator('#emi-suggestion').count(), 0)


class ReopenAndDeleteTests(Base):
    def test_a_top_up_reopens_a_paid_off_loan(self):
        make_repayment(self.loan, self.bank, 1000000, 0, days_ago=3)
        self.assertFalse(self.fresh().is_active)
        page = self.open_loan()
        self.top_up(page, amount=250000, when=self.today)
        self.assertTrue(self.fresh().is_active)
        self.open(page, reverse('loan-list'))
        self.assertIn('Home Loan', page.inner_text('#loans-shell'))
        self.assertRegex(page.inner_text('#loans-shell'), r'Active\s*1')

    def test_deleting_it_takes_everything_back(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank, months=12)
        self.assertEqual(self.balance(), D('300000.00'))
        topup = self.loan.topups.get()
        page.click(f'#topups-card button[data-url="{reverse("loan-topup-delete", kwargs={"pk": topup.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        page.wait_for_load_state('load')
        self.wait_until(lambda: self.loan.topups.count() == 0)
        self.assertEqual(self.balance(), D('100000.00'))
        self.assertEqual(self.fresh().remaining_principal, D('1000000.00'))
        self.assertEqual(self.fresh().total_duration_months, 120)
        self.assertIn('Top-up deleted', page.inner_text('body'))
        self.assertIn('No amount recorded as received yet', page.inner_text('#money-received-card'))

    def test_deleting_the_top_up_that_reopened_a_loan_closes_it_again(self):
        make_repayment(self.loan, self.bank, 1000000, 0, days_ago=3)
        page = self.open_loan()
        self.top_up(page, amount=250000, when=self.today)
        topup = self.loan.topups.get()
        page.click(f'#topups-card button[data-url="{reverse("loan-topup-delete", kwargs={"pk": topup.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        self.wait_until(lambda: self.loan.topups.count() == 0)
        self.assertFalse(self.fresh().is_active)

    def test_a_cancelled_delete_keeps_it(self):
        page = self.open_loan()
        self.top_up(page, amount=200000)
        topup = self.loan.topups.get()
        page.click(f'#topups-card button[data-url="{reverse("loan-topup-delete", kwargs={"pk": topup.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModal button[data-bs-dismiss="modal"]')
        page.wait_for_timeout(500)
        self.assertEqual(self.loan.topups.count(), 1)

    def test_deleting_the_loan_takes_its_top_up_money_out_of_the_account(self):
        from expenses.models import Loan
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank)
        self.open(page, reverse('loan-list'))
        page.click(f'button[data-url="{reverse("loan-delete", kwargs={"pk": self.loan.uuid})}"]')
        page.wait_for_selector('#deleteModal.show')
        page.click('#deleteModalConfirmBtn')
        self.wait_until(lambda: not Loan.objects.filter(pk=self.loan.pk).exists())
        self.assertEqual(self.balance(), D('100000.00'))


class AcrossTheAppTests(Base):
    def test_net_worth_falls_by_what_is_owed_unless_the_money_went_into_an_account(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        start = page.inner_text('.acx-nw-amount')
        open_loan = self.open_loan()
        self.top_up(open_loan, amount=200000, account=None)
        self.open(page, reverse('account-list'))
        lower = page.inner_text('.acx-nw-amount')
        self.assertNotEqual(start, lower)
        self.top_up(open_loan, amount=200000, account=self.bank)
        self.open(page, reverse('account-list'))
        self.assertIn('−', page.inner_text('.acx-nw-amount'))       # (more owed; the 2,00,000 received offsets one of the two)

    def test_the_dashboard_and_the_accounts_page_agree(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank)
        from expenses.ledger_read_service import LedgerReadService
        engine = LedgerReadService.get_net_worth(self.user)[0]
        self.open(page, reverse('account-list'))
        shown = page.inner_text('.acx-nw-amount').replace('₹', '').replace(',', '').replace('−', '-')
        self.assertEqual(D(shown), engine)

    def test_the_export_lists_the_top_ups_and_amounts_received(self):
        page = self.open_loan()
        self.top_up(page, amount=200000, account=self.bank, months=6, note='Renovation')
        self.open(page, reverse('export-data'))
        for entity in ('loan_topups', 'loan_disbursements'):
            box = page.locator(f'input[name="entities"][value="{entity}"]')
            if not box.is_checked():
                box.check()
        for entity in ('expenses', 'incomes', 'transfers', 'loan_repayments', 'recurring', 'goals', 'goal_contributions', 'accounts',
                       'capital_events', 'loans', 'categories', 'budget_limits'):
            box = page.locator(f'input[name="entities"][value="{entity}"]')
            if box.count() and box.is_checked():
                box.uncheck()
        with page.expect_download() as download:
            page.locator('form button[type="submit"]:visible').first.click()
        with zipfile.ZipFile(download.value.path()) as archive:
            topups = list(csv.reader(io.StringIO(archive.read('loan_topups.csv').decode('utf-8'))))
            received = list(csv.reader(io.StringIO(archive.read('loan_amounts_received.csv').decode('utf-8'))))
        self.assertEqual(topups[1][1:], ['Home Loan', '200000.00', '₹', '6', 'Renovation'])
        self.assertEqual(received[1][1:5], ['Home Loan', '200000.00', '₹', 'HDFC Savings'])

    def test_the_balance_chart_on_the_loan_page_still_draws_after_a_top_up(self):
        make_repayment(self.loan, self.bank, 8000, 9000, days_ago=100)
        make_repayment(self.loan, self.bank, 8100, 8900, days_ago=70)
        page = self.open_loan()
        self.top_up(page, amount=200000, account=None, when=self.today - timedelta(days=40))
        page.wait_for_timeout(1000)
        self.assertGreater(page.eval_on_selector('#loanTrendChart', 'c => c.width * c.height'), 0)

    def test_the_pages_in_hindi_have_the_new_words(self):
        user = make_user('hindi-topper', language='hi')
        make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)
        loan = make_loan(user, 'Home Loan', 1000000, 120, '9', days_ago=200)
        page = self.new_page(user)
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
        text = page.inner_text('body')
        self.assertIn('टॉप-अप', text)
        for english in ('Top up this loan', 'Top-ups', 'Money received'):
            self.assertNotIn(english, text)


class PhoneTests(Base):
    def test_a_top_up_can_be_added_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))
        page.click('button[data-bs-target="#topupModal"]:visible >> nth=0')
        page.wait_for_selector(MODAL + '.show')
        page.wait_for_timeout(400)
        page.fill(f'{MODAL} input[name="amount"]', '150000')
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)
        self.assertEqual(self.loan.topups.get().amount, D('150000.00'))

    def test_the_page_with_top_ups_fits_a_phone(self):
        page = self.open_loan(phone=True)
        self.top_up_phone(page)
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - window.innerWidth'), 2)

    def top_up_phone(self, page):
        page.click('button[data-bs-target="#topupModal"]:visible >> nth=0')
        page.wait_for_selector(MODAL + '.show')
        page.wait_for_timeout(400)
        page.fill(f'{MODAL} input[name="amount"]', '150000')
        self.set_select(page, f'{MODAL} select[name="received_account"]', str(self.bank.pk))
        page.click(f'{MODAL} button[type="submit"]')
        page.wait_for_load_state('load')
        page.wait_for_timeout(600)
