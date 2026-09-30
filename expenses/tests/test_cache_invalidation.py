from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase

from expenses.models import Account, Category, GoalContribution, Loan, SavingsGoal, UserProfile


class CacheInvalidationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cache_tester', password='password123')
        profile = self.user.profile
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        self.account = Account.objects.create(user=self.user, name='Main Account', balance=Decimal('5000.00'))

    def _warm_cache(self):
        cache.set(f'home_default_data_{self.user.id}', {'cached': True}, 300)
        cache.set(f'budget_dashboard_{self.user.id}', {'cached': True}, 300)
        self.assertIsNotNone(cache.get(f'home_default_data_{self.user.id}'))
        self.assertIsNotNone(cache.get(f'budget_dashboard_{self.user.id}'))

    def _assert_cache_invalidated(self):
        self.assertIsNone(cache.get(f'home_default_data_{self.user.id}'))
        self.assertIsNone(cache.get(f'budget_dashboard_{self.user.id}'))

    def test_category_invalidation(self):
        # 1. Create
        self._warm_cache()
        cat = Category.objects.create(user=self.user, name='Travel', limit=Decimal('1000.00'))
        self._assert_cache_invalidated()

        # 2. Update
        self._warm_cache()
        cat.limit = Decimal('1500.00')
        cat.save()
        self._assert_cache_invalidated()

        # 3. Delete
        self._warm_cache()
        cat.delete()
        self._assert_cache_invalidated()

    def test_savings_goal_invalidation(self):
        # 1. Create
        self._warm_cache()
        goal = SavingsGoal.objects.create(
            user=self.user,
            name='Emergency Fund',
            target_amount=Decimal('50000.00'),
            current_amount=Decimal('10000.00')
        )
        self._assert_cache_invalidated()

        # 2. Update
        self._warm_cache()
        goal.current_amount = Decimal('12000.00')
        goal.save()
        self._assert_cache_invalidated()

        # 3. Delete
        self._warm_cache()
        goal.delete()
        self._assert_cache_invalidated()

    def test_goal_contribution_invalidation(self):
        goal = SavingsGoal.objects.create(
            user=self.user,
            name='Vacation',
            target_amount=Decimal('20000.00'),
            current_amount=Decimal('0.00')
        )

        # 1. Create contribution
        self._warm_cache()
        contrib = GoalContribution.objects.create(
            goal=goal,
            account=self.account,
            amount=Decimal('1000.00'),
            date=date.today()
        )
        self._assert_cache_invalidated()

        # 2. Update contribution
        self._warm_cache()
        contrib.amount = Decimal('1500.00')
        contrib.save()
        self._assert_cache_invalidated()

        # 3. Delete contribution
        self._warm_cache()
        contrib.delete()
        self._assert_cache_invalidated()

    def test_loan_invalidation(self):
        # 1. Create
        self._warm_cache()
        loan = Loan.objects.create(
            user=self.user,
            name='Car Loan',
            loan_type='CAR',
            initial_principal=Decimal('100000.00'),
            duration_months=36,
            start_date=date.today()
        )
        self._assert_cache_invalidated()

        # 2. Update
        self._warm_cache()
        loan.name = 'Updated Car Loan'
        loan.save()
        self._assert_cache_invalidated()

        # 3. Delete
        self._warm_cache()
        loan.delete()
        self._assert_cache_invalidated()

    def test_user_profile_invalidation(self):
        # 1. Update salary_date
        self._warm_cache()
        profile = self.user.profile
        profile.salary_date = 15
        profile.save()
        self._assert_cache_invalidated()

        # 2. Update currency
        self._warm_cache()
        profile.currency = '$'
        profile.save()
        self._assert_cache_invalidated()
