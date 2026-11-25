"""A loan's own page: the figures at the top, the Overview, Schedule and History tabs, and the different kinds of loan
(EMI, interest only, bullet, paid off)."""
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import make_loan, make_repayment

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('detailer')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def open_loan(self, loan, query=''):
        page = self.new_page(self.user)
        self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}) + query)
        page.wait_for_selector('#tab-overview-btn')
        return page


class FigureTests(Base):
    def test_the_top_figures_are_what_is_owed_paid_and_charged(self):
        loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)
        make_repayment(loan, self.bank, 8000, 9000, days_ago=40)
        page = self.open_loan(loan)
        text = page.inner_text('body')
        self.assertIn('Home Loan', page.inner_text('h1'))
        self.assertIn('11,92,000', text)           # remaining principal: 12,00,000 less the 8,000 repaid
        self.assertRegex(text, r'Principal Paid\s*₹8,?000')
        self.assertRegex(text, r'Interest Paid\s*₹9,?000')
        self.assertIn('9.00%', text)

    def test_large_amounts_are_grouped_the_same_way_in_every_figure(self):
        loan = make_loan(self.user, 'Big', 5000000, 240, '9', days_ago=100)
        make_repayment(loan, self.bank, 150000, 45000, days_ago=40)
        page = self.open_loan(loan)
        text = page.inner_text('body')
        self.assertRegex(text, r'Principal Paid\s*₹1,50,000')
        self.assertRegex(text, r'Interest Paid\s*₹45,000')
        self.assertRegex(text, r'Remaining Principal\s*₹48,50,000')

    def test_the_overview_lists_the_terms_and_the_rate_history(self):
        from expenses.models import LoanInterestRate
        loan = make_loan(self.user, 'Rates', 600000, 60, '8')
        LoanInterestRate.objects.create(loan=loan, interest_rate=D('9.5'), effective_date=loan.start_date.replace(year=loan.start_date.year + 1))
        page = self.open_loan(loan)
        overview = page.inner_text('#tab-overview-pane')
        self.assertIn('60 Months', overview)
        self.assertIn('8.00%', overview)
        self.assertIn('9.50%', overview)
        self.assertIn('Current', overview)

    def test_a_loan_with_nothing_repaid_says_there_is_no_trend_yet(self):
        loan = make_loan(self.user, 'Fresh', 300000, 36, '10', days_ago=2)
        page = self.open_loan(loan)
        self.assertIn('Record your first repayment', page.inner_text('body'))

    def test_the_charts_on_the_loan_page_are_drawn(self):
        loan = make_loan(self.user, 'Charts', 600000, 60, '9', days_ago=100)
        make_repayment(loan, self.bank, 8000, 4000, days_ago=40)
        make_repayment(loan, self.bank, 8100, 3900, days_ago=10)
        page = self.open_loan(loan)
        page.wait_for_timeout(1000)
        for chart in ('#loanBreakdownChart', '#loanTrendChart'):
            self.assertGreater(page.eval_on_selector(chart, 'c => c.width * c.height'), 0, chart)

    def test_the_back_arrow_returns_to_the_list(self):
        loan = make_loan(self.user, 'Back test')
        page = self.open_loan(loan)
        page.click('h1 >> xpath=../a')
        page.wait_for_url(lambda url: url.rstrip('/').endswith('/loans'))

    def test_the_extra_emi_tip_is_shown_for_an_emi_loan_and_not_for_a_bullet_loan(self):
        emi_loan = make_loan(self.user, 'Plain EMI', 1200000, 120, '9', days_ago=100)
        bullet = make_loan(self.user, 'Bullet', 500000, 24, '10', days_ago=100, repayment_type='BULLET')
        self.assertIn('Pay 1 Extra EMI/Year', self.open_loan(emi_loan).inner_text('body'))
        self.assertNotIn('Pay 1 Extra EMI/Year', self.open_loan(bullet).inner_text('body'))


