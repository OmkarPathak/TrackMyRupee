"""The dashboard keeps its worked-out page for hours; a page worked out on one day is not served on the next."""
import sys
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, Expense


def at(year, month, day, hour=12):
    return timezone.make_aware(datetime(year, month, day, hour, 0))


class DashboardCacheDayTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cacher', 'c@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.client.force_login(self.user)
        account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('1000'), currency='₹')
        Expense.objects.create(user=self.user, date=at(2026, 10, 30).date(), amount=Decimal('100'), description='x',
                               category='Food', account=account, base_amount=Decimal('100'))
        # (the cache is switched off while the tests run, as it would be skipped for a test client)
        patcher = patch.object(sys, 'argv', ['manage.py', 'runserver'])
        patcher.start()
        self.addCleanup(patcher.stop)

    def home(self, moment):
        with patch('django.utils.timezone.now', return_value=moment):
            return self.client.get(reverse('home'))

    def test_a_page_built_in_october_is_not_served_in_november(self):
        first = self.home(at(2026, 10, 31, 23))
        self.assertEqual(first.context['total_expenses'], Decimal('100.00'))          # (the 30 October expense)
        second = self.home(at(2026, 11, 1, 1))
        self.assertEqual(second.context['total_expenses'], Decimal('0.00'))           # (November has none yet)

    def test_the_same_day_is_still_served_from_the_cache(self):
        first = self.home(at(2026, 10, 15, 9))
        Expense.objects.filter(user=self.user).update(amount=Decimal('999'), base_amount=Decimal('999'))   # (no signal: the cache stays)
        again = self.home(at(2026, 10, 15, 18))
        self.assertEqual(again.context['total_expenses'], first.context['total_expenses'])
        self.assertEqual(again.context.get('built_on'), '2026-10-15')

    def test_a_page_is_rebuilt_when_the_day_changes_inside_a_month(self):
        self.home(at(2026, 10, 15, 9))
        later = self.home(at(2026, 10, 16, 9))
        self.assertEqual(later.context.get('built_on'), '2026-10-16')
