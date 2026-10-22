"""Shared set-up for the Insights Engine tests."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import translation

from expenses.insights import registry
from expenses.models import Account, Category, Expense, Income, UserProfile

D = Decimal
TODAY = datetime.date(2026, 10, 22)
ENGINE_ON = override_settings(INSIGHTS_ENGINE_MODE='on')


def month_start(today, back):
    index = today.year * 12 + today.month - 1 - back
    return datetime.date(index // 12, index % 12 + 1, 1)


class InsightBase(TestCase):
    """A Pro user with a cash account, and helpers to record money on explicit dates."""
    tier = 'PRO'
    salary_day = 1

    def setUp(self):
        cache.clear()
        # a request from a Hindi or Marathi profile leaves that language active on the thread; never leak it
        self.addCleanup(translation.deactivate)
        self.user = User.objects.create_user(username='insight-user', password='pw', email='i@example.com')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = self.tier
        profile.currency = '₹'
        profile.salary_date = self.salary_day
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        self.profile = self.user.profile
        Account.objects.filter(user=self.user).delete()
        Category.objects.filter(user=self.user).delete()
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('1000000'), currency='₹')
        self.client.force_login(self.user)

    def income(self, amount, when, source_type='Salary'):
        return Income.objects.create(user=self.user, date=when, amount=D(str(amount)), source=source_type,
                                     source_type=source_type, currency='₹', account=self.cash)

    def spend(self, amount, when, category='Dining'):
        return Expense.objects.create(user=self.user, date=when, amount=D(str(amount)), category=category,
                                      currency='₹', account=self.cash, payment_method='Cash', description=category)

    def category(self, name, limit=None):
        return Category.objects.create(user=self.user, name=name, limit=None if limit is None else D(str(limit)))

    def life(self, today=TODAY, months=5):
        """The scenario of insights.sampling, in the database: steady months, then a higher October."""
        for back in range(1, months + 1):
            first = month_start(today, back)
            self.income(60000, first)
            self.spend(20000, first, 'Rent')
            self.spend(9000, first, 'Dining')
            self.spend(12000, first, 'Groceries')
            if back == 1:
                self.spend(9000, first, 'Travel')
        first = month_start(today, 0)
        self.income(70000, first)
        self.spend(20000, first, 'Rent')
        self.spend(12000, first, 'Groceries')
        self.spend(22000, first, 'Dining')


def temp_rule(test, **kwargs):
    """Register a throwaway rule for one test."""
    registry.load()

    def clean():
        registry.REGISTRY.pop(kwargs['id'], None)

    test.addCleanup(clean)
    return registry.rule(**kwargs)
