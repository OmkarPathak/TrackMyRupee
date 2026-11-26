"""The "count all loan payments as spending" setting: off by default (only EMI interest is spending, principal repays debt);
on, the whole EMI and loan prepayments / down payments are spending, while net worth's change keeps the documented rule."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from django.core.cache import cache

from expenses.models import Account, CapitalEvent, Expense, UserProfile
from expenses.savings import calculate_savings, monthly_savings, savings_for_period, user_counts_loan_payments
from expenses.tests.test_savings_rate import SavingsDbBase, today

D = Decimal


class ResultTests(SavingsDbBase):
    def test_off_by_default_only_interest_is_spending(self):
        r = calculate_savings(income=1000, operating_expenses=200, loan_interest=50, loan_principal=300)
        self.assertEqual((r.spending, r.savings), (D('250'), D('750')))

    def test_on_the_whole_emi_is_spending(self):
        r = calculate_savings(income=1000, operating_expenses=200, loan_interest=50, loan_principal=300, count_loan_payments=True)
        self.assertEqual((r.spending, r.savings), (D('550'), D('450')))

    def test_net_saved_keeps_the_documented_rule_whatever_the_setting(self):
        for flag in (False, True):
            r = calculate_savings(income=1000, operating_expenses=200, loan_interest=50, loan_principal=300, loan_capital=100,
                                  count_loan_payments=flag)
            self.assertEqual(r.net_saved, D('750'), flag)

    def test_prepayments_and_down_payments_count_only_when_on(self):
        off = calculate_savings(income=1000, loan_capital=400)
        on = calculate_savings(income=1000, loan_capital=400, count_loan_payments=True)
        self.assertEqual((off.savings, on.savings), (D('1000'), D('600')))


class PeriodTests(SavingsDbBase):
    def setUp(self):
        super().setUp()
        self.income(10000)
        self.expense(1000)
        self.loan_obj = self.loan()
        self.repayment(self.loan_obj, 2000, 300)
        self.start = self.end = today()

    def test_the_users_setting_is_read_by_default(self):
        self.assertEqual(savings_for_period(self.user, self.start, self.end).savings, D('8700.00'))
        UserProfile.objects.filter(user=self.user).update(count_loan_payments_as_spending=True)
        self.user.refresh_from_db()
        self.assertTrue(user_counts_loan_payments(self.user))
        self.assertEqual(savings_for_period(self.user, self.start, self.end).savings, D('7000.00'))

    def test_an_explicit_flag_wins(self):
        self.assertEqual(savings_for_period(self.user, self.start, self.end, count_loan_payments=True).savings, D('7000.00'))

    def test_loan_down_payment_counts_when_on(self):
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('500'), subtype='loan_prepayment', currency='₹',
                                    exclude_from_averages=True)
        off = savings_for_period(self.user, self.start, self.end, count_loan_payments=False).savings
        on = savings_for_period(self.user, self.start, self.end, count_loan_payments=True).savings
        self.assertEqual(off, D('8700.00'))
        self.assertEqual(on, D('6500.00'))

    def test_monthly_savings_follows_the_flag(self):
        key = (today().year, today().month)
        off = monthly_savings(self.user, self.start, self.end, count_loan_payments=False)
        on = monthly_savings(self.user, self.start, self.end, count_loan_payments=True)
        self.assertEqual(off[key].savings - on[key].savings, D('1700.00'))


class SettingsScreenTests(SavingsDbBase):
    def setUp(self):
        super().setUp()
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True, app_tour_pending=False)
        self.user.refresh_from_db()
        bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('50000'))
        Expense.objects.create(user=self.user, date=today(), amount=D('100'), description='x', category='Food', account=bank)
        cache.clear()
    def test_default_is_off(self):
        self.assertFalse(self.user.profile.count_loan_payments_as_spending)

    def test_the_settings_page_offers_the_switch(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('profile-settings')), 'loan-payments-setting')

    def test_the_dashboard_notes_it_only_when_on(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse('home')), 'Counting all loan payments as spending')
        UserProfile.objects.filter(user=self.user).update(count_loan_payments_as_spending=True)
        self.assertContains(self.client.get(reverse('home')), 'Counting all loan payments as spending')
