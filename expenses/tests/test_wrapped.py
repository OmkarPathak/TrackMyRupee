"""Year in Review ("Wrapped"): the extra story, the personality rules, and what the page offers for sharing."""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, Expense, Income, UserProfile
from expenses.utils import generate_year_in_review_data
from expenses.wrapped import (
    _longest_gap,
    _longest_week_streak,
    _personality,
    build_wrapped,
)

D = Decimal
YEAR = timezone.localdate().year - 1          # a finished year: nothing stops at today


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('wrap-user', password='pw', first_name='Riya')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PLUS'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('100000'), currency='₹')

    def spend(self, amount, when, category='Food', description='lunch'):
        return Expense.objects.create(user=self.user, account=self.bank, amount=D(str(amount)), base_amount=D(str(amount)),
                                      date=when, category=category, description=description, currency='₹', exchange_rate=1)

    def story(self, year=YEAR):
        review = generate_year_in_review_data(self.user, year)
        return build_wrapped(self.user, year, review)


class HelperTests(TestCase):
    def test_longest_gap_counts_days_without_an_entry(self):
        start, end = datetime.date(2025, 1, 1), datetime.date(2025, 1, 31)
        days = {datetime.date(2025, 1, 1), datetime.date(2025, 1, 10), datetime.date(2025, 1, 31)}
        self.assertEqual(_longest_gap(days, start, end), (20, datetime.date(2025, 1, 11)))

    def test_longest_gap_is_zero_when_every_day_has_an_entry(self):
        days = {datetime.date(2025, 1, d) for d in range(1, 6)}
        self.assertEqual(_longest_gap(days, datetime.date(2025, 1, 1), datetime.date(2025, 1, 5))[0], 0)

    def test_week_streak_counts_consecutive_weeks_across_a_year_end(self):
        days = {datetime.date(2024, 12, 23), datetime.date(2024, 12, 31), datetime.date(2025, 1, 8), datetime.date(2025, 2, 20)}
        self.assertEqual(_longest_week_streak(days), 3)
        self.assertEqual(_longest_week_streak(set()), 0)

    def rules(self, **over):
        facts = dict(expense_count=100, months_with_spend=10, biggest_share=5, weekend_share=28, food_share=10,
                     travel_share=2, no_spend_share=5, per_day=1.0, per_day_text='1.0', steadiness=0.6)
        facts.update(over)
        return _personality(facts)['key']

    def test_each_rule_and_the_default(self):
        self.assertEqual(self.rules(), 'balanced')
        self.assertEqual(self.rules(biggest_share=25), 'big_ticket')
        self.assertEqual(self.rules(weekend_share=50), 'weekend')
        self.assertEqual(self.rules(food_share=30), 'foodie')
        self.assertEqual(self.rules(travel_share=20), 'explorer')
        self.assertEqual(self.rules(no_spend_share=30), 'minimalist')
        self.assertEqual(self.rules(per_day=4), 'everyday')
        self.assertEqual(self.rules(steadiness=0.1), 'steady')

    def test_a_thin_year_gets_the_getting_started_personality_whatever_else_is_true(self):
        self.assertEqual(self.rules(expense_count=5, weekend_share=90), 'getting_started')
        self.assertEqual(self.rules(months_with_spend=2, food_share=90), 'getting_started')

    def test_the_first_matching_rule_wins(self):
        self.assertEqual(self.rules(biggest_share=30, weekend_share=60), 'big_ticket')


