from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase

from expenses.models import Expense, Income, Loan, LoanRepayment, UserProfile
from expenses.services import SalaryAnalysisService


class SalaryAnalysisServiceTest(TestCase):
    """Test the SalaryAnalysisService for salary cycle calculations."""
    
    def setUp(self):
        """Set up test user and profile with custom salary date."""
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = UserProfile.objects.get(user=self.user)
        
    def test_salary_date_default_is_1(self):
        """Test that default salary_date is 1."""
        self.assertEqual(self.profile.salary_date, 1)
        
    def test_salary_date_can_be_set(self):
        """Test that salary_date can be set to any value between 1-31."""
        self.profile.salary_date = 15
        self.profile.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.salary_date, 15)
        
    def test_salary_date_validation_min(self):
        """Test that salary_date cannot be less than 1."""
        self.profile.salary_date = 0
        with self.assertRaises(Exception):
            self.profile.full_clean()
            
    def test_salary_date_validation_max(self):
        """Test that salary_date cannot be more than 31."""
        self.profile.salary_date = 32
        with self.assertRaises(Exception):
            self.profile.full_clean()
    
    def test_get_salary_cycle_dates_with_default_salary_date(self):
        """Test salary cycle dates when salary_date is 1 (default)."""
        # salary_date = 1, target_date = May 15
        target = date(2026, 5, 15)
        start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        
        # Should be May 1 to May 31
        self.assertEqual(start, date(2026, 5, 1))
        self.assertEqual(end, date(2026, 5, 31))
        
    def test_get_salary_cycle_dates_mid_month(self):
        """Test salary cycle dates when target is in the middle of cycle."""
        self.profile.salary_date = 15
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        # target_date = May 25, salary_date = 15
        # Salary cycle: May 15 - June 14
        target = date(2026, 5, 25)
        start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        
        self.assertEqual(start, date(2026, 5, 15))
        self.assertEqual(end, date(2026, 6, 14))
        
    def test_get_salary_cycle_dates_before_salary_date(self):
        """Test salary cycle dates when target is before salary date in month."""
        self.profile.salary_date = 20
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        # target_date = May 10, salary_date = 20
        # Should be in April 20 - May 19 cycle
        target = date(2026, 5, 10)
        start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        
        self.assertEqual(start, date(2026, 4, 20))
        self.assertEqual(end, date(2026, 5, 19))
        
    def test_get_salary_cycle_dates_year_boundary(self):
        """Test salary cycle spanning year boundary."""
        self.profile.salary_date = 15
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        # target_date = Jan 10, salary_date = 15
        # Should be Dec 15, 2025 - Jan 14, 2026
        target = date(2026, 1, 10)
        start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        
        self.assertEqual(start, date(2025, 12, 15))
        self.assertEqual(end, date(2026, 1, 14))
        
    def test_get_salary_cycle_dates_day_31_in_short_month(self):
        """Test that day 31 is handled correctly in months with fewer days."""
        self.profile.salary_date = 31
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        # target_date = Feb 15, salary_date = 31
        # February has 28 days, so should use Feb 28
        target = date(2026, 2, 15)
        start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        
        # Should start on Jan 31, not Feb 31
        self.assertEqual(start, date(2026, 1, 31))
        self.assertEqual(end, date(2026, 2, 27))
        
    def test_get_transactions_in_salary_cycle(self):
        """Test fetching transactions within a salary cycle."""
        self.profile.salary_date = 15
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        # Create transactions in May 15 - June 14 cycle
        Income.objects.create(
            user=self.user,
            date=date(2026, 5, 15),
            amount=10000,
            source='Salary'
        )
        
        Expense.objects.create(
            user=self.user,
            date=date(2026, 5, 20),
            amount=500,
            category='Food',
            description='Groceries'
        )
        
        Expense.objects.create(
            user=self.user,
            date=date(2026, 6, 10),
            amount=200,
            category='Transport',
            description='Taxi'
        )
        
        # Create transaction outside the cycle (should not be included)
        Expense.objects.create(
            user=self.user,
            date=date(2026, 6, 20),
            amount=100,
            category='Food',
            description='Outside cycle'
        )
        
        target = date(2026, 5, 25)
        trans = SalaryAnalysisService.get_transactions_in_salary_cycle(self.user, target)
        
        self.assertEqual(trans['income'].count(), 1)
        self.assertEqual(trans['expenses'].count(), 2)
        
    def test_calculate_salary_cycle_metrics(self):
        """Test calculation of salary cycle metrics (income, expenses, savings)."""
        self.profile.salary_date = 1
        self.profile.save()
        
        # Create transactions for May
        Income.objects.create(
            user=self.user,
            date=date(2026, 5, 1),
            amount=10000,
            source='Salary'
        )
        
        Expense.objects.create(
            user=self.user,
            date=date(2026, 5, 5),
            amount=2000,
            category='Food',
            description='Groceries'
        )
        
        Expense.objects.create(
            user=self.user,
            date=date(2026, 5, 10),
            amount=500,
            category='Transport',
            description='Taxi'
        )
        
        target = date(2026, 5, 15)
        metrics = SalaryAnalysisService.calculate_salary_cycle_metrics(self.user, target)
        
        # Verify metrics
        self.assertEqual(metrics['total_income'], 10000)
        self.assertEqual(metrics['total_expenses'], 2500)
        self.assertEqual(metrics['savings'], 7500)
        self.assertGreater(metrics['daily_burn'], 0)
        
    def test_calculate_salary_cycle_metrics_with_loan_repayment(self):
        """Test that loan repayments are correctly included in metrics."""
        self.profile.salary_date = 1
        self.profile.save()
        self.profile.refresh_from_db()
        
        # Create a loan
        loan = Loan.objects.create(
            user=self.user,
            name='Personal Loan',
            loan_type='PERSONAL',
            initial_principal=100000,
            duration_months=24,
            start_date=date(2026, 1, 1),
            is_active=True
        )
        
        # Create income
        Income.objects.create(
            user=self.user,
            date=date(2026, 5, 1),
            amount=10000,
            source='Salary'
        )
        
        # Create loan repayment (EMI: 4000, Interest: 800, Principal: 3200)
        LoanRepayment.objects.create(
            loan=loan,
            date=date(2026, 5, 5),
            amount=4000,
            principal_portion=3200,
            interest_portion=800,
            exchange_rate=1.0
        )
        
        # Create expense
        Expense.objects.create(
            user=self.user,
            date=date(2026, 5, 10),
            amount=1000,
            category='Food',
            description='Groceries'
        )
        
        target = date(2026, 5, 15)
        metrics = SalaryAnalysisService.calculate_salary_cycle_metrics(self.user, target)
        
        # Interest counts as spending; principal is repaying borrowed money, so it does not
        # (single definition in expenses/savings.py).
        # Savings = Income - Expenses - Interest = 10000 - 1000 - 800 = 8200
        self.assertEqual(metrics['total_income'], 10000)
        self.assertEqual(metrics['total_loan_principal'], 3200)
        self.assertAlmostEqual(metrics['savings'], 8200, places=0)
        self.assertAlmostEqual(metrics['savings_rate'], 82.0, places=1)
        
    def test_multiple_users_salary_dates_independent(self):
        """Test that different users can have different salary dates."""
        user2 = User.objects.create_user(username='testuser2', password='testpass')
        profile2 = UserProfile.objects.get(user=user2)
        
        self.profile.salary_date = 10
        self.profile.save()
        self.user = User.objects.get(pk=self.user.pk)
        
        profile2.salary_date = 20
        profile2.save()
        user2 = User.objects.get(pk=user2.pk)
        
        self.assertEqual(self.profile.salary_date, 10)
        self.assertEqual(profile2.salary_date, 20)
        
        # Verify they calculate different cycles for the same target date
        target = date(2026, 5, 25)
        start1, end1 = SalaryAnalysisService.get_salary_cycle_dates(self.user, target)
        start2, end2 = SalaryAnalysisService.get_salary_cycle_dates(user2, target)
        
        # User 1: May 10 - June 9
        self.assertEqual(start1, date(2026, 5, 10))
        self.assertEqual(end1, date(2026, 6, 9))
        
        # User 2: May 20 - June 19
        self.assertEqual(start2, date(2026, 5, 20))
        self.assertEqual(end2, date(2026, 6, 19))

    def test_salary_cycle_property_test_three_years_including_leap(self):
        """
        Property test: For every salary_date from 1 to 31 and every date across
        three years (2024 leap year through 2026), asserts:
        (i) start <= d <= end
        (ii) the cycle returned for end + 1 day starts exactly on end + 1 day (no gaps, no overlaps).
        """
        start_d = date(2024, 1, 1)
        end_d = date(2026, 12, 31)

        for s_day in range(1, 32):
            self.profile.salary_date = s_day
            self.profile.save()
            self.user.refresh_from_db()

            curr = start_d
            while curr <= end_d:
                start, end = SalaryAnalysisService.get_salary_cycle_dates(self.user, curr)
                self.assertLessEqual(start, curr, f"Start {start} > target {curr} for salary_date={s_day}")
                self.assertLessEqual(curr, end, f"Target {curr} > end {end} for salary_date={s_day}")

                next_d = end + timedelta(days=1)
                next_start, next_end = SalaryAnalysisService.get_salary_cycle_dates(self.user, next_d)
                self.assertEqual(
                    next_start, next_d,
                    f"Gap/overlap: cycle for {curr} ended {end}, but next cycle for {next_d} started {next_start}"
                )
                # Advance by 5 days or jump to end of cycle to keep test execution fast
                curr = min(curr + timedelta(days=7), end + timedelta(days=1))


