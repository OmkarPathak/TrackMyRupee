
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from expenses.forms import ExpenseForm
from expenses.models import (
    Category,
    Expense,
    GoalContribution,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from finance_tracker.plans import PLAN_DETAILS


class StrictLimitEnforcementTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        # Ensure profile exists
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        
        # Create categories (6)
        for i in range(6):
            Category.objects.create(user=self.user, name=f'Category {i}')
            
        # Create savings goals (2)
        for i in range(2):
            SavingsGoal.objects.create(user=self.user, name=f'Goal {i}', target_amount=100)
            
        # Create recurring transactions (1)
        RecurringTransaction.objects.create(
            user=self.user, 
            description='Test Recurring', 
            amount=10, 
            category='Category 0',
            frequency='MONTHLY',
            start_date=date.today() - timedelta(days=32)
        )

    def test_category_limit_enforcement(self):
        # By default, profile is FREE
        self.user.refresh_from_db()
        # We created 6 categories in setUp.
        self.assertGreaterEqual(Category.objects.filter(user=self.user).count(), 6)
        
        form = ExpenseForm(user=self.user)
        choices = list(form.fields['category'].widget.choices)
        # FREE limit
        limit_free = PLAN_DETAILS['FREE']['limits']['budget_categories']
        self.assertEqual(len(choices), limit_free)
        
        # Upgrade to PLUS
        p = self.user.profile
        p.tier = 'PLUS'
        p.subscription_end_date = timezone.now() + timedelta(days=30)
        p.save()
        # Add a 10th+ category to test the limit
        for i in range(5):
            Category.objects.create(user=self.user, name=f'Category {10+i}')
        self.user.refresh_from_db()
        
        form = ExpenseForm(user=self.user)
        choices = list(form.fields['category'].widget.choices)
        # PLUS limit
        limit_plus = PLAN_DETAILS['PLUS']['limits']['budget_categories']
        self.assertEqual(len(choices), limit_plus)

    def test_recurring_transaction_limit_enforcement(self):
        # FREE limit is 0
        from expenses.views import process_user_recurring_transactions
        self.user.refresh_from_db()
        
        # Ensure rt is an EXPENSE and set start_date to yesterday
        rt = RecurringTransaction.objects.get(user=self.user)
        rt.transaction_type = 'EXPENSE'
        rt.start_date = date.today() - timedelta(days=1)
        rt.save()
        
        process_user_recurring_transactions(self.user)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 0)
        
        # Upgrade to PLUS (limit 3)
        p = self.user.profile
        p.tier = 'PLUS'
        p.subscription_end_date = timezone.now() + timedelta(days=30)
        p.save()
        self.user.refresh_from_db()
        
        process_user_recurring_transactions(self.user)
        # Plus limit for process_user_recurring_transactions
        limit_plus = PLAN_DETAILS['PLUS']['limits']['recurring_transactions']
        self.assertEqual(Expense.objects.filter(user=self.user).count(), min(1, limit_plus))

    def test_downgrade_notification_logic(self):
        # ... (existing test)
        p = self.user.profile
        p.tier = 'PRO'
        p.subscription_end_date = timezone.now() - timedelta(days=1)
        p.save()
        self.user.refresh_from_db()
        
        self.assertTrue(self.user.profile.subscription_expired)
        self.assertEqual(self.user.profile.last_tier_display, 'Pro')
        self.assertEqual(self.user.profile.active_tier, 'FREE')

    def test_recurring_transaction_update_limit_enforcement(self):
        from django.urls import reverse

        from expenses.models import RecurringTransaction
        
        # Clean up existing subs from setUp
        RecurringTransaction.objects.filter(user=self.user).delete()
        
        # Create 2 active subs
        rt1 = RecurringTransaction.objects.create(user=self.user, amount=10, transaction_type='EXPENSE', frequency='MONTHLY', start_date=timezone.now(), is_active=True)
        rt2 = RecurringTransaction.objects.create(user=self.user, amount=20, transaction_type='EXPENSE', frequency='MONTHLY', start_date=timezone.now(), is_active=True)
        
        # Downgrade to FREE (limit 0)
        p = self.user.profile
        p.tier = 'FREE'
        p.save()
        
        self.client.force_login(self.user)
        # Try to update first one
        response = self.client.get(reverse('recurring-edit', kwargs={'pk': rt1.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('recurring-list'), response.url)
        
    def test_savings_goal_update_limit_enforcement(self):
        from django.urls import reverse

        from expenses.models import SavingsGoal
        
        # Clean up existing goals from setUp
        SavingsGoal.objects.filter(user=self.user).delete()
        
        # Create 2 goals
        g1 = SavingsGoal.objects.create(user=self.user, name="Goal 1", target_amount=100)
        g2 = SavingsGoal.objects.create(user=self.user, name="Goal 2", target_amount=200)
        
        # Downgrade to FREE (limit 1)
        p = self.user.profile
        p.tier = 'FREE'
        p.save()
        
        limit_free = PLAN_DETAILS['FREE']['limits']['savings_goals']
        self.client.force_login(self.user)
        # Goal index >= limit should be locked
        response = self.client.get(reverse('goal-edit', kwargs={'pk': g2.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('goal-list'), response.url)
        
        # Goal 1 should NOT be locked
        response = self.client.get(reverse('goal-edit', kwargs={'pk': g1.pk}))
        self.assertEqual(response.status_code, 200)

    def test_category_update_limit_enforcement(self):
        from django.urls import reverse

        from expenses.models import Category
        
        # Clean up existing categories from setUp and signal
        Category.objects.filter(user=self.user).delete()
        
        # Create 6 categories
        cats = [Category.objects.create(user=self.user, name=f"Cat {i}") for i in range(1, 7)]
        # Order by ID is used, so cat 6 is index 5
        cat6 = cats[5]
        
        # Downgrade to FREE (limit 3)
        p = self.user.profile
        p.tier = 'FREE'
        p.save()
        
        limit_free = PLAN_DETAILS['FREE']['limits']['budget_categories']
        self.client.force_login(self.user)
        # Cat index >= limit should be locked
        response = self.client.get(reverse('category-edit', kwargs={'pk': cat6.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('category-list'), response.url)

    def test_savings_goal_locked_status(self):
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.test import RequestFactory

        from expenses.views import SavingsGoalDetailView, SavingsGoalListView
        
        factory = RequestFactory()
        request = factory.get('/goals/')
        request.user = self.user
        
        view = SavingsGoalListView()
        view.request = request
        context = view.get_context_data(object_list=SavingsGoal.objects.filter(user=self.user))
        
        limit_free = PLAN_DETAILS['FREE']['limits']['savings_goals']
        goals = context['goals']
        for i, goal in enumerate(goals):
            if i < limit_free:
                self.assertFalse(goal.is_locked)
            else:
                self.assertTrue(goal.is_locked)
        
        # Verify POST to locked goal fails
        locked_goal = goals[1]
        request_post = factory.post(f'/goals/{locked_goal.pk}/', {'amount': 10, 'date': date.today()})
        request_post.user = self.user
        
        # Add Session and Messages
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request_post)
        request_post.session.save()
        
        from django.contrib.messages.storage.fallback import FallbackStorage
        request_post._messages = FallbackStorage(request_post)
        
        view_detail = SavingsGoalDetailView()
        response = view_detail.post(request_post, pk=locked_goal.pk)
        
        self.assertEqual(response.status_code, 302) # Redirect due to lock
        self.assertEqual(GoalContribution.objects.filter(goal=locked_goal).count(), 0)
