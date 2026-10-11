"""Calendar: the month grid's per-day numbers, the scheduled (pending) projection, the day panel,
search, and the views around them."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from expenses import calendar_data
from expenses.models import (
    Account, CapitalEvent, Expense, FXRate, Income, Loan, LoanRepayment, RecurringTransaction, Transfer,
    UserProfile,
)

D = Decimal
TODAY = datetime.date(2026, 10, 11)          # a Sunday; October 2026 starts on a Thursday


def d(day, month=10, year=2026):
    return datetime.date(year, month, day)


class CalBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cal-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        Account.objects.filter(user=self.user).delete()
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('1000000'), currency='₹')
        self.demat = Account.objects.create(user=self.user, name='Demat', account_type='DEMAT', balance=D('0'),
                                            currency='₹')
        self.savings = Account.objects.create(user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT',
                                              balance=D('0'), currency='₹')
        self.client.force_login(self.user)
        cache.clear()

    def expense(self, amount, when, category='Food', description='x', **kw):
        return Expense.objects.create(user=self.user, date=when, amount=D(str(amount)), category=category,
                                      currency='₹', account=self.cash, description=description, **kw)

    def income(self, amount, when, source_type='Other', description='pay', **kw):
        return Income.objects.create(user=self.user, date=when, amount=D(str(amount)), source=source_type,
                                     source_type=source_type, currency='₹', account=self.cash,
                                     description=description, **kw)

    def schedule(self, **kw):
        values = dict(user=self.user, transaction_type='EXPENSE', description='Netflix', amount=D('199'),
                      currency='₹', category='Food', frequency='MONTHLY', start_date=d(15, 9), account=self.cash)
        values.update(kw)
        return RecurringTransaction.objects.create(**values)

    def month(self, year=2026, month=10, search='', today=TODAY):
        weeks = calendar_data.build_month(self.user, year, month, today, search)
        return {c['day']: c for week in weeks for c in week if c}


class TestMonthHelpers(TestCase):
    def test_clamp_month_keeps_valid_values_and_repairs_the_rest(self):
        self.assertEqual(calendar_data.clamp_month(2026, 3, TODAY), (2026, 3))
        for bad in ((2026, 13), (2026, 0), (1999, 5), (9999, 1), ('x', 1), (None, None), (2026, 'abc')):
            self.assertEqual(calendar_data.clamp_month(*bad, TODAY), (2026, 10), bad)
        self.assertEqual(calendar_data.clamp_month(2076, 1, TODAY), (2076, 1))   # 50 years ahead is the limit
        self.assertEqual(calendar_data.clamp_month(2077, 1, TODAY), (2026, 10))

    def test_shift_month_wraps_years(self):
        self.assertEqual(calendar_data.shift_month(2026, 1, -1), (2025, 12))
        self.assertEqual(calendar_data.shift_month(2026, 12, 1), (2027, 1))
        self.assertEqual(calendar_data.shift_month(2026, 10, 14), (2027, 12))


class TestOccurrences(CalBase):
    def dates(self, rt, start, end):
        return calendar_data.occurrences(rt, start, end)

    def test_monthly_is_the_start_day_each_month(self):
        rt = self.schedule(start_date=d(15, 9))
        self.assertEqual(self.dates(rt, d(1), d(31)), [d(15)])
        self.assertEqual(self.dates(rt, d(1, 12), d(31, 12)), [d(15, 12)])

    def test_posted_occurrences_are_not_pending(self):
        rt = self.schedule(start_date=d(15, 9))
        RecurringTransaction.objects.filter(pk=rt.pk).update(last_processed_date=d(15, 10))
        rt.refresh_from_db()
        self.assertEqual(self.dates(rt, d(1), d(31)), [])
        self.assertEqual(self.dates(rt, d(1, 11), d(30, 11)), [d(15, 11)])

    def test_last_day_of_month_schedule_lands_on_real_month_ends(self):
        rt = self.schedule(start_date=d(31, 8), is_last_day_of_month=True)
        self.assertEqual(self.dates(rt, d(1, 9), d(31, 12)), [d(30, 9), d(31, 10), d(30, 11), d(31, 12)])
        self.assertEqual(self.dates(rt, d(1, 2, 2027), d(28, 2, 2027)), [d(28, 2, 2027)])

    def test_last_working_day_schedule_skips_weekends(self):
        rt = self.schedule(start_date=d(1, 9), is_last_working_day=True)
        # 31 Oct 2026 is a Saturday, so the last working day is Friday the 30th
        self.assertEqual(self.dates(rt, d(1), d(31)), [d(30)])

    def test_monthly_from_the_31st_does_not_drift_after_a_short_month(self):
        rt = self.schedule(start_date=d(31, 1))
        got = self.dates(rt, d(1, 2), d(30, 4))
        self.assertEqual(got, [d(28, 2), d(31, 3), d(30, 4)])

    def test_weekly_and_biweekly_and_daily(self):
        weekly = self.schedule(frequency='WEEKLY', start_date=d(1))
        self.assertEqual(self.dates(weekly, d(1), d(31)), [d(1), d(8), d(15), d(22), d(29)])
        biweekly = self.schedule(frequency='BIWEEKLY', start_date=d(1))
        self.assertEqual(self.dates(biweekly, d(1), d(31)), [d(1), d(15), d(29)])
        daily = self.schedule(frequency='DAILY', start_date=d(28))
        self.assertEqual(self.dates(daily, d(28), d(31)), [d(28), d(29), d(30), d(31)])

    def test_far_future_fixed_step_schedules_do_not_loop_for_ever(self):
        rt = self.schedule(frequency='DAILY', start_date=d(1, 1, 2020))
        far = self.dates(rt, d(1, 6, 2060), d(3, 6, 2060))
        self.assertEqual(far, [d(1, 6, 2060), d(2, 6, 2060), d(3, 6, 2060)])

    def test_end_date_stops_a_schedule(self):
        rt = self.schedule(frequency='WEEKLY', start_date=d(1), end_date=d(14))
        self.assertEqual(self.dates(rt, d(1), d(31)), [d(1), d(8)])

    def test_nothing_before_the_start_date(self):
        rt = self.schedule(start_date=d(20))
        self.assertEqual(self.dates(rt, d(1), d(19)), [])

    def test_quarterly_and_yearly(self):
        q = self.schedule(frequency='QUARTERLY', start_date=d(5, 7))
        self.assertEqual(self.dates(q, d(1), d(31, 12)), [d(5, 10)])
        y = self.schedule(frequency='YEARLY', start_date=d(5, 10, 2025))
        self.assertEqual(self.dates(y, d(1), d(31)), [d(5)])
        self.assertEqual(self.dates(y, d(1, 11), d(31, 12)), [])


class TestPendingByDay(CalBase):
    def test_kinds_and_base_currency_amounts(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=TODAY,
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        self.schedule(description='Rent', amount=D('1000'), start_date=d(5))
        self.schedule(description='Salary', transaction_type='INCOME', source='Salary', amount=D('5000'),
                      start_date=d(1))
        self.schedule(description='SIP', transaction_type='TRANSFER', from_account=self.cash,
                      to_account=self.demat, account=None, amount=D('2000'), start_date=d(7))
        self.schedule(description='Move', transaction_type='TRANSFER', from_account=self.cash,
                      to_account=self.savings, account=None, amount=D('300'), start_date=d(8))
        self.schedule(description='Hosting', amount=D('10'), currency='$', start_date=d(9))
        got = calendar_data.pending_by_day(self.user, d(1), d(31))
        self.assertEqual([(p['kind'], p['amount']) for p in got[d(5)]], [('out', D('1000.00'))])
        self.assertEqual(got[d(1)][0]['kind'], 'in')
        self.assertTrue(got[d(1)][0]['is_salary'])
        self.assertEqual(got[d(7)][0]['kind'], 'invest')
        self.assertEqual(got[d(8)][0]['kind'], 'transfer')
        self.assertEqual(got[d(9)][0]['amount'], D('800.00'), 'a foreign-currency schedule shows in your currency')

    def test_schedules_on_an_inactive_account_are_not_shown(self):
        self.schedule(start_date=d(5))
        Account.objects.filter(pk=self.cash.pk).update(is_active=False)
        self.assertEqual(calendar_data.pending_by_day(self.user, d(1), d(31)), {})

    def test_paused_and_other_users_schedules_are_not_shown(self):
        self.schedule(start_date=d(5), is_active=False)
        other = User.objects.create_user(username='cal-other', password='x')
        other_account = Account.objects.create(user=other, name='C', account_type='CASH_WALLET', balance=1, currency='₹')
        RecurringTransaction.objects.create(user=other, transaction_type='EXPENSE', description='Theirs',
                                            amount=D('5'), currency='₹', category='Food', frequency='MONTHLY',
                                            start_date=d(5), account=other_account)
        self.assertEqual(calendar_data.pending_by_day(self.user, d(1), d(31)), {})

    def test_search_filters_pending_entries(self):
        self.schedule(description='Netflix', start_date=d(5))
        self.schedule(description='Rent', start_date=d(6), category='Housing')
        self.assertEqual(list(calendar_data.pending_by_day(self.user, d(1), d(31), 'rent')), [d(6)])
        self.assertEqual(list(calendar_data.pending_by_day(self.user, d(1), d(31), 'housing')), [d(6)])


class TestMonthGrid(CalBase):
    def test_layout_has_blank_cells_around_the_month(self):
        weeks = calendar_data.build_month(self.user, 2026, 10, TODAY)
        self.assertEqual([c['day'] if c else None for c in weeks[0]], [None, None, None, None, 1, 2, 3])
        self.assertEqual(len(weeks), 5)
        self.assertEqual([c['day'] if c else None for c in weeks[-1]], [25, 26, 27, 28, 29, 30, 31])
        feb = calendar_data.build_month(self.user, 2024, 2, TODAY)          # leap year
        self.assertEqual(max(c['day'] for w in feb for c in w if c), 29)
        sunday_start = calendar_data.build_month(self.user, 2026, 2, TODAY)   # 1 Feb 2026 is a Sunday
        self.assertEqual(sunday_start[0][0]['day'], 1)

    def test_every_kind_of_money_movement_is_counted(self):
        self.income(50000, d(5))
        self.expense(300, d(5))
        loan = Loan.objects.create(user=self.user, name='Home', loan_type='HOME', initial_principal=D('100000'),
                                   duration_months=12, start_date=d(1, 9), currency='₹')
        LoanRepayment.objects.create(loan=loan, date=d(5), amount=D('10000'), principal_portion=D('9000'),
                                     interest_portion=D('1000'), from_account=self.cash)
        CapitalEvent.objects.create(user=self.user, date=d(5), amount=D('600000'), currency='₹',
                                    subtype='loan_prepayment', linked_loan=loan, account=self.cash)
        Transfer.objects.create(user=self.user, date=d(5), amount=D('2500'), from_account=self.cash,
                                to_account=self.demat)
        Transfer.objects.create(user=self.user, date=d(5), amount=D('900'), from_account=self.cash,
                                to_account=self.savings)         # your own money moving: not spending
        cell = self.month()[5]
        self.assertEqual((cell['income'], cell['income_count']), (D('50000.00'), 1))
        # expense 300 + the whole EMI 10,000 + the 6,00,000 prepayment
        self.assertEqual((cell['expense'], cell['expense_count']), (D('610300.00'), 3))
        self.assertEqual((cell['investment'], cell['investment_count']), (D('2500.00'), 1))
        self.assertEqual(cell['total_count'], 5)

    def test_amounts_are_in_the_users_currency(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=TODAY,
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=D('100'),
                                     currency='$')
        Expense.objects.create(user=self.user, date=d(6), amount=D('10'), currency='$', category='Food',
                               account=usd, description='x')
        self.assertEqual(self.month()[6]['expense'], D('800.00'))

    def test_other_users_and_other_months_are_left_out(self):
        other = User.objects.create_user(username='cal-other2', password='x')
        acc = Account.objects.create(user=other, name='C', account_type='CASH_WALLET', balance=1, currency='₹')
        Expense.objects.create(user=other, date=d(5), amount=D('77'), currency='₹', category='Food', account=acc,
                               description='theirs')
        self.expense(10, d(30, 9))
        self.expense(20, d(1, 11))
        self.assertTrue(all(c['expense'] == 0 for c in self.month().values()))

    def test_salary_day_star_from_income_and_from_a_pending_salary(self):
        self.income(1000, d(3), source_type='Salary')
        self.income(1000, d(4), description='Monthly salary credit')
        self.income(1000, d(6), source_type='Freelance')
        self.schedule(description='Pay', transaction_type='INCOME', source='Salary', start_date=d(27))
        days = self.month()
        self.assertEqual({n for n, c in days.items() if c['is_salary_day']}, {3, 4, 27})

    def test_today_and_the_three_day_due_soon_window(self):
        self.schedule(description='A', start_date=d(11, 9), frequency='MONTHLY')     # 11th: today
        self.schedule(description='B', start_date=d(14, 9))                           # 3 days away
        self.schedule(description='C', start_date=d(15, 9))                           # 4 days away
        self.schedule(description='D', start_date=d(10, 9))                           # yesterday (unposted)
        days = self.month()
        self.assertTrue(days[11]['is_today'])
        self.assertEqual({n for n, c in days.items() if c['due_soon']}, {11, 14})
        self.assertFalse(days[10]['due_soon'], 'an earlier, unposted day is not "due soon"')
        self.assertFalse(self.month(today=d(1, 11))[14]['due_soon'])

    def test_pending_totals_split_money_in_from_money_out(self):
        self.schedule(description='Rent', amount=D('1000'), start_date=d(5))
        self.schedule(description='Pay', transaction_type='INCOME', source='Other', amount=D('400'), start_date=d(5))
        cell = self.month()[5]
        self.assertEqual((cell['pending_out'], cell['pending_in']), (D('1000.00'), D('400.00')))
        self.assertEqual(len(cell['pending']), 2)

    def test_heat_intensity_follows_the_busiest_day(self):
        self.expense(1000, d(5))
        self.expense(600, d(6))
        self.expense(300, d(7))
        self.expense(100, d(8))
        days = self.month()
        self.assertEqual([days[n]['intensity'] for n in (5, 6, 7, 8, 9)], [4, 3, 2, 1, 0])

    def test_empty_month_has_no_heat(self):
        self.assertTrue(all(c['intensity'] == 0 for c in self.month().values()))

    def test_search_narrows_every_total(self):
        self.expense(100, d(5), description='Swiggy', category='Food')
        self.expense(200, d(5), description='Uber', category='Cab')
        self.income(900, d(5), description='Bonus')
        self.income(50, d(6), description='Refund', source_type='Refund / Reimbursement')
        Transfer.objects.create(user=self.user, date=d(7), amount=D('10'), from_account=self.cash,
                                to_account=self.demat, description='SIP')
        self.assertEqual(self.month(search='swiggy')[5]['expense'], D('100.00'))
        self.assertEqual(self.month(search='cab')[5]['expense'], D('200.00'))
        self.assertEqual(self.month(search='bonus')[5]['income'], D('900.00'))
        self.assertEqual(self.month(search='bonus')[5]['expense'], 0)
        self.assertEqual(self.month(search='refund')[6]['income'], D('50.00'))
        self.assertEqual(self.month(search='sip')[7]['investment'], D('10.00'))
        self.assertEqual(self.month(search='nothing like this')[5]['total_count'], 0)

    def test_query_count_is_small_and_does_not_grow_with_the_data(self):
        for n in range(1, 25):
            self.expense(10, d(n))
        with self.assertNumQueries(7):
            calendar_data.build_month(self.user, 2026, 10, TODAY)


class TestDayDetail(CalBase):
    def test_lists_every_kind_with_flow_and_totals(self):
        self.expense(300, d(5), description='Lunch', category='Food')
        self.income(5000, d(5), source_type='Salary', description='Salary')
        loan = Loan.objects.create(user=self.user, name='Home', loan_type='HOME', initial_principal=D('100000'),
                                   duration_months=12, start_date=d(1, 9), currency='₹')
        LoanRepayment.objects.create(loan=loan, date=d(5), amount=D('10000'), principal_portion=D('9000'),
                                     interest_portion=D('1000'), from_account=self.cash)
        CapitalEvent.objects.create(user=self.user, date=d(5), amount=D('700'), currency='₹', subtype='other',
                                    note='Sofa', account=self.cash)
        Transfer.objects.create(user=self.user, date=d(5), amount=D('2500'), from_account=self.cash,
                                to_account=self.demat)
        Transfer.objects.create(user=self.user, date=d(5), amount=D('900'), from_account=self.cash,
                                to_account=self.savings)
        detail = calendar_data.day_detail(self.user, d(5), TODAY)
        kinds = sorted(i['kind'] for i in detail['items'])
        self.assertEqual(kinds, ['capital', 'expense', 'income', 'invest', 'loan', 'transfer'])
        self.assertEqual(detail['income'], D('5000.00'))
        self.assertEqual(detail['outflow'], D('11000.00'))      # 300 + 10,000 EMI + 700
        self.assertEqual(detail['invested'], D('2500.00'))
        by_kind = {i['kind']: i for i in detail['items']}
        self.assertEqual(by_kind['expense']['account'], 'Cash')
        self.assertEqual(by_kind['transfer']['flow'], 'move')
        self.assertEqual(by_kind['capital']['title'], 'Sofa')

    def test_cell_totals_and_day_panel_agree(self):
        self.expense(300, d(5))
        self.income(5000, d(5))
        CapitalEvent.objects.create(user=self.user, date=d(5), amount=D('700'), currency='₹', subtype='other',
                                    account=self.cash)
        cell = self.month()[5]
        detail = calendar_data.day_detail(self.user, d(5), TODAY)
        self.assertEqual((cell['income'], cell['expense']), (detail['income'], detail['outflow']))

    def test_coming_up_groups_a_day_and_stops_after_three(self):
        self.schedule(description='Netflix', amount=D('199'), start_date=d(15))
        self.schedule(description='iCloud', amount=D('219'), start_date=d(15))
        self.schedule(description='Hosting', amount=D('100'), start_date=d(15))
        self.schedule(description='Gym', amount=D('500'), start_date=d(20))
        self.schedule(description='Pay', transaction_type='INCOME', source='Salary', amount=D('1200'),
                      start_date=d(13))
        self.schedule(description='Later', amount=D('1'), start_date=d(27))
        self.schedule(description='Even later', amount=D('1'), start_date=d(28))
        detail = calendar_data.day_detail(self.user, d(11), TODAY)
        self.assertEqual([c['date'] for c in detail['coming_up']], [d(13), d(15), d(20)])
        self.assertEqual(detail['coming_up'][0]['kind'], 'in')
        self.assertEqual(detail['coming_up'][1]['amount'], D('518.00'))
        self.assertTrue(detail['coming_up'][1]['title'].endswith('+1 more'))

    def test_scheduled_for_the_day_itself(self):
        self.schedule(description='Netflix', start_date=d(11))
        detail = calendar_data.day_detail(self.user, d(11), TODAY)
        self.assertEqual([p['description'] for p in detail['pending']], ['Netflix'])
        self.assertTrue(detail['is_today'])

    def test_search_filters_the_day(self):
        self.expense(10, d(5), description='Swiggy')
        self.expense(20, d(5), description='Uber')
        detail = calendar_data.day_detail(self.user, d(5), TODAY, 'uber')
        self.assertEqual([i['title'] for i in detail['items']], ['Uber'])


class TestCalendarViews(CalBase):
    def test_month_page_renders_and_follows_the_url(self):
        self.expense(100, d(5))
        response = self.client.get(reverse('calendar-month', args=[2026, 10]))
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertEqual((ctx['current_year'], ctx['current_month']), (2026, 10))
        self.assertEqual((ctx['prev_year'], ctx['prev_month'], ctx['next_year'], ctx['next_month']), (2026, 9, 2026, 11))
        self.assertContains(response, 'cal-cell')
        self.assertContains(response, 'October')

    def test_default_page_is_the_current_month_by_the_users_clock(self):
        with patch('expenses.views.misc.timezone.localdate', return_value=d(31, 12, 2026)):
            ctx = self.client.get(reverse('calendar')).context
        self.assertEqual((ctx['current_year'], ctx['current_month']), (2026, 12))
        self.assertEqual((ctx['next_year'], ctx['next_month']), (2027, 1))

    def test_a_crafted_url_falls_back_instead_of_failing(self):
        for url in ('/calendar/9999/12/', '/calendar/2026/13/', '/calendar/0/1/', '/calendar/1/12/'):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, url)

    def test_htmx_request_gets_only_the_shell(self):
        response = self.client.get(reverse('calendar-month', args=[2026, 10]), HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="calendar-shell"')
        self.assertNotContains(response, '<html')

    def test_search_keeps_its_text_and_navigation_links(self):
        self.expense(100, d(5), description='Swiggy')
        response = self.client.get(reverse('calendar-month', args=[2026, 10]), {'search': 'swi gg'})
        self.assertEqual(response.context['search_query'], 'swi gg')
        self.assertContains(response, 'search=swi%20gg')

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('calendar')).status_code, 302)
        self.assertEqual(self.client.get(reverse('calendar-day', args=[2026, 10, 5])).status_code, 302)

    def test_today_is_preselected_only_on_the_current_month(self):
        with patch('expenses.views.misc.timezone.localdate', return_value=TODAY):
            current = self.client.get(reverse('calendar-month', args=[2026, 10]))
            other = self.client.get(reverse('calendar-month', args=[2026, 11]))
        self.assertEqual(current.context['selected_iso'], '2026-10-11')
        self.assertEqual(other.context['selected_iso'], '')
        self.assertContains(current, 'hx-trigger="load"')
        self.assertNotContains(other, 'hx-trigger="load"')

    def test_colours_come_from_theme_tokens_not_fixed_values(self):
        css = open('static/css/calendar.css').read()
        for token in ('--color-success-text', '--color-danger-text', '--color-info-text', '--color-muted-text',
                      '--color-warning', '--bs-success-rgb'):
            self.assertIn(token, css)
        import re
        self.assertEqual(re.findall(r'#[0-9a-fA-F]{3,8}\b', css), [], 'no hard-coded hex colours')


class TestCalendarDayView(CalBase):
    def test_renders_the_day(self):
        self.expense(300, d(5), description='Lunch')
        response = self.client.get(reverse('calendar-day', args=[2026, 10, 5]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Lunch')
        self.assertContains(response, 'Mon, 5 Oct')
        self.assertContains(response, 'start_date=2026-10-05&end_date=2026-10-05')

    def test_invalid_or_out_of_range_dates_are_404(self):
        for args in ((2026, 2, 30), (2026, 13, 1), (2026, 0, 5), (1, 1, 1), (9999, 1, 1)):
            self.assertEqual(self.client.get(reverse('calendar-day', args=args)).status_code, 404, args)

    def test_empty_day_and_empty_search_messages(self):
        self.assertContains(self.client.get(reverse('calendar-day', args=[2026, 10, 6])), 'No activity on this day.')
        self.expense(5, d(6), description='x')
        self.assertContains(self.client.get(reverse('calendar-day', args=[2026, 10, 6]), {'search': 'zzz'}),
                            'matches your search')

    def test_rows_link_to_the_entry_and_never_to_other_users(self):
        expense = self.expense(300, d(5), description='Lunch')
        other = User.objects.create_user(username='cal-other3', password='x')
        acc = Account.objects.create(user=other, name='C', account_type='CASH_WALLET', balance=1, currency='₹')
        Expense.objects.create(user=other, date=d(5), amount=D('9'), currency='₹', category='Food', account=acc,
                               description='Secret')
        response = self.client.get(reverse('calendar-day', args=[2026, 10, 5]))
        self.assertContains(response, reverse('expense-edit', args=[expense.uuid]))
        self.assertNotContains(response, 'Secret')


class TestCalendarLayout(CalBase):
    """The header is one row, the grid fits the screen, and a tapped day scrolls to its panel."""

    def page(self):
        return self.client.get(reverse('calendar-month', args=[2026, 10])).content.decode()

    def test_back_month_and_search_share_one_header_row(self):
        html = self.page()
        head = html[html.index('class="cal-head"'):html.index('<!-- Legend -->')]
        self.assertIn('cal-nav', head)
        self.assertIn('cal-search-form', head)
        self.assertIn('Back to Dashboard', head)
        self.assertEqual(head.count('<h1'), 1)

    def test_grid_height_is_tied_to_the_viewport(self):
        css = open('static/css/calendar.css').read()
        self.assertRegex(css, r'\.cal-main\s*\{[^}]*height:\s*calc\(100dvh')
        self.assertRegex(css, r'\.cal-weeks \.cal-week\s*\{[^}]*flex:\s*1 1 0')

    def test_tapping_a_day_scrolls_to_the_panel_smoothly(self):
        html = self.page()
        self.assertIn("scrollIntoView({ behavior: 'smooth', block: 'start' })", html)
        self.assertIn("window.__calScrollToDay = true", html)
        # the initial load of today's panel must not scroll (only taps do)
        self.assertIn('hx-trigger="load"', html)

    def test_scripts_do_not_assume_bootstrap_is_loaded_yet(self):
        """bootstrap.bundle is deferred: touching it unguarded would stop the click handlers from binding."""
        html = self.page()
        self.assertIn('if (!window.bootstrap) { return; }', html)

    def test_day_panel_has_no_back_to_top_button_and_no_filler_height(self):
        response = self.client.get(reverse('calendar-day', args=[2026, 10, 5]))
        self.assertNotContains(response, 'cal-up')
        css = open('static/css/calendar.css').read()
        panel = css[css.index('.cal-panel {'):css.index('}', css.index('.cal-panel {'))]
        self.assertNotIn('min-height', panel)

    def test_arrows_are_centred_and_a_line_separates_the_weekdays_from_the_days(self):
        css = open('static/css/calendar.css').read()
        btn = css[css.index('.cal-nav-btn {'):css.index('}', css.index('.cal-nav-btn {'))]
        for needle in ('display: inline-flex', 'align-items: center', 'justify-content: center', 'padding: 0'):
            self.assertIn(needle, btn)
        weekdays = css[css.index('.cal-weekdays {'):css.index('}', css.index('.cal-weekdays {'))]
        self.assertIn('border-bottom', weekdays)


class TestDayCardTypography(CalBase):
    def test_long_titles_can_shrink_instead_of_overlapping_the_amount(self):
        css = open('static/css/calendar.css').read()
        text = css[css.index('.cal-row-text {'):css.index('}', css.index('.cal-row-text {'))]
        self.assertIn('min-width: 0', text)
        amount = css[css.index('.cal-row-amount {'):css.index('}', css.index('.cal-row-amount {'))]
        self.assertIn('flex: none', amount)
        title = css[css.index('.cal-row-title {'):css.index('}', css.index('.cal-row-title {'))]
        self.assertIn('text-overflow: ellipsis', title)

    def test_phone_sizes_are_smaller_than_desktop(self):
        css = open('static/css/calendar.css').read()
        phone = css[css.index('@media (max-width: 767.98px)'):]
        self.assertIn('.cal-row-title { font-size: .85rem; }', phone)
        self.assertIn('.cal-panel-title { font-size: 1rem;', phone)

    def test_day_rows_use_the_shrinkable_text_column(self):
        self.expense(5, d(5), description='A very long description that must not collide with the amount')
        html = self.client.get(reverse('calendar-day', args=[2026, 10, 5])).content.decode()
        self.assertIn('cal-row-text', html)
        self.assertNotIn('min-w-0', html)


class TestCellShapeAndLegend(CalBase):
    def css(self):
        return open('static/css/calendar.css').read()

    def test_small_screens_use_square_days(self):
        css = self.css()
        block = css[css.index('@media (max-width: 991.98px)'):css.index('@media (min-width: 992px)')]
        self.assertIn('.cal-cell, .cal-cell-blank { aspect-ratio: 1 / 1; }', block)
        self.assertIn('.cal-main { height: auto;', block)

    def test_wide_screens_cap_the_row_height_so_days_are_never_tall(self):
        css = self.css()
        wide = css[css.index('@media (min-width: 992px)'):]
        self.assertIn('container-type: inline-size', wide)
        self.assertRegex(wide, r'max-height:\s*calc\(\(100cqw[^;]*\) / 7 \* 0\.9\)')

    def test_each_legend_item_keeps_its_marker_and_label_together(self):
        css = self.css()
        item = css[css.index('.cal-legend > span {'):css.index('}', css.index('.cal-legend > span {'))]
        for needle in ('inline-flex', 'align-items: center', 'white-space: nowrap'):
            self.assertIn(needle, item)
        # the app's global ".ring" style adds a bottom margin; the legend marker must reset it
        ring = css[css.index('.cal-legend .ring {'):css.index('}', css.index('.cal-legend .ring {'))]
        self.assertIn('margin: 0', ring)


class TestPhoneSpacing(CalBase):
    def phone_css(self):
        css = open('static/css/calendar.css').read()
        return css[css.index('@media (max-width: 767.98px)'):]

    def test_phone_layout_is_not_cramped(self):
        phone = self.phone_css()
        self.assertIn('--cal-gap: .45rem', phone)
        self.assertIn('row-gap: .85rem', phone)            # header rows
        self.assertIn('gap: .5rem 1rem', phone)            # legend items
        self.assertIn('margin-top: 1.5rem', phone)         # air above the day card

    def test_salary_star_sits_in_the_corner_so_it_never_pushes_the_dots_out(self):
        phone = self.phone_css()
        star = phone[phone.index('.cal-star {'):phone.index('}', phone.index('.cal-star {'))]
        self.assertIn('position: absolute', star)
        self.assertIn('flex-wrap: nowrap', phone)


class TestSharedSearchBox(CalBase):
    def test_search_uses_the_same_markup_and_stylesheet_as_the_other_pages(self):
        html = self.client.get(reverse('calendar-month', args=[2026, 10])).content.decode()
        self.assertIn('tmr_filter.css', html)
        self.assertIn('class="tmr-search-box"', html)
        self.assertIn('tmr-search-icon', html)
        self.assertIn('class="tmr-search-input"', html)
        self.assertIn('placeholder="Search transactions..."', html)

    def test_clear_button_appears_only_with_a_search(self):
        url = reverse('calendar-month', args=[2026, 10])
        self.assertNotContains(self.client.get(url), 'tmr-search-clear-btn')
        self.assertContains(self.client.get(url, {'search': 'x'}), 'tmr-search-clear-btn')

    def test_calendar_css_has_no_search_styling_of_its_own(self):
        css = open('static/css/calendar.css').read()
        self.assertNotIn('.cal-search {', css)
        self.assertNotIn('.cal-search input', css)

    def test_phone_order_is_month_search_legend_grid_heat_then_day(self):
        html = self.client.get(reverse('calendar-month', args=[2026, 10])).content.decode()
        marks = ['cal-nav"', 'cal-search-form', 'class="cal-legend"', 'cal-weeks"', 'cal-heat-below', 'id="cal-day-panel"']
        positions = [html.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))
        css = open('static/css/calendar.css').read()
        phone = css[css.index('@media (max-width: 767.98px)'):]
        self.assertIn('.cal-nav { order: 1;', phone)
        self.assertIn('.cal-search-form { order: 2;', phone)
        self.assertNotIn('cal-pill-back', html)


class TestViewInTransactionsLink(CalBase):
    def test_link_is_a_custom_range_so_the_time_picker_does_not_say_this_month(self):
        response = self.client.get(reverse('calendar-day', args=[2026, 9, 15]))
        self.assertContains(response, 'time_period=custom&start_date=2026-09-15&end_date=2026-09-15')

    def test_the_transactions_page_filters_to_that_day_with_that_link(self):
        self.expense(10, d(15, 9), description='Inside')
        self.expense(20, d(16, 9), description='Outside')
        response = self.client.get(reverse('all-transactions'), {
            'time_period': 'custom', 'start_date': '2026-09-15', 'end_date': '2026-09-15'})
        self.assertEqual(response.context['applied_state']['time_period'], 'custom')
        self.assertContains(response, 'Inside')
        self.assertNotContains(response, 'Outside')

    def test_single_day_range_label_has_no_dash(self):
        js = open('static/js/tmr_filter.js').read()
        self.assertIn('if (sD === eD) return `${sD} ${months[sM - 1]} ${sY}`;', js)
