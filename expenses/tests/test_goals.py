from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.forms import SavingsGoalForm
from expenses.models import GoalContribution, SavingsGoal
from finance_tracker.plans import PLAN_DETAILS


class SavingsGoalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', password='testpassword')
        # Setup FREE tier profile
        self.user.profile.tier = 'FREE'
        self.user.profile.save()
        
        self.goal = SavingsGoal.objects.create(
            user=self.user,
            name='Test Goal',
            target_amount=Decimal('1000.00'),
        )

    def test_savings_goal_model_progress(self):
        self.assertEqual(self.goal.progress_percentage, 0)
        self.assertFalse(self.goal.is_completed)
        
        self.goal.current_amount = Decimal('500.00')
        self.goal.save()
        self.assertEqual(self.goal.progress_percentage, 50.0)
        self.assertFalse(self.goal.is_completed)

        self.goal.current_amount = Decimal('1000.00')
        self.goal.save()
        self.assertEqual(self.goal.progress_percentage, 100.0)
        self.assertTrue(self.goal.is_completed)
        
        self.goal.current_amount = Decimal('1500.00')
        self.goal.save()
        self.assertEqual(self.goal.progress_percentage, 100.0)
        self.assertTrue(self.goal.is_completed)

    def test_goal_contribution_updates_goal(self):
        self.assertEqual(self.goal.current_amount, Decimal('0.00'))
        
        contrib1 = GoalContribution.objects.create(goal=self.goal, amount=Decimal('200.00'))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.current_amount, Decimal('200.00'))
        
        contrib2 = GoalContribution.objects.create(goal=self.goal, amount=Decimal('300.00'))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.current_amount, Decimal('500.00'))
        
        contrib1.delete()
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.current_amount, Decimal('300.00'))

    def test_goal_contribution_update_without_account(self):
        contribution = GoalContribution.objects.create(
            goal=self.goal,
            amount=Decimal('200.00'),
            account=None,
        )

        contribution.amount = Decimal('350.00')
        contribution.save()

        contribution.refresh_from_db()
        self.goal.refresh_from_db()
        self.assertEqual(contribution.amount, Decimal('350.00'))
        self.assertIsNone(contribution.account)
        self.assertEqual(self.goal.current_amount, Decimal('350.00'))

    def test_savings_goal_form_validation(self):
        form = SavingsGoalForm(data={
            'name': 'Vacation',
            'target_amount': '500.00',
            'currency': '₹',
            'icon': '✈️',
            'color': 'primary'
        })
        self.assertTrue(form.is_valid())
        
        form_invalid = SavingsGoalForm(data={
            'name': 'Vacation',
            'target_amount': '-500.00',
            'currency': '₹',
            'icon': '✈️',
            'color': 'primary'
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn('target_amount', form_invalid.errors)

    def test_goal_list_view_free_tier(self):
        self.client.login(username='testuser', password='testpassword')
        limit = PLAN_DETAILS['FREE']['limits']['savings_goals']
        
        # Add goals up to the limit
        existing_count = SavingsGoal.objects.filter(user=self.user).count()
        if limit != -1 and existing_count < limit:
            for i in range(limit - existing_count):
                SavingsGoal.objects.create(user=self.user, name=f'Free Goal {i}', target_amount=Decimal('100.00'))
        
        response = self.client.get(reverse('goal-list'))
        self.assertEqual(response.status_code, 200)
        
        # Now it should be False if limit reached
        if limit != -1:
            self.assertFalse(response.context['can_create_goal'])
        else:
            self.assertTrue(response.context['can_create_goal'])

    def test_goal_list_view_pro_tier(self):
        pro_user = User.objects.create_user(username='prouser', password='testpassword')
        pro_user.profile.tier = 'PRO'
        pro_user.profile.is_lifetime = True
        pro_user.profile.save()
        
        self.client.login(username='prouser', password='testpassword')
        response = self.client.get(reverse('goal-list'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_create_goal'])

    def test_goal_create_limit_for_free_user(self):
        self.client.login(username='testuser', password='testpassword')
        limit = PLAN_DETAILS['FREE']['limits']['savings_goals']
        if limit == -1: return # Skip if unlimited
        
        # Fill up to limit
        current_count = SavingsGoal.objects.filter(user=self.user).count()
        for i in range(limit - current_count):
             SavingsGoal.objects.create(user=self.user, name=f'Fill {i}', target_amount=Decimal('100.00'))
             
        # Trying to load the create page should redirect
        response = self.client.get(reverse('goal-create'))
        self.assertEqual(response.status_code, 302)
        
        # POSTing should also fail
        response = self.client.post(reverse('goal-create'), data={
            'name': 'Exceeding Goal',
            'target_amount': '500.00',
            'currency': '₹',
            'icon': '🚗',
            'color': 'primary'
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SavingsGoal.objects.filter(user=self.user).count(), limit)
        
    def test_goal_list_view_plus_tier_limit(self):
        plus_user = User.objects.create_user(username='plususer', password='testpassword')
        plus_user.profile.tier = 'PLUS'
        plus_user.profile.is_lifetime = True
        plus_user.profile.save()
        
        self.client.login(username='plususer', password='testpassword')
        limit = PLAN_DETAILS['PLUS']['limits']['savings_goals']
        if limit == -1: return
        
        for i in range(limit):
             SavingsGoal.objects.create(user=plus_user, name=f'Plus Goal {i}', target_amount=Decimal('100.00'))
        
        response = self.client.get(reverse('goal-list'))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_create_goal'])
        
    def test_goal_create_limit_for_plus_user(self):
        plus_user = User.objects.create_user(username='plususer2', password='testpassword')
        plus_user.profile.tier = 'PLUS'
        plus_user.profile.is_lifetime = True
        plus_user.profile.save()
        
        self.client.login(username='plususer2', password='testpassword')
        limit = PLAN_DETAILS['PLUS']['limits']['savings_goals']
        if limit == -1: return
        
        for i in range(limit):
             SavingsGoal.objects.create(user=plus_user, name=f'Plus Goal {i}', target_amount=Decimal('100.00'))

        # Trying to load the create page should redirect
        response = self.client.get(reverse('goal-create'))
        self.assertEqual(response.status_code, 302)

    def test_goal_detail_add_funds(self):
        self.client.login(username='testuser', password='testpassword')
        
        response = self.client.post(reverse('goal-detail', kwargs={'pk': self.goal.pk}), data={
            'amount': '250.00',
            'date': '2023-10-01'
        })
        
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('goal-detail', kwargs={'pk': self.goal.pk}))
        
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.current_amount, Decimal('250.00'))
        self.assertEqual(self.goal.contributions.count(), 1)
        self.assertEqual(self.goal.contributions.first().amount, Decimal('250.00'))

    def test_goal_detail_clear_confetti_ajax(self):
        self.client.login(username='testuser', password='testpassword')
        session = self.client.session
        session['trigger_confetti'] = True
        session.save()
        
        response = self.client.post(reverse('goal-detail', kwargs={'pk': self.goal.pk}), 
                               data='{"clear_confetti": true}', 
                               content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('trigger_confetti', self.client.session)

    def test_savings_goal_estimated_completion_date(self):
        from django.utils import timezone
        from datetime import date, timedelta
        
        # When no contributions exist, it should return None
        self.assertIsNone(self.goal.estimated_completion_date)
        
        # Add a contribution on yesterday
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        
        # To avoid circular import/shadow postings dependencies in test, let's create a contribution
        # and test properties
        contrib = GoalContribution.objects.create(
            goal=self.goal,
            amount=Decimal('100.00'),
            date=yesterday
        )
        
        # Progress: 100/1000 = 10%. Elapsed days: 1. Avg daily: 100. Days left: 9.
        # Est completion should be today + 9 days = yesterday + 10 days
        self.goal.refresh_from_db()
        est_date = self.goal.estimated_completion_date
        self.assertIsNotNone(est_date)
        expected_date = today + timedelta(days=9)
        self.assertEqual(est_date, expected_date)

    def test_goal_detail_framing_and_needed_monthly(self):
        from django.utils import timezone
        from datetime import date, timedelta
        
        self.client.login(username='testuser', password='testpassword')
        
        # Set target date to 10 months from today (approx 304 days)
        today = timezone.localdate()
        target_date = today + timedelta(days=304)
        self.goal.target_date = target_date
        self.goal.save()
        
        # Add a contribution on yesterday to set a pace
        yesterday = today - timedelta(days=1)
        GoalContribution.objects.create(
            goal=self.goal,
            amount=Decimal('200.00'),
            date=yesterday
        )
        
        # Pace is: 200/day. Remaining target amount: 800.
        # Est days left: 4 days.
        # Est completion date: today + 4 days, which is well before target_date.
        # Therefore, user is early/on-track.
        
        response = self.client.get(reverse('goal-detail', kwargs={'pk': self.goal.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_on_track'])
        self.assertIsNotNone(response.context['needed_per_month'])
        self.assertIsNone(response.context['needed_gap']) # No gap since user is on track
        self.assertIsNotNone(response.context['completion_status'])
        self.assertTrue(response.context['completion_status']['is_early'])
        
        # Verify content rendering
        content = response.content.decode('utf-8')
        self.assertIn('Needed/month to hit target', content)
        self.assertIn('Target date', content)
        self.assertIn('At current pace', content)
