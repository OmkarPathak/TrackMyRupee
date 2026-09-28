from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.filters import GOAL_DETAIL_FILTERS
from expenses.forms import SavingsGoalForm
from expenses.models import Account, GoalContribution, SavingsGoal
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
        from datetime import timedelta

        from django.utils import timezone
        
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
        from datetime import timedelta

        from django.utils import timezone
        
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

    def test_goal_contribution_locking_and_current_amount_accuracy(self):
        """
        Tests sequential GoalContribution creation, updates, and deletions,
        confirming that current_amount reflects all adjustments accurately.

        NOTE ON CONCURRENCY & SQLITE TEST LIMITATION:
        Under SQLite (the test database backend configured in settings.py),
        `connection.features.has_select_for_update` is False, meaning SQLite does
        not support PostgreSQL-style SELECT ... FOR UPDATE row locks. In SQLite,
        any select_for_update() call is silently treated as a regular SELECT, and
        SQLite only provides database-wide locks rather than row-level concurrency.
        Under production PostgreSQL (where has_select_for_update is True), the
        select_for_update() call on SavingsGoal within transaction.atomic() acquires
        an exclusive row lock, preventing concurrent contributions from reading a
        stale current_amount and overwriting each other's increments.
        """
        # Verify that select_for_update is invoked on SavingsGoal.objects during save() and delete()
        with patch.object(SavingsGoal.objects, 'select_for_update', wraps=SavingsGoal.objects.select_for_update) as mock_sfu:
            c1 = GoalContribution.objects.create(goal=self.goal, amount=Decimal('150.00'))
            self.assertEqual(mock_sfu.call_count, 1)

            self.goal.refresh_from_db()
            self.assertEqual(self.goal.current_amount, Decimal('150.00'))

            c2 = GoalContribution.objects.create(goal=self.goal, amount=Decimal('250.00'))
            self.assertEqual(mock_sfu.call_count, 2)

            self.goal.refresh_from_db()
            self.assertEqual(self.goal.current_amount, Decimal('400.00'))

            # Update existing contribution: revert old 150, apply new 200 -> delta +50
            c1.amount = Decimal('200.00')
            c1.save()
            self.assertEqual(mock_sfu.call_count, 3)

            self.goal.refresh_from_db()
            self.assertEqual(self.goal.current_amount, Decimal('450.00'))

            # Delete contribution: revert 200 -> remaining 250
            c1.delete()
            self.assertEqual(mock_sfu.call_count, 4)

            self.goal.refresh_from_db()
            self.assertEqual(self.goal.current_amount, Decimal('250.00'))

            # Delete second contribution: revert 250 -> remaining 0
            c2.delete()
            self.assertEqual(mock_sfu.call_count, 5)

            self.goal.refresh_from_db()
            self.assertEqual(self.goal.current_amount, Decimal('0.00'))

    def test_is_goal_locked_method_and_call_sites_agreement(self):
        """
        Tests UserProfile.is_goal_locked() directly and confirms that all 4
        call sites in goals.py (ListView context, UpdateView dispatch,
        DetailView GET, and DetailView POST contribution) agree on lock status.
        """
        # Ensure user is on FREE tier (limit = 1 savings goal)
        self.user.profile.tier = 'FREE'
        self.user.profile.is_lifetime = False
        self.user.profile.save()

        # self.goal is the first goal (created in setUp) -> within limit (unlocked)
        # Create second and third goals -> beyond limit (locked)
        goal2 = SavingsGoal.objects.create(user=self.user, name='Second Goal', target_amount=Decimal('500.00'))
        goal3 = SavingsGoal.objects.create(user=self.user, name='Third Goal', target_amount=Decimal('800.00'))

        # 1. Direct model method verification
        profile = self.user.profile
        self.assertFalse(profile.is_goal_locked(self.goal))
        self.assertTrue(profile.is_goal_locked(goal2))
        self.assertTrue(profile.is_goal_locked(goal3))

        # 2. Model method with pre-fetched ordered_goals list
        all_ordered = list(SavingsGoal.objects.filter(user=self.user).order_by('created_at', 'id'))
        self.assertFalse(profile.is_goal_locked(self.goal, ordered_goals=all_ordered))
        self.assertTrue(profile.is_goal_locked(goal2, ordered_goals=all_ordered))
        self.assertTrue(profile.is_goal_locked(goal3, ordered_goals=all_ordered))

        # 3. Model method with PRO tier (unlimited)
        profile.tier = 'PRO'
        profile.is_lifetime = True
        profile.save()
        self.assertFalse(profile.is_goal_locked(self.goal))
        self.assertFalse(profile.is_goal_locked(goal2))
        self.assertFalse(profile.is_goal_locked(goal3))

        # Reset to FREE tier for view testing
        profile.tier = 'FREE'
        profile.is_lifetime = False
        profile.save()

        self.client.login(username='testuser', password='testpassword')

        # Site 1: SavingsGoalListView.get_context_data()
        list_response = self.client.get(reverse('goal-list'))
        self.assertEqual(list_response.status_code, 200)
        view_goals = {g.pk: g.is_locked for g in list_response.context['goals']}
        self.assertFalse(view_goals[self.goal.pk])
        self.assertTrue(view_goals[goal2.pk])
        self.assertTrue(view_goals[goal3.pk])

        # Site 2: SavingsGoalUpdateView.dispatch()
        # Unlocked goal can be accessed for update
        edit_allowed_resp = self.client.get(reverse('goal-edit', kwargs={'pk': self.goal.pk}))
        self.assertEqual(edit_allowed_resp.status_code, 200)

        # Locked goal redirects with error message
        edit_locked_resp = self.client.get(reverse('goal-edit', kwargs={'pk': goal2.pk}))
        self.assertEqual(edit_locked_resp.status_code, 302)
        self.assertRedirects(edit_locked_resp, reverse('goal-list'))

        # Site 3: SavingsGoalDetailView._is_locked() on GET
        detail_unlocked_resp = self.client.get(reverse('goal-detail', kwargs={'pk': self.goal.pk}))
        self.assertEqual(detail_unlocked_resp.status_code, 200)
        self.assertFalse(detail_unlocked_resp.context['is_locked'])

        detail_locked_resp = self.client.get(reverse('goal-detail', kwargs={'pk': goal2.pk}))
        self.assertEqual(detail_locked_resp.status_code, 200)
        self.assertTrue(detail_locked_resp.context['is_locked'])

        # Site 4: SavingsGoalDetailView.post() contribution lock check
        # Unlocked goal accepts contribution
        post_unlocked_resp = self.client.post(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            data={'amount': '50.00', 'date': '2026-01-01'}
        )
        self.assertEqual(post_unlocked_resp.status_code, 302)
        self.assertEqual(post_unlocked_resp.url, reverse('goal-detail', kwargs={'pk': self.goal.pk}))
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.current_amount, Decimal('50.00'))

        # Locked goal rejects contribution and redirects to goal-list
        post_locked_resp = self.client.post(
            reverse('goal-detail', kwargs={'pk': goal2.pk}),
            data={'amount': '50.00', 'date': '2026-01-01'}
        )
        self.assertEqual(post_locked_resp.status_code, 302)
        self.assertRedirects(post_locked_resp, reverse('goal-list'))
        goal2.refresh_from_db()
        self.assertEqual(goal2.current_amount, Decimal('0.00'))
        self.assertEqual(goal2.contributions.count(), 0)