class TabTests(Base):
    def test_the_schedule_tab_loads_the_remaining_months_and_ends_at_zero(self):
        loan = make_loan(self.user, 'Short loan', 120000, 12, '12', days_ago=100)
        page = self.open_loan(loan)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        rows = page.locator('#tab-schedule-pane table tbody tr')
        self.assertGreaterEqual(rows.count(), 8)
        last = rows.nth(rows.count() - 1).inner_text()
        self.assertRegex(last, r'₹0\s*$')

    def test_the_schedule_goes_down_every_month(self):
        loan = make_loan(self.user, 'Down', 600000, 60, '9', days_ago=100)
        page = self.open_loan(loan)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        balances = page.eval_on_selector_all('#tab-schedule-pane table tbody tr td:last-child',
                                             "els => els.map(e => Number(e.innerText.replace(/[^0-9.]/g, '')))")
        self.assertEqual(balances, sorted(balances, reverse=True))
        self.assertEqual(len(set(balances)), len(balances))

    def test_the_schedule_tab_can_be_opened_straight_from_a_link(self):
        loan = make_loan(self.user, 'Deep link', 600000, 60, '9', days_ago=100)
        page = self.open_loan(loan, '?tab=schedule')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        self.assertTrue(page.is_visible('#tab-schedule-pane'))

    def test_the_history_tab_lists_each_repayment_with_its_account(self):
        loan = make_loan(self.user, 'History', 600000, 60, '9', days_ago=100)
        make_repayment(loan, self.bank, 8000, 4000, days_ago=60)
        make_repayment(loan, self.bank, 8100, 3900, days_ago=30)
        page = self.open_loan(loan)
        page.click('#tab-history-btn')
        page.wait_for_selector('#tab-history-pane table tbody tr')
        text = page.inner_text('#tab-history-pane')
        self.assertEqual(page.locator('#tab-history-pane table tbody tr').count(), 2)
        self.assertIn('HDFC Savings', text)
        self.assertRegex(text.replace(',', ''), r'12000')
        self.assertRegex(text.replace(',', ''), r'12000')

    def test_the_history_tab_without_repayments_says_so(self):
        loan = make_loan(self.user, 'No history', 600000, 60, '9', days_ago=10)
        page = self.open_loan(loan)
        page.click('#tab-history-btn')
        page.wait_for_selector('#tab-history-pane .card')
        self.assertEqual(page.locator('#tab-history-pane table tbody tr').count(), 0)

    def test_a_paid_off_loan_has_no_schedule_left(self):
        loan = make_loan(self.user, 'Done', 50000, 12, '10', days_ago=100)
        make_repayment(loan, self.bank, 50000, 2000, days_ago=5)
        page = self.open_loan(loan)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane .card')
        self.assertEqual(page.locator('#tab-schedule-pane table tbody tr').count(), 0)
        self.assertIn('₹0', page.inner_text('body'))


class KindsOfLoanTests(Base):
    def test_an_interest_only_loan_pays_interest_each_month_and_the_principal_at_the_end(self):
        loan = make_loan(self.user, 'Interest only', 240000, 6, '12', days_ago=1, repayment_type='INTEREST_ONLY')
        page = self.open_loan(loan)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        principals = page.eval_on_selector_all('#tab-schedule-pane table tbody tr td:nth-child(3)',
                                               "els => els.map(e => Number(e.innerText.replace(/[^0-9.]/g, '')))")
        interests = page.eval_on_selector_all('#tab-schedule-pane table tbody tr td:nth-child(4)',
                                              "els => els.map(e => Number(e.innerText.replace(/[^0-9.]/g, '')))")
        self.assertTrue(all(p == 0 for p in principals[:-1]), principals)
        self.assertEqual(principals[-1], 240000)
        self.assertEqual(interests[0], 2400)

    def test_a_bullet_loan_opens_without_an_error_and_shows_its_balance(self):
        loan = make_loan(self.user, 'Bullet', 500000, 24, '10', days_ago=100, repayment_type='BULLET')
        page = self.open_loan(loan)
        self.assertIn('5,00,000', page.inner_text('body'))
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane .card')

    def test_a_loan_started_in_the_future_opens_and_owes_the_full_principal(self):
        loan = make_loan(self.user, 'Not yet', 300000, 36, '10', days_ago=-20)
        page = self.open_loan(loan)
        self.assertIn('3,00,000', page.inner_text('body'))
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        self.assertEqual(page.locator('#tab-schedule-pane table tbody tr').count(), 36)

    def test_a_loan_past_its_term_but_still_owing_shows_one_final_payment(self):
        loan = make_loan(self.user, 'Overdue', 100000, 3, '10', days_ago=300)
        page = self.open_loan(loan)
        page.click('#tab-schedule-btn')
        page.wait_for_selector('#tab-schedule-pane table tbody tr')
        self.assertEqual(page.locator('#tab-schedule-pane table tbody tr').count(), 1)

    def test_a_loan_in_dollars_shows_its_own_currency(self):
        loan = make_loan(self.user, 'Dollar loan', 50000, 24, '6', currency='$', days_ago=60)
        page = self.open_loan(loan)
        text = page.inner_text('body')
        self.assertIn('$50,000', text.replace(' ', ''))
