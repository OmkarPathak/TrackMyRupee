"""The savings rate has ONE definition (expenses/savings.py). These tests pin that definition and
prove every screen, email and service reports the same number for the same data.

    savings = income - expenses - loan interest - capital events counted in averages
    rate    = savings / (income - cashback/refund income) * 100   (0 if that denominator <= 0)
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.management.commands.send_monthly_report import Command as MonthlyReportCommand
from expenses.models import (
    Account,
    CapitalEvent,
    Expense,
    FXRate,
    Income,
    Loan,
    LoanRepayment,
    UserProfile,
)
from expenses.savings import (
    CASHBACK_REFUND_TYPES,
    SavingsResult,
    calculate_savings,
    counted_capital_total,
    expense_total,
    income_totals,
    loan_interest_total,
    monthly_savings,
    savings_for_period,
)
from expenses.services import SalaryAnalysisService

D = Decimal


def today():
    return timezone.localdate()


class TestSavingsFormula(TestCase):
    def test_basic(self):
        r = calculate_savings(income=1000, operating_expenses=400)
        self.assertEqual((r.savings, r.denominator, r.rate), (D('600'), D('1000'), D('60')))

    def test_every_spending_component_reduces_savings(self):
        r = calculate_savings(income=1000, operating_expenses=100, loan_interest=50, capital_events=25)
        self.assertEqual(r.spending, D('175'))
        self.assertEqual(r.savings, D('825'))

    def test_cashback_and_refunds_count_as_income_but_not_in_the_denominator(self):
        r = calculate_savings(income=1300, cashback_refund=300, operating_expenses=400)
        self.assertEqual(r.savings, D('900'))        # all income is money in
        self.assertEqual(r.denominator, D('1000'))   # but the base excludes recoveries
        self.assertEqual(r.rate, D('90'))

    def test_no_income_or_non_positive_denominator_gives_zero_not_an_error(self):
        self.assertEqual(calculate_savings(income=0, operating_expenses=50).rate, D('0'))
        self.assertEqual(calculate_savings(income=200, cashback_refund=200, operating_expenses=50).rate, D('0'))
        self.assertEqual(calculate_savings(income=200, cashback_refund=300).rate, D('0'))
        self.assertEqual(calculate_savings().rate, D('0'))

    def test_overspending_is_a_negative_rate(self):
        r = calculate_savings(income=1000, operating_expenses=1500)
        self.assertEqual((r.savings, r.rate), (D('-500'), D('-50')))

    def test_extra_capital_events_are_added_on_top(self):
        r = calculate_savings(income=1000, capital_events=100, extra_capital_events=150)
        self.assertEqual((r.capital_events, r.savings), (D('250'), D('750')))

    def test_accepts_none_ints_floats_and_strings(self):
        r = calculate_savings(income='1000.50', cashback_refund=None, operating_expenses=400.25, loan_interest=0)
        self.assertEqual(r.savings, D('600.25'))

    def test_rounding_is_half_up(self):
        r = calculate_savings(income=D('3'), operating_expenses=D('1'))      # 66.666...
        self.assertEqual(r.rate_rounded(1), D('66.7'))
        self.assertEqual(r.rate_rounded(2), D('66.67'))
        self.assertEqual(calculate_savings(income=200, operating_expenses=D('199.9')).rate_rounded(1), D('0.1'))
        self.assertEqual(calculate_savings(income=D('1000'), operating_expenses=D('875')).rate_rounded(0), D('13'))  # 12.5 -> 13

    def test_result_is_immutable(self):
        with self.assertRaises(Exception):
            calculate_savings(income=1).income = D('5')

    def test_the_cashback_refund_types_are_the_two_documented_ones(self):
        self.assertEqual(set(CASHBACK_REFUND_TYPES), {'Cashback & Rewards', 'Refund / Reimbursement'})

    def test_loan_principal_has_no_place_in_the_definition(self):
        """There is deliberately no principal argument: repaying borrowed money is not spending."""
        with self.assertRaises(TypeError):
            calculate_savings(income=1, loan_principal=1)


class SavingsDbBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('sv', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        cache.clear()

    def other_user(self):
        user = User.objects.create_user('sv-other', password='x')
        UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        return user

    def income(self, amount, source_type='Salary', when=None, user=None):
        return Income.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                     source_type=source_type, currency='₹')

    def expense(self, amount, when=None, user=None):
        return Expense.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                      description='x', category='Food', currency='₹')

    def loan(self):
        return Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=100000,
                                   duration_months=24, start_date=date(2025, 1, 1), is_active=True)

    def repayment(self, loan, emi, interest, when=None, rate='1.0'):
        return LoanRepayment.objects.create(
            loan=loan, date=when or today(), amount=D(str(emi)), principal_portion=D(str(emi)) - D(str(interest)),
            interest_portion=D(str(interest)), exchange_rate=D(rate))

    def capital(self, amount, excluded, when=None, user=None):
        return CapitalEvent.objects.create(user=user or self.user, date=when or today(), amount=D(str(amount)),
                                           subtype='large_purchase', currency='₹', exclude_from_averages=excluded)


class TestSavingsHelpers(SavingsDbBase):
    def test_income_totals_split_out_cashback_and_refunds(self):
        self.income(1000)
        self.income(200, 'Cashback & Rewards')
        self.income(100, 'Refund / Reimbursement')
        self.income(50, 'Other')
        self.assertEqual(income_totals(Income.objects.filter(user=self.user)), (D('1350.00'), D('300.00')))

    def test_empty_querysets_are_zero(self):
        self.assertEqual(income_totals(Income.objects.none()), (D('0'), D('0')))
        self.assertEqual(expense_total(Expense.objects.none()), D('0'))
        self.assertEqual(loan_interest_total(LoanRepayment.objects.none()), D('0'))
        self.assertEqual(counted_capital_total(CapitalEvent.objects.none()), D('0'))

    def test_loan_interest_is_the_interest_portion_in_base_currency(self):
        loan = self.loan()
        self.repayment(loan, emi=4000, interest=800)
        foreign = self.repayment(loan, emi=4000, interest=100)
        LoanRepayment.objects.filter(pk=foreign.pk).update(exchange_rate=D('80'))   # foreign repayment: 100 * 80
        self.assertEqual(loan_interest_total(LoanRepayment.objects.filter(loan=loan)), D('8800.00'))

    def test_only_capital_events_counted_in_averages_are_included(self):
        self.capital(5000, excluded=True)
        self.capital(700, excluded=False)
        self.assertEqual(counted_capital_total(CapitalEvent.objects.filter(user=self.user)), D('700.00'))

    def test_foreign_income_and_expense_use_base_amounts(self):
        for src, dst, rate in (('USD', 'INR', '80'), ('INR', 'USD', '0.0125')):
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=today(),
                                            defaults={'rate': D(rate)})
        cache.clear()
        Income.objects.create(user=self.user, date=today(), amount=D('100'), source_type='Salary', currency='$')
        Expense.objects.create(user=self.user, date=today(), amount=D('10'), description='x', category='Food', currency='$')
        r = savings_for_period(self.user, today(), today())
        self.assertEqual((r.income, r.operating_expenses, r.savings), (D('8000.00'), D('800.00'), D('7200.00')))

    def test_period_bounds_are_inclusive_and_users_are_isolated(self):
        start, end = date(2026, 3, 1), date(2026, 3, 31)
        self.income(100, when=start)
        self.income(100, when=end)
        self.income(999, when=start - timedelta(days=1))
        self.income(999, when=end + timedelta(days=1))
        self.income(5000, user=self.other_user(), when=start)
        self.assertEqual(savings_for_period(self.user, start, end).income, D('200.00'))

    def test_savings_for_period_combines_every_component(self):
        loan = self.loan()
        self.income(10000)
        self.income(500, 'Cashback & Rewards')
        self.expense(1000)
        self.repayment(loan, emi=4000, interest=800)
        self.capital(300, excluded=False)
        self.capital(9000, excluded=True)
        r = savings_for_period(self.user, today(), today())
        self.assertEqual(r.income, D('10500.00'))
        self.assertEqual(r.spending, D('2100.00'))           # 1000 + 800 interest + 300 counted capital
        self.assertEqual(r.savings, D('8400.00'))
        self.assertEqual(r.rate, D('84'))                    # 8400 / 10000

    def test_monthly_savings_matches_savings_for_period_month_by_month(self):
        loan = self.loan()
        for month, scale in ((1, 1), (2, 2), (3, 3)):
            when = date(2026, month, 10)
            self.income(1000 * scale, when=when)
            self.income(100 * scale, 'Refund / Reimbursement', when=when)
            self.expense(300 * scale, when=when)
            self.repayment(loan, emi=500, interest=50 * scale, when=when)
            self.capital(70 * scale, excluded=False, when=when)
            self.capital(9999, excluded=True, when=when)
        by_month = monthly_savings(self.user, date(2026, 1, 1), date(2026, 3, 31))
        self.assertEqual(sorted(by_month), [(2026, 1), (2026, 2), (2026, 3)])
        for month in (1, 2, 3):
            start = date(2026, month, 1)
            end = date(2026, month, [31, 28, 31][month - 1])
            expected = savings_for_period(self.user, start, end)
            got = by_month[(2026, month)]
            self.assertEqual((got.income, got.cashback_refund, got.operating_expenses, got.loan_interest, got.capital_events),
                             (expected.income, expected.cashback_refund, expected.operating_expenses,
                              expected.loan_interest, expected.capital_events), month)
            self.assertEqual(got.rate, expected.rate)

    def test_monthly_savings_extra_capital_creates_and_adds_to_months(self):
        self.income(1000, when=date(2026, 5, 10))
        result = monthly_savings(self.user, date(2026, 5, 1), date(2026, 6, 30),
                                 extra_capital_by_month={(2026, 5): 250, (2026, 6): 40})
        self.assertEqual(result[(2026, 5)].savings, D('750.00'))
        self.assertEqual(result[(2026, 6)].savings, D('-40'))     # month with no activity still reports the extra spend

    def test_monthly_savings_ignores_activity_outside_the_window_and_other_users(self):
        self.income(1000, when=date(2026, 5, 10))
        self.income(1000, when=date(2026, 7, 10))
        self.income(9999, when=date(2026, 5, 10), user=self.other_user())
        result = monthly_savings(self.user, date(2026, 5, 1), date(2026, 6, 30))
        self.assertEqual(list(result), [(2026, 5)])
        self.assertEqual(result[(2026, 5)].income, D('1000.00'))

    def test_monthly_savings_uses_a_constant_number_of_queries(self):
        for m in range(1, 7):
            self.income(100, when=date(2026, m, 5))
            self.expense(10, when=date(2026, m, 6))
        with self.assertNumQueries(4):
            monthly_savings(self.user, date(2026, 1, 1), date(2026, 6, 30))


class TestEveryScreenReportsTheSameRate(SavingsDbBase):
    """One month of mixed activity; every consumer must land on exactly the same rate."""

    # income 10,000 salary + 500 cashback + 200 refund   -> denominator 10,000, income 10,700
    # spending: expenses 1,000 + interest 800 + counted capital 300 = 2,100 (principal 3,200 and
    # excluded capital 9,000 do NOT count)
    # savings 8,600 -> rate 86.0
    EXPECTED_RATE = 86.0

    def setUp(self):
        super().setUp()
        self.profile = self.user.profile
        self.profile.salary_date = 1
        self.profile.save()
        Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=0, currency='₹')
        loan = self.loan()
        self.income(10000)
        self.income(500, 'Cashback & Rewards')
        self.income(200, 'Refund / Reimbursement')
        self.expense(1000)
        self.repayment(loan, emi=4000, interest=800)
        self.capital(300, excluded=False)
        self.capital(9000, excluded=True)

    def month_bounds(self):
        first = today().replace(day=1)
        nxt = (first + timedelta(days=32)).replace(day=1)
        return first, nxt - timedelta(days=1)

    def test_core_calculation(self):
        r = savings_for_period(self.user, *self.month_bounds())
        self.assertEqual(float(r.rate), self.EXPECTED_RATE)
        self.assertEqual(r.savings, D('8600.00'))

    def test_dashboard_hero(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        hero = response.context['hero_metrics']
        self.assertEqual(hero['savings_rate'], self.EXPECTED_RATE)
        self.assertEqual(hero['saved'], D('8600.00'))
        self.assertEqual(hero['spent'], D('2100.00'))

    def test_analytics_monthly_series_and_ytd(self):
        response = self.client.get(reverse('analytics'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['balance_rate_data'][-1], self.EXPECTED_RATE)
        self.assertEqual(response.context['avg_balance_rate'], self.EXPECTED_RATE)

    def test_analytics_capital_toggle_adds_the_excluded_events(self):
        response = self.client.get(reverse('analytics'), {'include_capital_events': '1'})
        self.assertEqual(response.status_code, 200)
        with_toggle = response.context['balance_rate_data'][-1]
        # 8,600 minus the 9,000 excluded-from-averages event = -400 -> -4.0%
        self.assertEqual(with_toggle, -4.0)
        # ...and the YTD average (which has no toggle) is unaffected
        self.assertEqual(response.context['avg_balance_rate'], self.EXPECTED_RATE)

    def test_month_on_month_view(self):
        response = self.client.get(reverse('analytics-mom'))
        self.assertEqual(response.status_code, 200)
        summary = response.context['summary']
        self.assertEqual(summary['savings_rate'], self.EXPECTED_RATE)
        self.assertEqual(summary['total_savings'], 8600.0)

    def test_transactions_page(self):
        response = self.client.get(reverse('all-transactions'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['savings_rate'], self.EXPECTED_RATE)
        self.assertEqual(response.context['net_remaining'], D('8600.00'))

    def test_transactions_page_respects_its_filters(self):
        response = self.client.get(reverse('all-transactions'), {'time_period': 'last_month'})
        self.assertEqual(response.context['savings_rate'], 0)
        self.assertEqual(response.context['net_remaining'], D('0'))

    def test_monthly_email_report(self):
        first, last = self.month_bounds()
        data = MonthlyReportCommand().get_report_data(self.user, first, last)
        self.assertEqual(data['savings_rate'], self.EXPECTED_RATE)
        self.assertEqual(data['savings'], D('8600.00'))

    def test_monthly_email_footnote_describes_the_same_formula(self):
        from django.template.loader import render_to_string
        first, last = self.month_bounds()
        data = MonthlyReportCommand().get_report_data(self.user, first, last)
        html = render_to_string('emails/monthly_report.html', {
            'data': data, 'user': self.user, 'currency_symbol': '₹', 'month_name': 'X', 'report_url': '#',
            'period_label': 'X', 'start_date': first, 'end_date': last,
        })
        self.assertIn('Loan principal is repaying borrowed money', html)
        self.assertNotIn('Total Income − Total Outflow', html)

    def test_salary_cycle_service(self):
        metrics = SalaryAnalysisService.calculate_salary_cycle_metrics(self.user, today())
        self.assertEqual(metrics['savings_rate'], self.EXPECTED_RATE)
        self.assertEqual(metrics['savings'], 8600.0)
        self.assertEqual(metrics['total_loan_principal'], 3200.0)   # informational only

    def test_previous_month_comparison_uses_the_same_definition(self):
        prev_month_day = today().replace(day=1) - timedelta(days=2)
        self.income(2000, when=prev_month_day)
        self.income(400, 'Cashback & Rewards', when=prev_month_day)
        self.expense(500, when=prev_month_day)
        loan = Loan.objects.get(user=self.user)
        self.repayment(loan, emi=900, interest=100, when=prev_month_day)
        response = self.client.get(reverse('home'))
        prev = response.context['prev_month_data']
        # income 2400, denominator 2000, spend 500 + 100 interest -> savings 1800 -> 90%
        self.assertEqual(float(prev['savings_rate']), 90.0)
        self.assertEqual(prev['savings'], D('1800.00'))


class TestDocumentedExample(TestCase):
    def test_worked_example_in_the_analytics_guide(self):
        r = calculate_savings(income=51000, cashback_refund=1000, operating_expenses=20000, loan_interest=2000)
        self.assertEqual(r.savings, D('29000'))
        self.assertEqual(r.denominator, D('50000'))
        self.assertEqual(r.rate, D('58'))
