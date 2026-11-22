"""Choosing Today in the time period menu narrows All Transactions to today's entries, on a computer and on a phone."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense
        self.user = make_user('todayfilter')
        cash = make_account(self.user, 'Cash', 'CASH_WALLET', 1000)
        today = timezone.localdate()
        for label, days in (('Chai today', 0), ('Dinner yesterday', 1)):
            Expense.objects.create(user=self.user, date=today - timedelta(days=days), amount=Decimal('50'),
                                   description=label, category='Food', account=cash, base_amount=Decimal('50'))

    def choose_today(self, page):
        self.open(page, reverse('all-transactions') + '?time_period=last_3_months')
        page.click('#tmr-time-dropdown-btn')
        page.click('[data-time-key="today"]')
        self.wait_until(lambda: 'Dinner yesterday' not in page.content(), 'the list was not narrowed')


class DesktopTests(Base):
    def test_today_narrows_the_list_and_the_button_says_so(self):
        page = self.new_page(self.user)
        self.choose_today(page)
        self.assertIn('Chai today', page.content())
        self.assertEqual(page.inner_text('.tmr-time-label').strip(), 'Today')

    def test_the_menu_shows_today_first_with_its_date(self):
        page = self.new_page(self.user)
        self.open(page, reverse('all-transactions'))
        page.click('#tmr-time-dropdown-btn')
        first = page.locator('.tmr-popover-list .tmr-popover-item').first
        self.assertEqual(first.get_attribute('data-time-key'), 'today')
        self.assertIn(timezone.localdate().strftime('%d %b'), first.inner_text())


class PhoneTests(Base):
    def test_it_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.choose_today(page)
        self.assertIn('Chai today', page.content())
