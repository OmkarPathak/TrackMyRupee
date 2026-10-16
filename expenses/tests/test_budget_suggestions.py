"""The Budget page's smart suggestion: shown only when history supports it; dismissal is per user."""

import json
from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from expenses.budget_page import build_budget_page
from expenses.budget_suggestions import find_suggestion, suggestion_key
from expenses.models import (
    BudgetLimitVersion,
    BudgetSuggestionDismissal,
    Category,
    Expense,
)
from expenses.tests.budget_helpers import backdate_limits
from expenses.tests.test_budget_api import Base

D = Decimal
TODAY = timezone.localdate()
CUR = TODAY.replace(day=1)


def months_ago(n):
    idx = CUR.year * 12 + CUR.month - 1 - n
    return date(idx // 12, idx % 12 + 1, 1)


class SuggestionTests(Base):
    def setUp(self):
        cache.clear()
        super().setUp()
        Expense.objects.filter(user=self.user).delete()
        BudgetLimitVersion.objects.filter(user=self.user, category_name='fuel').update(amount=D('10000'))
        backdate_limits(self.user, months_ago(5))
        self.spend('Food', 5000)                     # Food is at its limit this month
        for n in (1, 2, 3):
            self.spend('Fuel', 300, when=months_ago(n).replace(day=5))     # Fuel: 3% of its limit

    def suggestion(self):
        page = build_budget_page(self.user, CUR, TODAY)
        return find_suggestion(self.user, CUR, page['categories'])

    def test_suggests_moving_unused_limit_to_the_category_at_its_limit(self):
        s = self.suggestion()
        self.assertEqual((s['donor_name'], s['needy_name'], s['months']), ('Fuel', 'Food', 3))
        self.assertEqual(s['amount'], 500)                 # 10% of Food's 5000, rounded to 100
        self.assertEqual(s['donor_untouched'], 10000.0)
        self.assertEqual(s['key'], suggestion_key(self.fuel.id, self.food.id, CUR))

    def test_hidden_when_the_donor_was_used_in_any_of_the_three_months(self):
        self.spend('Fuel', 1500, when=months_ago(2).replace(day=9))        # 18% that month
        self.assertIsNone(self.suggestion())

    def test_hidden_when_the_donor_had_no_limit_in_one_of_the_months(self):
        BudgetLimitVersion.objects.filter(user=self.user, category_name='fuel').update(effective_from=months_ago(2))
        self.assertIsNone(self.suggestion())

    def test_hidden_when_nothing_needs_attention(self):
        Expense.objects.filter(user=self.user, category='Food').delete()
        self.assertIsNone(self.suggestion())

    def test_hidden_when_the_donor_is_busy_this_month(self):
        self.spend('Fuel', 2000)
        self.assertIsNone(self.suggestion())

    def test_amount_never_exceeds_the_donors_room(self):
        self.spend('Fuel', 800)                            # still under 10%; room is now 9200
        Category.objects.filter(pk=self.food.pk).update(name='Food')
        s = self.suggestion()
        self.assertLessEqual(s['amount'], 9200)

    def test_over_budget_category_asks_for_at_least_what_it_overspent(self):
        self.spend('Food', 1500)                           # Food now over by 1500
        self.assertEqual(self.suggestion()['amount'], 1500)

    def test_dismissal_is_remembered_per_user_and_pair(self):
        s = self.suggestion()
        resp = self.client.post(reverse('budgets-api-dismiss'), json.dumps({'key': s['key']}),
                                content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self.suggestion())
        other = self.make_user('someone-else')
        self.assertFalse(BudgetSuggestionDismissal.objects.filter(user=other).exists())

    def test_dismiss_validates_and_requires_login(self):
        for bad in ({}, {'key': 5}, {'key': 'drop table'}, {'key': 'move:1:2:2026-1'}):
            resp = self.client.post(reverse('budgets-api-dismiss'), json.dumps(bad), content_type='application/json')
            self.assertEqual(resp.status_code, 400, bad)
        self.client.logout()
        resp = self.client.post(reverse('budgets-api-dismiss'), '{}', content_type='application/json')
        self.assertEqual(resp.status_code, 401)

    def test_page_shows_the_banner_and_only_for_the_current_month(self):
        page = self.client.get(reverse('budget'))
        self.assertContains(page, 'data-bdx-suggestion')
        self.assertContains(page, 'Smart move:')
        past = self.client.get(reverse('budget'), {'month': months_ago(1).month, 'year': months_ago(1).year})
        self.assertNotContains(past, 'data-bdx-suggestion')

    def test_banner_gone_after_dismissal(self):
        self.client.post(reverse('budgets-api-dismiss'), json.dumps({'key': self.suggestion()['key']}),
                         content_type='application/json')
        self.assertNotContains(self.client.get(reverse('budget')), 'data-bdx-suggestion')

    def test_no_extra_queries_when_nothing_needs_attention(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        Expense.objects.filter(user=self.user, category='Food').delete()
        page = build_budget_page(self.user, CUR, TODAY)
        with CaptureQueriesContext(connection) as ctx:
            find_suggestion(self.user, CUR, page['categories'])
        self.assertEqual(len(ctx), 0)
