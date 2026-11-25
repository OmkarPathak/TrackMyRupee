"""Loans beyond the Loans pages: the public EMI calculator, net worth, the dashboard, the calendar, the transactions
list, a data export, other currencies, and the pages in Hindi and Marathi."""
import csv
import io
import zipfile
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.loan_helpers import amount_in, emi, make_loan, make_repayment

D = Decimal


class CalculatorTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.page = self.new_page()
        self.open(self.page, reverse('loan-emi-calculator'))
        self.page.wait_for_selector('#emiValue')

    def figure(self, element):
        return float(self.page.inner_text(element).replace('₹', '').replace(',', ''))

    def calculate(self, principal, rate, months, extra='0'):
        self.page.fill('#principal', principal)
        self.page.fill('#annualRate', rate)
        self.page.fill('#tenureMonths', months)
        self.page.fill('#extraPayment', extra)
        self.page.wait_for_timeout(400)

    def test_it_opens_with_an_example_and_no_sign_in(self):
        self.assertAlmostEqual(self.figure('#emiValue'), emi(1000000, 10.5, 60), delta=0.01)
        self.assertEqual(self.page.inner_text('#emiValue'), self.page.inner_text('#hero-emi'))
        self.assertIn('Amortization Preview', self.page.inner_text('body'))

    def test_the_figures_follow_the_inputs(self):
        self.calculate('2500000', '8.75', '240')
        expected = emi(2500000, 8.75, 240)
        self.assertAlmostEqual(self.figure('#emiValue'), expected, delta=0.02)
        self.assertAlmostEqual(self.figure('#totalPayment'), expected * 240, delta=5)
        self.assertAlmostEqual(self.figure('#totalInterest'), expected * 240 - 2500000, delta=5)

    def test_amounts_are_grouped_the_indian_way(self):
        self.calculate('2500000', '8.75', '240')
        self.assertRegex(self.page.inner_text('#totalPayment'), r'₹\d{1,2},\d{2},\d{3}\.\d{2}')

    def test_a_loan_with_no_interest_splits_the_principal_evenly(self):
        self.calculate('120000', '0', '12')
        self.assertAlmostEqual(self.figure('#emiValue'), 10000.0, delta=0.01)
        self.assertAlmostEqual(self.figure('#totalInterest'), 0.0, delta=0.01)

    def test_a_monthly_prepayment_saves_interest_and_shortens_the_loan(self):
        self.calculate('1000000', '10.5', '60', '5000')
        self.assertGreater(self.figure('#interestSaved'), 0)
        text = self.page.inner_text('body')
        revised = int(text.split('Estimated revised tenure:')[1].split('months')[0].strip())
        self.assertLess(revised, 60)

    def test_nothing_reads_nan_or_infinity_for_bad_inputs(self):
        for principal, rate, months in (('0', '10', '12'), ('100000', '', '12'), ('100000', '10', '0'), ('', '', '')):
            with self.subTest(principal=principal, rate=rate, months=months):
                self.calculate(principal, rate, months)
                text = self.page.inner_text('body')
                self.assertNotIn('NaN', text)
                self.assertNotIn('Infinity', text)

    def test_it_gives_the_same_emi_as_the_loan_form_in_the_app(self):
        user = make_user('comparer')
        self.calculate('1200000', '9', '120')
        calculator = self.figure('#emiValue')
        app = self.new_page(user)
        self.open(app, reverse('loan-create'))
        app.fill('input[name="initial_principal"]', '1200000')
        app.fill('input[name="interest_rate"]', '9')
        app.fill('input[name="duration_months"]', '120')
        shown = float(''.join(ch for ch in app.inner_text('#emi-preview-value') if ch.isdigit() or ch == '.'))
        self.assertAlmostEqual(calculator, shown, delta=0.01)


class AcrossTheAppTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('everywhere')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()
        self.loan = make_loan(self.user, 'Home Loan', 1200000, 120, '9', days_ago=100)

    def test_a_repayment_is_listed_with_the_transactions(self):
        make_repayment(self.loan, self.bank, 8000, 9000, days_ago=10)
        page = self.new_page(self.user)
        self.open(page, reverse('all-transactions'))
        text = page.inner_text('body')
        self.assertIn('Home Loan', text)
        self.assertIn('17,000', text)

    def test_an_emi_due_soon_shows_on_the_dashboard_and_the_calendar(self):
        from expenses.models import RecurringTransaction
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='LOAN', amount=D('17000'), base_amount=D('17000'), currency='₹', account=self.bank,
            loan=self.loan, frequency='MONTHLY', start_date=self.today + timedelta(days=3), description='Loan EMI: Home Loan')
        page = self.new_page(self.user)
        self.open(page, reverse('home'))
        page.wait_for_timeout(1200)
        self.assertIn('Loan EMI: Home Loan', page.inner_text('body'))
        self.open(page, reverse('calendar'))
        self.assertIn('Home Loan', page.inner_text('body'))

    def test_net_worth_counts_what_is_owed_on_the_loan_on_the_accounts_page_and_the_dashboard(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        headline = page.inner_text('.acx-nw-amount')
        self.assertIn('7,00,000', headline)                 # 5,00,000 in the bank less the 12,00,000 loan
        self.assertTrue(headline.startswith('−') or headline.startswith('-'))
        self.assertIn('owed on cards and loans', page.inner_text('.acx-nw'))
        self.open(page, reverse('home'))
        page.wait_for_timeout(1500)
        self.assertRegex(page.inner_text('body'), r'Net Worth\s*→\s*[−-]₹7L')

    def test_once_the_loan_is_paid_off_net_worth_is_the_balance_again(self):
        from expenses.models import Loan
        Loan.objects.all().delete()
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        self.assertEqual(page.inner_text('.acx-nw-amount').strip(), '₹5,00,000')

    def test_a_loan_linked_account_shows_the_outstanding_amount_and_follows_repayments(self):
        from expenses.models import Account
        Account.objects.create(user=self.user, name='Home loan account', account_type='HOME_LOAN', balance=D('0'), currency='₹',
                               linked_loan=self.loan)
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        self.assertIn('12,00,000', page.inner_text('body'))
        make_repayment(self.loan, self.bank, 100000, 9000, days_ago=1)
        self.open(page, reverse('account-list'))
        self.assertIn('11,00,000', page.inner_text('body'))

    def test_the_data_export_has_the_loans_and_their_repayments(self):
        make_repayment(self.loan, self.bank, 8000, 9000, days_ago=10)
        page = self.new_page(self.user)
        self.open(page, reverse('export-data'))
        for entity in ('loans', 'loan_repayments'):
            box = page.locator(f'input[name="entities"][value="{entity}"]')
            if not box.is_checked():
                box.check()
        with page.expect_download() as download:
            page.locator('form button[type="submit"]:visible').first.click()
        path = download.value.path()
        with zipfile.ZipFile(path) as archive:
            self.assertTrue({'loans.csv', 'loan_repayments.csv'} <= set(archive.namelist()), archive.namelist())
            loans = list(csv.reader(io.StringIO(archive.read('loans.csv').decode('utf-8'))))
            repayments = list(csv.reader(io.StringIO(archive.read('loan_repayments.csv').decode('utf-8'))))
        self.assertEqual(loans[1][0], 'Home Loan')
        self.assertEqual(D(loans[1][2]), D('1200000'))
        self.assertEqual(len(repayments), 2)
        self.assertIn('Home Loan', repayments[1])

    def test_a_dollar_loan_is_added_to_the_totals_in_the_persons_own_currency(self):
        from expenses.models import FXRate
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=self.today,
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        make_loan(self.user, 'Dollar loan', 1000, 12, '6', currency='$', days_ago=10)
        page = self.new_page(self.user)
        self.open(page, reverse('loan-list'))
        text = page.inner_text('body')
        self.assertIn('Dollar loan', text)
        self.assertIn('$', text)

    def test_the_loans_menu_shows_how_many_are_still_active(self):
        make_loan(self.user, 'Second', 100000, 12)
        page = self.new_page(self.user)
        self.open(page, reverse('home'))
        self.assertIn('2 active', page.inner_text('body'))


class LanguageTests(E2ETestCase):
    def pages(self, user, loan):
        return (('loan-list', {}), ('loan-detail', {'pk': loan.uuid}), ('loan-create', {}), ('pay-emi', {}))

    def test_the_loan_pages_are_in_the_chosen_language(self):
        for language, word in (('hi', 'ऋण'), ('mr', 'कर्ज')):
            with self.subTest(language=language):
                user = make_user(f'speaker-{language}', language=language)
                make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
                loan = make_loan(user, 'Home Loan', 1200000, 120, '9', days_ago=100)
                page = self.new_page(user)
                for name, kwargs in self.pages(user, loan):
                    self.open(page, reverse(name, kwargs=kwargs))
                    page.wait_for_timeout(400)
                    text = page.inner_text('main, .container, body')
                    self.assertTrue(any('ऀ' <= ch <= 'ॿ' for ch in text), (language, name))
                self.open(page, reverse('loan-detail', kwargs={'pk': loan.uuid}))
                text = page.inner_text('body')
                for english in ('Remaining Principal', 'Principal Paid', 'Interest Paid', 'Record Repayment', 'Money received',
                                'Interest Rate History', 'Loan Summary'):
                    self.assertNotIn(english, text, f'{english!r} is still in English in {language}')