class StoryTests(Base):
    def test_no_expenses_means_no_story(self):
        self.assertFalse(self.story()['has_story'])

    def test_top_categories_have_shares_and_bar_widths(self):
        for _ in range(3):
            self.spend(300, datetime.date(YEAR, 3, 3), 'Rent')
        self.spend(100, datetime.date(YEAR, 4, 4), 'Food')
        story = self.story()
        self.assertEqual([c['name'] for c in story['top']], ['Rent', 'Food'])
        self.assertEqual([c['pct'] for c in story['top']], [90, 10])
        self.assertEqual(story['top'][0]['width'], 100)
        self.assertLess(story['top'][1]['width'], 100)

    def test_favourite_stop_needs_three_visits_and_ignores_case(self):
        for d, text in enumerate(['Chai Point', 'chai point ', 'CHAI POINT', 'Elsewhere'], start=1):
            self.spend(50, datetime.date(YEAR, 2, d), description=text)
        stop = self.story()['facts']['favourite_stop']
        self.assertEqual(stop['count'], 3)
        self.assertEqual(stop['name'].lower().strip(), 'chai point')

    def test_no_stop_is_named_below_three_visits(self):
        self.spend(50, datetime.date(YEAR, 2, 1), description='Once')
        self.spend(50, datetime.date(YEAR, 2, 2), description='Once')
        self.assertIsNone(self.story()['facts']['favourite_stop'])

    def test_no_spend_streak_is_the_longest_quiet_run(self):
        self.spend(10, datetime.date(YEAR, 1, 1))
        self.spend(10, datetime.date(YEAR, 12, 31))
        streak = self.story()['facts']['no_spend_streak']
        self.assertEqual(streak['days'], 363)

    def test_glow_up_compares_categories_with_the_year_before_in_percent_only(self):
        self.spend(200, datetime.date(YEAR - 1, 5, 5), 'Dining')
        self.spend(100, datetime.date(YEAR, 5, 5), 'Dining')
        self.spend(100, datetime.date(YEAR, 6, 6), 'Rent')            # no earlier Rent: nothing to compare with
        glow = self.story()['glow']
        self.assertEqual(glow, [{'name': 'Dining', 'change': -50}])

    def test_no_glow_up_without_an_earlier_year(self):
        self.spend(100, datetime.date(YEAR, 5, 5))
        self.assertEqual(self.story()['glow'], [])

    def test_milestones_count_days_with_any_entry(self):
        self.spend(10, datetime.date(YEAR, 1, 1))
        Income.objects.create(user=self.user, account=self.bank, amount=D('100'), source_type='Salary', date=datetime.date(YEAR, 1, 2), currency='₹')
        self.assertEqual(self.story()['milestones']['days_logged'], 2)

    def test_money_story_carries_the_savings_rate_and_clamps_the_ring(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('1000'), source_type='Salary', date=datetime.date(YEAR, 1, 2), currency='₹')
        self.spend(2000, datetime.date(YEAR, 1, 3))
        money = self.story()['money']
        self.assertTrue(money['has_income'])
        self.assertLess(money['savings_rate'], 0)
        self.assertEqual(money['ring_pct'], 0)


class PageTests(Base):
    def setUp(self):
        super().setUp()
        for month in range(1, 7):
            self.spend(100 * month, datetime.date(YEAR, month, 5))
        for i in range(25):
            self.spend(10, datetime.date(YEAR, 7, 1 + i % 20))

    def test_the_page_offers_cards_and_the_share_sheet(self):
        html = self.client.get(reverse('year_in_review', args=[YEAR])).content.decode()
        self.assertEqual(html.count('class="wr-card'), 4)           # cover, categories, facts, milestones (no income)
        for needed in ('id="wrHide"', 'id="wrMoney"', 'id="wrDownload"', 'js/wrapped.js'):
            self.assertIn(needed, html)

    def test_income_based_cards_are_marked_so_the_sheet_can_leave_them_out(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('50000'), source_type='Salary', date=datetime.date(YEAR, 2, 1), currency='₹')
        html = self.client.get(reverse('year_in_review', args=[YEAR])).content.decode()
        self.assertIn('data-card="savings" data-money="1"', html)
        self.assertNotIn('data-money="1"', html.split('data-card="facts"')[0])    # the spending cards carry no income

    def test_the_spending_cards_never_print_income(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('987654'), source_type='Salary', date=datetime.date(YEAR, 2, 1), currency='₹')
        html = self.client.get(reverse('year_in_review', args=[YEAR])).content.decode()
        spending = html[:html.index('data-card="savings"')]
        self.assertNotIn('9,87,654', spending)
        self.assertNotIn('987654', spending)

    def test_an_empty_year_shows_the_friendly_message(self):
        html = self.client.get(reverse('year_in_review', args=[YEAR - 5])).content.decode()
        self.assertNotIn('class="wr-card', html)
        self.assertIn('enough data', html)

    def test_closing_and_done_lead_back_to_analytics(self):
        html = self.client.get(reverse('year_in_review', args=[YEAR])).content.decode()
        self.assertIn(f'href="{reverse("analytics")}" id="wrClose"', html)
        self.assertIn(f'href="{reverse("analytics")}" class="btn btn-action-cancel">', html)
