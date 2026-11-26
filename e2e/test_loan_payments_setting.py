"""The "Count all loan payments as spending" switch in Settings → Profile, driven as a person would: turn it on, see the
Dashboard's Spent and savings rate change with a note, see the note on Analytics, turn it off again."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

D = Decimal
SWITCH = '#loan-payments-setting input[type=checkbox]'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense, Income, Loan, LoanRepayment
        self.user = make_user('emi-payer')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        today = timezone.localdate()
        Income.objects.create(user=self.user, account=self.bank, amount=D('100000'), source='Salary', source_type='Salary', date=today, currency='₹')
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10000'), description='Rent', category='Rent', date=today, currency='₹')
        loan = Loan.objects.create(user=self.user, name='Car Loan', loan_type='CAR', initial_principal=D('500000'), duration_months=60,
                                   start_date=today, currency='₹')
        # an EMI of 20,000: 3,000 interest and 17,000 principal
        LoanRepayment.objects.create(loan=loan, from_account=self.bank, amount=D('20000'), principal_portion=D('17000'),
                                     interest_portion=D('3000'), date=today, base_amount=D('20000'))

    def set_switch(self, page, on):
        self.open(page, reverse('profile-settings'))
        page.wait_for_selector(SWITCH)
        if page.is_checked(SWITCH) != on:
            page.click('#loan-payments-setting .form-check-input')
        page.click('button[type=submit].btn-action-primary')
        page.wait_for_load_state('networkidle')

    def home(self, page):
        self.open(page, reverse('home'))
        page.wait_for_selector('#bento-card-spent')
        page.wait_for_timeout(800)
        return {
            'spent': page.inner_text('#bento-card-spent .bento-metric-value'),
            'rate': page.inner_text('.metric-main:has(span.fs-4)').replace('\n', ''),
            'note': page.locator('#loan-payments-note').count(),
        }


class SwitchTests(Base):
    def test_it_is_off_to_begin_with_and_the_dashboard_has_no_note(self):
        page = self.new_page(self.user)
        self.open(page, reverse('profile-settings'))
        page.wait_for_selector(SWITCH)
        self.assertFalse(page.is_checked(SWITCH))
        shown = self.home(page)
        self.assertEqual(shown['note'], 0)
        self.assertIn('87', shown['rate'])                     # (100000 - 10000 - 3000 interest) / 100000
        self.assertRegex(shown['spent'], r'₹13')

    def test_turning_it_on_counts_the_whole_emi_as_spending(self):
        page = self.new_page(self.user)
        self.set_switch(page, True)
        shown = self.home(page)
        self.assertEqual(shown['note'], 1)
        self.assertIn('70', shown['rate'])                     # (100000 - 10000 - 20000) / 100000
        self.assertRegex(shown['spent'], r'₹30')

    def test_the_choice_is_remembered_on_the_settings_page(self):
        page = self.new_page(self.user)
        self.set_switch(page, True)
        self.open(page, reverse('profile-settings'))
        page.wait_for_selector(SWITCH)
        self.assertTrue(page.is_checked(SWITCH))

    def test_turning_it_off_again_brings_back_the_interest_only_figures(self):
        page = self.new_page(self.user)
        self.set_switch(page, True)
        self.set_switch(page, False)
        shown = self.home(page)
        self.assertEqual(shown['note'], 0)
        self.assertIn('87', shown['rate'])
        self.assertRegex(shown['spent'], r'₹13')

    def test_the_net_worth_card_does_not_move_with_the_switch(self):
        page = self.new_page(self.user)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        before = page.inner_text('#bento-card-networth')
        self.set_switch(page, True)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        page.wait_for_timeout(800)
        self.assertEqual(page.inner_text('#bento-card-networth'), before)


class AnalyticsTests(Base):
    def test_analytics_notes_it_only_when_on(self):
        page = self.new_page(self.user)
        for name in ('analytics', 'analytics-cashflow'):
            self.open(page, reverse(name))
            page.wait_for_load_state('networkidle')
            self.assertEqual(page.locator('#loan-payments-note').count(), 0, name)
        self.set_switch(page, True)
        for name in ('analytics', 'analytics-cashflow'):
            self.open(page, reverse(name))
            page.wait_for_load_state('networkidle')
            self.assertEqual(page.locator('#loan-payments-note').count(), 1, name)


class PhoneTests(Base):
    def test_the_switch_is_usable_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.set_switch(page, True)
        self.assertEqual(self.home(page)['note'], 1)
