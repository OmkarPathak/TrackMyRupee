"""The "Where Did My Salary Go?" card and the whole "Financial Intelligence" accordion that held it (Money Flow
Analysis, Actionable Alerts, Share Your Financial Snapshot, and the sparkle button that opened it) are gone from the
dashboard: the same figures are in the sections around it, so the page no longer shows them twice."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Expense, Income


class SalaryCardRemovedTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('story-user', 'story@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        account = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('50000'), currency='₹')
        Income.objects.create(user=self.user, date=date.today(), amount=Decimal('60000'), source='Salary', source_type='Salary', currency='₹', account=account)
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('1500'), category='Food', currency='₹', account=account, payment_method='Cash')
        self.client.force_login(self.user)

    def test_the_card_its_story_and_its_chart_are_not_on_the_page(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        for gone in ('Where Did My Salary Go', 'Your Financial Story', 'tour-money-flow-card', 'moneyFlowChart',
                     'Your story is still being written', 'collapseInsights', 'insightsAccordion', 'Money Flow Analysis',
                     'Actionable Alerts', 'Share Your Financial Snapshot', 'salary-wrapped-card', 'downloadBreakdown',
                     'shareToWhatsApp', 'Financial Intelligence'):
            self.assertNotIn(gone, html, gone)
        self.assertNotIn('monthly_story', response.context)

    def test_what_stays_on_the_dashboard_still_works(self):
        html = self.client.get(reverse('home')).content.decode()
        for kept in ('categoryChart', 'savingsMethodModal'):          # the charts and the "how it works" button
            self.assertIn(kept, html, kept)