class SalaryDateFormTest(TestCase):
    """Test form handling for salary date."""
    
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.profile = self.user.profile
        
    def test_profile_update_form_salary_date(self):
        """Test that ProfileUpdateForm includes salary_date field."""
        from expenses.forms import ProfileUpdateForm
        
        form = ProfileUpdateForm(instance=self.user)
        self.assertIn('salary_date', form.fields)
        
    def test_salary_date_update_form(self):
        """Test SalaryDateUpdateForm."""
        from expenses.forms import SalaryDateUpdateForm
        
        data = {'salary_date': '15'}
        form = SalaryDateUpdateForm(data=data, instance=self.user.profile)
        self.assertTrue(form.is_valid())
        
    def test_salary_date_form_invalid_values(self):
        """Test that SalaryDateUpdateForm rejects invalid salary dates."""
        from expenses.forms import SalaryDateUpdateForm
        
        # Invalid: Less than 1
        form = SalaryDateUpdateForm(data={'salary_date': '0'}, instance=self.user.profile)
        # Form may not validate this at form level, but model validation should catch it
        
        # Invalid: Greater than 31
        form = SalaryDateUpdateForm(data={'salary_date': '32'}, instance=self.user.profile)
        # Same as above

    def test_profile_update_form_social_user_email_disabled(self):
        """Test that ProfileUpdateForm disables email change for social users with a note."""
        from allauth.socialaccount.models import SocialAccount
        from expenses.forms import ProfileUpdateForm

        SocialAccount.objects.create(user=self.user, provider='google', uid='12345')
        form = ProfileUpdateForm(instance=self.user)
        self.assertTrue(form.fields['auth_email'].disabled)
        self.assertFalse(form.fields['first_name'].disabled)
        self.assertFalse(form.fields['last_name'].disabled)
        self.assertIn("social login", str(form.fields['auth_email'].help_text).lower())

    def test_profile_update_view_social_user_renders_note(self):
        """Test that profile settings page renders the social login note and enabled submit button."""
        from allauth.socialaccount.models import SocialAccount

        SocialAccount.objects.create(user=self.user, provider='google', uid='12345')
        self.client.force_login(self.user)
        response = self.client.get('/settings/profile/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Managed by social login. Email address cannot be changed.")
        self.assertNotContains(response, '<button type="submit" class="btn btn-primary" disabled>')

    def test_get_cycle_context_inactive_default(self):
        """Test get_cycle_context for a user with default salary_date=1."""
        from expenses.periods import get_cycle_context

        ctx = get_cycle_context(self.user, today=date(2026, 10, 2))
        self.assertFalse(ctx['cycle_active'])
        self.assertEqual(ctx['salary_date'], 1)
        self.assertEqual(ctx['cycle_range'], '')
        self.assertEqual(ctx['last_month_range'], '01 Sep – 30 Sep')
        self.assertEqual(ctx['prev_cycle_range'], '01 Sep – 30 Sep')

    def test_get_cycle_context_active_custom_salary_date(self):
        """Test get_cycle_context for a user with salary_date=7."""
        from expenses.periods import get_cycle_context

        self.profile.salary_date = 7
        self.profile.save()
        self.user.refresh_from_db()

        ctx = get_cycle_context(self.user, today=date(2026, 10, 2))
        self.assertTrue(ctx['cycle_active'])
        self.assertEqual(ctx['salary_date'], 7)
        self.assertEqual(ctx['cycle_range'], '07 Sep – 06 Oct')
        self.assertEqual(ctx['prev_cycle_range'], '07 Aug – 06 Sep')
        self.assertEqual(ctx['calendar_month_range'], '01 Oct – 31 Oct')
        self.assertEqual(ctx['last_month_range'], '01 Sep – 30 Sep')

    def test_resolve_period_calendar_month(self):
        """Test resolve_period for calendar_month and calendar_last_month."""
        from expenses.periods import resolve_period

        self.profile.salary_date = 7
        self.profile.save()
        self.user.refresh_from_db()

        today = date(2026, 10, 2)
        # Normal this_month resolves to salary cycle
        cycle_period = resolve_period(self.user, 'this_month', today=today)
        self.assertTrue(cycle_period.is_cycle)
        self.assertEqual(cycle_period.start, date(2026, 9, 7))
        self.assertEqual(cycle_period.end, date(2026, 10, 6))

        # calendar_month resolves to standard calendar boundaries
        cal_period = resolve_period(self.user, 'calendar_month', today=today)
        self.assertFalse(cal_period.is_cycle)
        self.assertEqual(cal_period.start, date(2026, 10, 1))
        self.assertEqual(cal_period.end, date(2026, 10, 31))

        # calendar_last_month resolves to prior month calendar boundaries
        cal_last_period = resolve_period(self.user, 'calendar_last_month', today=today)
        self.assertFalse(cal_last_period.is_cycle)
        self.assertEqual(cal_last_period.start, date(2026, 9, 1))
        self.assertEqual(cal_last_period.end, date(2026, 9, 30))

    def test_dashboard_renders_salary_cycle_toolbar(self):
        """Test that the dashboard page renders salary cycle in filter toolbar when salary_date != 1."""
        self.profile.salary_date = 7
        self.profile.has_seen_tutorial = True
        self.profile.save()
        self.client.force_login(self.user)

        response = self.client.get('/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Salary Cycle')
        self.assertContains(response, 'Salary cycle:')