class SavingsGoalDetailUnifiedFiltersTests(TestCase):
    def setUp(self):
        from django.utils import timezone
        self.client = Client()
        self.user = User.objects.create_user(username='filteruser', password='testpassword')
        self.user.profile.tier = 'PRO'
        self.user.profile.is_lifetime = True
        self.user.profile.save()
        self.client.login(username='filteruser', password='testpassword')

        self.account_hdfc = Account.objects.create(
            user=self.user,
            name='HDFC Bank',
            account_type='SAVINGS',
            balance=Decimal('50000.00'),
            currency='₹',
        )
        self.account_icici = Account.objects.create(
            user=self.user,
            name='ICICI Bank',
            account_type='SAVINGS',
            balance=Decimal('30000.00'),
            currency='₹',
        )

        self.goal = SavingsGoal.objects.create(
            user=self.user,
            name='Europe Trip',
            target_amount=Decimal('200000.00'),
            currency='₹',
        )

        today = timezone.localdate()
        self.c1 = GoalContribution.objects.create(
            goal=self.goal,
            account=self.account_hdfc,
            amount=Decimal('250.00'),
            date=today,
        )
        self.c2 = GoalContribution.objects.create(
            goal=self.goal,
            account=self.account_icici,
            amount=Decimal('1500.00'),
            date=today,
        )

    def test_goal_detail_context_and_table_rendering(self):
        response = self.client.get(reverse('goal-detail', kwargs={'pk': self.goal.pk}))
        self.assertEqual(response.status_code, 200)

        # Context contains unified filter keys
        self.assertIn('filter_config', response.context)
        self.assertEqual(response.context['filter_config'], GOAL_DETAIL_FILTERS)
        self.assertIn('applied_state', response.context)
        self.assertEqual(response.context['applied_state']['time_period'], 'all')
        self.assertEqual(response.context['applied_state']['sort'], 'date_desc')
        self.assertEqual(response.context['filtered_total'], Decimal('1750.00'))

        # Template contains ledger card, mobile grid view, and unified toolbar
        content = response.content.decode('utf-8')
        self.assertIn('transaction-ledger-card', content)
        self.assertIn('mobileGridView', content)
        self.assertIn('tmr-toolbar-goal_detail', content)
        self.assertIn('Contribution History', content)
        self.assertIn('HDFC Bank', content)
        self.assertIn('ICICI Bank', content)

    def test_goal_detail_filter_by_account(self):
        # Filter for HDFC only
        response = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'account': [str(self.account_hdfc.id)]}
        )
        self.assertEqual(response.status_code, 200)
        contribs = response.context['contributions']
        self.assertEqual(len(contribs), 1)
        self.assertEqual(contribs[0].id, self.c1.id)
        self.assertEqual(response.context['filtered_total'], Decimal('250.00'))

    def test_goal_detail_filter_by_amount_range(self):
        # Under ₹500 should return c1 (250) only
        resp_under_500 = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'amount_range': 'Under ₹500'}
        )
        self.assertEqual(resp_under_500.status_code, 200)
        self.assertEqual(len(resp_under_500.context['contributions']), 1)
        self.assertEqual(resp_under_500.context['contributions'][0].id, self.c1.id)
        self.assertEqual(resp_under_500.context['filtered_total'], Decimal('250.00'))

        # ₹500 to ₹2,000 should return c2 (1500) only
        resp_mid = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'amount_range': '₹500 to ₹2,000'}
        )
        self.assertEqual(resp_mid.status_code, 200)
        self.assertEqual(len(resp_mid.context['contributions']), 1)
        self.assertEqual(resp_mid.context['contributions'][0].id, self.c2.id)
        self.assertEqual(resp_mid.context['filtered_total'], Decimal('1500.00'))

    def test_goal_detail_search_by_account_name(self):
        # Search "ICICI"
        response = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'search': 'ICICI'}
        )
        self.assertEqual(response.status_code, 200)
        contribs = response.context['contributions']
        self.assertEqual(len(contribs), 1)
        self.assertEqual(contribs[0].id, self.c2.id)
        self.assertEqual(response.context['search_query'], 'ICICI')

    def test_goal_detail_sorting(self):
        # Sort amount descending (1500 then 250)
        resp_desc = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'sort': 'amount_desc'}
        )
        self.assertEqual(resp_desc.status_code, 200)
        amounts_desc = [c.amount for c in resp_desc.context['contributions']]
        self.assertEqual(amounts_desc, [Decimal('1500.00'), Decimal('250.00')])

        # Sort amount ascending (250 then 1500)
        resp_asc = self.client.get(
            reverse('goal-detail', kwargs={'pk': self.goal.pk}),
            {'sort': 'amount_asc'}
        )
        self.assertEqual(resp_asc.status_code, 200)
        amounts_asc = [c.amount for c in resp_asc.context['contributions']]
        self.assertEqual(amounts_asc, [Decimal('250.00'), Decimal('1500.00')])

    def test_goal_detail_filter_options_api(self):
        # API options for goal_detail account filter
        response = self.client.get(
            reverse('filter-options-api'),
            {'page': 'goal_detail', 'filter': 'account'}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['page'], 'goal_detail')
        self.assertEqual(data['filter'], 'account')
        option_labels = [opt['label'] for opt in data['options']]
        self.assertIn('HDFC Bank', option_labels)
        self.assertIn('ICICI Bank', option_labels)
