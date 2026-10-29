"""Regression tests for the dashboard fixes made before the Insights Engine work:
  * a capital-event note is escaped on the Smart Insights card (it used to be rendered as HTML),
  * a salary-cycle user's category trend compares with the previous cycle (it used to find nothing),
  * "today" on the dashboard is the local date, and
  * the rupee-priced Fun Fact is only offered to rupee users.
"""

import datetime

from django.urls import reverse

from expenses.models import CapitalEvent, Category, UserProfile
from expenses.services import SalaryAnalysisService
from expenses.tests.test_dashboard_calculations import DashBase, D, today


class CapitalEventNoteIsEscapedTests(DashBase):
    def test_html_in_a_note_is_not_rendered(self):
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('5000'), currency='₹', subtype='other',
                                    account=self.cash, note='<script>alert(1)</script>')
        html = self.client.get(reverse('home')).content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)


class SalaryCycleCategoryTrendTests(DashBase):
    def test_trend_compares_with_the_previous_cycle(self):
        UserProfile.objects.filter(user=self.user).update(salary_date=5)
        self.user.refresh_from_db()
        Category.objects.filter(user=self.user, name='Food').update(limit=D('5000'))
        start, _ = SalaryAnalysisService.get_salary_cycle_dates(self.user, today())
        previous_day = start - datetime.timedelta(days=3)
        self.expense(1000, 'Food', when=previous_day)
        self.expense(400, 'Food', when=start)

        ctx = self.home()
        food = next(row for row in ctx['category_limits'] if row['name'] == 'Food')
        self.assertEqual(food['trend_dir'], 'down')
        self.assertEqual(food['trend_pct'], 60)


class DashboardUsesLocalDateTests(DashBase):
    def test_upcoming_payment_days_left_use_the_local_date(self):
        from expenses.models import RecurringTransaction

        # Saving a recurring item recomputes its next due date from the start date, so use a weekly item started five
        # days ago: its next due date is two days from now in every month (a monthly one depends on month lengths)
        started = today() - datetime.timedelta(days=5)
        due = today() + datetime.timedelta(days=2)
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=D('499'), currency='₹', description='Netflix',
            category='Food', frequency='WEEKLY', start_date=started, last_processed_date=started,
            next_due_date=due, account=self.cash, is_active=True)
        ctx = self.home()
        messages = [str(a['message']) for a in ctx['actionable_alerts'] if a['title'] == 'Upcoming Payment']
        self.assertEqual(len(messages), 1)
        self.assertIn('in 2 days', messages[0])


class FunFactIsForRupeesTests(DashBase):
    def test_not_offered_in_other_currencies(self):
        UserProfile.objects.filter(user=self.user).update(currency='$')
        self.user.refresh_from_db()
        self.expense(90000, 'Food')
        self.assertIsNone(self.home()['salary_breakdown']['relatable_metric'])
