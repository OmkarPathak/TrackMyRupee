"""The pinned clock (expenses/tests/clock.py) reaches every place production code asks for "today".

Each case pins a date far from the real one and checks the call site sees the pinned date. If someone puts
``date.today()`` / ``datetime.now()`` back in one of these paths, the matching test fails.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from decimal import Decimal

from expenses.account_valuation import _calculate_fd_current
from expenses.flows.asset import CarFlow
from expenses.flows.recurring import RentBillFlow
from expenses.forms import IncomeForm
from expenses.models import Account, UserProfile
from expenses.tests.clock import pin_today
from expenses.views.utils import parse_month_year

PINNED_DAYS = [date(2031, 2, 28), date(2031, 12, 31)]


class PinnedClockReachesProductionCodeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='clockpin', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('100'), currency='₹')

    def test_parse_month_year_default(self):
        for day in PINNED_DAYS:
            with self.subTest(day=day), pin_today(day):
                request = RequestFactory().get('/')
                self.assertEqual(parse_month_year(request), (day.month, day.year))

    def test_income_form_initial_date(self):
        for day in PINNED_DAYS:
            with self.subTest(day=day), pin_today(day):
                form = IncomeForm(user=self.user)
                self.assertEqual(form['date'].initial, day)

    def test_flow_defaults_use_pinned_date(self):
        for day in PINNED_DAYS:
            with self.subTest(day=day), pin_today(day):
                rent = RentBillFlow().derive({'amount': 100})
                self.assertEqual(rent['start_date'], day)
                car = CarFlow().derive({'purchase_price': 100})
                self.assertEqual(car['acquisition_date'], day)

    def test_expense_list_days_left(self):
        for day, expected in [(date(2031, 2, 28), 0), (date(2031, 2, 10), 18), (date(2031, 12, 31), 0)]:
            with self.subTest(day=day), pin_today(day):
                # (log in under the pin: a session made on the real date would be long expired in 2031)
                self.client.force_login(self.user)
                response = self.client.get(reverse('expense-list'))
                self.assertEqual(response.status_code, 200, response.get('Location'))
                self.assertEqual(response.context['days_left'], expected)

    def test_deposit_valuation_default_today(self):
        # A deposit that started the day before the pinned date has earned something; seen from the real
        # date (years earlier) it would not have started yet and be worth just its principal.
        for day in PINNED_DAYS:
            with self.subTest(day=day), pin_today(day):
                value = _calculate_fd_current(Decimal('1000'), Decimal('10'), day - timedelta(days=30), 'SIMPLE')
                self.assertGreater(value, Decimal('1000'))
