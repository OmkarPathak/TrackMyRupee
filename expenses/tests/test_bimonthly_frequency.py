"""A bill or income that repeats every two months (the electricity and water bills in Kerala): the frequency exists and
every place that works with a frequency (next date, monthly equivalent, forecast, calendar, filters, loan interest)
treats it as two months, not as monthly."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from expenses.forms import RecurringTransactionForm
from expenses.models import Account, Income, RecurringTransaction
from expenses.recurring_utils import calculate_recurring_equivalents, get_recurring_month_occurrence_amount
from expenses.services import LoanService
from expenses.services_recurring import RecurringService
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal
NEXT = RecurringTransaction.get_next_date


class ChoiceTests(TestCase):
    def test_every_two_months_is_offered_between_monthly_and_quarterly(self):
        values = [value for value, _ in RecurringTransaction.FREQUENCY_CHOICES]
        self.assertIn('BIMONTHLY', values)
        self.assertEqual(values.index('BIMONTHLY'), values.index('MONTHLY') + 1)
        self.assertEqual(values.index('QUARTERLY'), values.index('BIMONTHLY') + 1)

    def test_the_label_says_what_it_means(self):
        label = dict(RecurringTransaction.FREQUENCY_CHOICES)['BIMONTHLY']
        self.assertIn('2 Months', str(label))

    def test_the_recurring_form_accepts_it(self):
        field = RecurringTransactionForm().fields['frequency']
        self.assertIn('BIMONTHLY', [value for value, _ in field.choices])


class NextDateTests(TestCase):
    def test_the_next_date_is_two_months_on(self):
        self.assertEqual(NEXT(date(2026, 1, 15), 'BIMONTHLY', date(2026, 1, 15)), date(2026, 3, 15))

    def test_it_keeps_the_start_day_through_short_months(self):
        start = date(2026, 12, 31)
        first = NEXT(start, 'BIMONTHLY', start)
        second = NEXT(first, 'BIMONTHLY', start)
        third = NEXT(second, 'BIMONTHLY', start)
        self.assertEqual([first, second, third], [date(2027, 2, 28), date(2027, 4, 30), date(2027, 6, 30)])

    def test_a_date_between_two_occurrences_goes_to_the_next_one(self):
        self.assertEqual(NEXT(date(2026, 2, 20), 'BIMONTHLY', date(2026, 1, 15)), date(2026, 3, 15))

    def test_it_never_returns_a_date_that_is_not_after_the_current_one(self):
        start = date(2026, 1, 31)
        current = start
        for _ in range(12):
            following = NEXT(current, 'BIMONTHLY', start)
            self.assertGreater(following, current)
            current = following


class AmountTests(TestCase):
    def test_a_two_monthly_bill_is_half_a_month_and_six_a_year(self):
        monthly, yearly = calculate_recurring_equivalents('BIMONTHLY', D('1200'))
        self.assertEqual((monthly, yearly), (D('600'), D('7200')))

    def test_a_forecast_month_counts_it_only_in_every_second_month(self):
        schedule = SimpleNamespace(amount=D('1000'), base_amount=D('1000'), start_date=date(2026, 1, 10), end_date=None,
                                   frequency='BIMONTHLY')
        months = [get_recurring_month_occurrence_amount(schedule, 2026, m) for m in range(1, 7)]
        self.assertEqual(months, [D('1000'), 0, D('1000'), 0, D('1000'), 0])

    def test_a_month_before_the_start_has_nothing(self):
        schedule = SimpleNamespace(amount=D('1000'), base_amount=D('1000'), start_date=date(2026, 3, 10), end_date=None,
                                   frequency='BIMONTHLY')
        self.assertEqual(get_recurring_month_occurrence_amount(schedule, 2026, 1), 0)

    def test_loan_interest_for_a_two_month_period_is_two_months_of_interest(self):
        monthly = LoanService.period_interest(D('100000'), D('12'), 'MONTHLY')
        self.assertEqual(LoanService.period_interest(D('100000'), D('12'), 'BIMONTHLY'), monthly * 2)

    def test_the_period_a_schedule_covers_is_sixty_days(self):
        self.assertEqual(RecurringService.PERIOD_DAYS['BIMONTHLY'], 60)


class LastDueTests(TestCase):
    def test_the_last_due_date_is_on_the_two_month_schedule(self):
        with patch('django.utils.timezone.localdate', return_value=date(2026, 6, 20)):
            last = RecurringService.last_due_before_today(date(2026, 1, 15), 'BIMONTHLY')
        self.assertEqual(last, date(2026, 5, 15))


class ProcessingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('keralite', password='pw')
        self.bank = Account.objects.create(user=self.user, name='Federal Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')

    def test_a_two_monthly_schedule_posts_only_in_its_own_months(self):
        start = timezone.localdate() - relativedelta(months=4)
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=D('900'), currency='₹', base_amount=D('900'),
            description='Rent share', source='Rent', frequency='BIMONTHLY', start_date=start, account=self.bank)
        process_user_recurring_transactions(self.user, force=True)
        posted = Income.objects.filter(user=self.user)
        self.assertGreaterEqual(posted.count(), 1)
        for income in posted:
            gap = (income.date.year - start.year) * 12 + income.date.month - start.month
            self.assertEqual(gap % 2, 0, income.date)


class ListingTests(TestCase):
    def test_the_filter_offers_it(self):
        import inspect

        from expenses.filters import definitions
        self.assertIn('"BIMONTHLY"', inspect.getsource(definitions))

    def test_the_summary_report_counts_it_as_half_a_month(self):
        from expenses.portfolio_summary import MONTHLY_FACTOR
        self.assertEqual(MONTHLY_FACTOR['BIMONTHLY'], D(1) / D(2))
