
from django.test import TestCase
from django.contrib.auth.models import User
from expenses.models import UserProfile, Category, SavingsGoal, RecurringTransaction, Expense, GoalContribution
from expenses.forms import ExpenseForm
from django.utils import timezone
from datetime import timedelta, date

class StrictLimitEnforcementTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.profile = self.user.profile
        
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
        # Signals create 6 default categories. We created 6 more. Total 12.
        self.assertEqual(Category.objects.filter(user=self.user).count(), 12)
        
        form = ExpenseForm(user=self.user)
        choices = list(form.fields['category'].widget.choices)
        # FREE limit is 5
        self.assertEqual(len(choices), 5)
        
        # Upgrade to PLUS
        p = self.user.profile
        p.tier = 'PLUS'
        p.subscription_end_date = timezone.now() + timedelta(days=30)
        p.save()
        self.user.refresh_from_db()
        
        form = ExpenseForm(user=self.user)
        choices = list(form.fields['category'].widget.choices)
        # PLUS limit is 10
        self.assertEqual(len(choices), 10)

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
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)

    def test_downgrade_notification_logic(self):
        # Setup expired subscription
        p = self.user.profile
        p.tier = 'PRO'
        p.subscription_end_date = timezone.now() - timedelta(days=1)
        p.save()
        self.user.refresh_from_db()
        
        self.assertTrue(self.user.profile.subscription_expired)
        self.assertEqual(self.user.profile.last_tier_display, 'Pro')
        self.assertEqual(self.user.profile.active_tier, 'FREE')

    def test_savings_goal_locked_status(self):
        from django.test import RequestFactory
        from expenses.views import SavingsGoalListView, SavingsGoalDetailView
        from django.contrib.sessions.middleware import SessionMiddleware
        
        factory = RequestFactory()
        request = factory.get('/goals/')
        request.user = self.user
        
        # FREE limit is 1
        view = SavingsGoalListView()
        view.request = request
        context = view.get_context_data(object_list=SavingsGoal.objects.filter(user=self.user))
        
        goals = context['goals']
        self.assertFalse(goals[0].is_locked) # First goal created
        self.assertTrue(goals[1].is_locked)  # Second goal created
        
        # Verify POST to locked goal fails
        locked_goal = goals[1]
        request_post = factory.post(f'/goals/{locked_goal.pk}/', {'amount': 10, 'date': date.today()})
        request_post.user = self.user
        
        # Add Session and Messages
        middleware = SessionMiddleware(lambda r: None)
        middleware.process_request(request_post)
        request_post.session.save()
        
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request_post, '_messages', FallbackStorage(request_post))
        
        view_detail = SavingsGoalDetailView()
        response = view_detail.post(request_post, pk=locked_goal.pk)
        
        self.assertEqual(response.status_code, 302) # Redirect due to lock
        self.assertEqual(GoalContribution.objects.filter(goal=locked_goal).count(), 0)
