"""The dashboard's metric cards compare this month with last month.

A percentage change is only meaningful against a positive base: against a negative one the sign flips (from -59,772 to
65,228 is not "-209%"), which printed "+-209.1%". And a negative amount reads "-₹59,772", not "₹-59,772".
"""
import re
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, Expense, Income, Transfer, UserProfile
from expenses.templatetags.digit_filters import compact_money

D = Decimal


class FilterTests(SimpleTestCase):
    def test_the_sign_goes_in_front_of_the_currency_symbol(self):
        self.assertEqual(compact_money(-59772, '₹'), '-₹59,772')
        self.assertEqual(compact_money(-150000, '₹'), '-₹1.5L')

    def test_positive_amounts_are_unchanged(self):
        self.assertEqual(compact_money(65228, '₹'), '₹65,228')
        self.assertEqual(compact_money(150000, '₹'), '₹1.5L')
        self.assertEqual(compact_money(2500000, '$'), '$2.5M')

    def test_zero_and_amounts_that_round_to_zero_have_no_sign(self):
        self.assertEqual(compact_money(0, '₹'), '₹0')
        self.assertEqual(compact_money(-0.4, '₹'), '₹0')

    def test_what_is_not_a_number_comes_back_as_it_was(self):
        self.assertEqual(compact_money('n/a', '₹'), 'n/a')
        self.assertEqual(compact_money(None, '₹'), None)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('comparison', password='pass12345!')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier, profile.currency = 'PRO', '₹'
        profile.has_seen_tutorial = profile.consent_granted = True
        profile.save()
        self.today = timezone.localdate()
        self.last_month = (self.today.replace(day=1) - timedelta(days=1)).replace(day=10)
        self.bank = self.account('Bank', 'SAVINGS_ACCOUNT', 900000)
        self.fd = self.account('FD', 'FD', 0)
        self.client.force_login(self.user)
        # (the Dashboard sends someone with no entries at all to the onboarding)
        Expense.objects.create(user=self.user, category='Food', amount=D('10'), base_amount=D('10'), currency='₹',
                               date=self.today, account=self.bank)

    def account(self, name, kind, balance):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency='₹')

    def move(self, source, destination, amount, when):
        return Transfer.objects.create(user=self.user, from_account=source, to_account=destination, amount=D(amount),
                                       converted_amount=D(amount), date=when)

    def put_in(self, amount, when):
        self.move(self.bank, self.fd, amount, when)

    def take_out(self, amount, when):
        self.move(self.fd, self.bank, amount, when)

    def home(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return response

    def card(self, response, name):
        """The HTML of one metric card."""
        html = response.content.decode()
        start = html.index(f'id="bento-card-{name}"')
        end = html.find('class="card bento-metric-card"', start)
        return html[start:end if end != -1 else start + 6000]

    def text(self, card):
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', card))


class InvestedComparisonTests(Base):
    def test_from_a_negative_month_to_a_positive_one_shows_the_change_in_money_and_no_percentage(self):
        self.take_out(60000, self.last_month)
        self.put_in(65000, self.today)
        response = self.home()
        data = response.context['prev_month_data']
        self.assertEqual(data['investments'], D('-60000'))
        self.assertIsNone(data['investments_pct'])
        card = self.card(response, 'invested')
        self.assertNotIn('+-', card)
        self.assertNotIn('%', self.text(card).replace('vs last month', ''))
        self.assertIn('1.2L', self.text(card))                           # 65,000 - (-60,000) = 1,25,000
        self.assertIn('bi-caret-up-fill', card)

    def test_getting_more_negative_is_a_fall_with_no_percentage(self):
        self.take_out(60000, self.last_month)
        self.take_out(100000, self.today)
        response = self.home()
        self.assertIsNone(response.context['prev_month_data']['investments_pct'])
        card = self.card(response, 'invested')
        self.assertIn('bi-caret-down-fill', card)
        self.assertEqual(response.context['prev_month_data']['investments_diff_amount'], D('-40000'))
        self.assertNotRegex(self.text(card), r'\d\s?%')

    def test_a_rise_from_a_positive_month_is_a_plus_percentage(self):
        self.put_in(50000, self.last_month)
        self.put_in(100000, self.today)
        response = self.home()
        self.assertEqual(response.context['prev_month_data']['investments_pct'], D('100'))
        self.assertIn('+100.0%', self.card(response, 'invested'))

    def test_a_fall_from_a_positive_month_is_a_minus_percentage_in_red(self):
        self.put_in(100000, self.last_month)
        self.put_in(50000, self.today)
        card = self.card(self.home(), 'invested')
        self.assertIn('-50.0%', card)
        self.assertNotIn('+-', card)
        self.assertIn('bi-caret-down-fill', card)

    def test_after_a_month_of_nothing_there_is_no_percentage(self):
        self.put_in(50000, self.today)
        response = self.home()
        self.assertIsNone(response.context['prev_month_data']['investments_pct'])
        self.assertNotRegex(self.text(self.card(response, 'invested')), r'\d\s?%')

    def test_no_card_ever_prints_a_plus_followed_by_a_minus(self):
        for last, now in ((60000, 65000), (100000, 50000), (50000, 100000)):
            Transfer.objects.filter(user=self.user).delete()
            (self.take_out if last == 60000 else self.put_in)(last, self.last_month)
            self.put_in(now, self.today)
            html = self.home().content.decode()
            for name in ('networth', 'income', 'spent', 'invested'):
                self.assertNotIn('+-', self.card_or_empty(html, name))

    def card_or_empty(self, html, name):
        start = html.find(f'id="bento-card-{name}"')
        if start == -1:
            return ''
        end = html.find('class="card bento-metric-card"', start)
        return html[start:end if end != -1 else start + 6000]


class NegativeAmountTests(Base):
    def test_a_negative_invested_total_reads_minus_then_the_symbol(self):
        self.take_out(60000, self.today)
        text = self.text(self.card(self.home(), 'invested'))
        self.assertIn('-₹60,000', text)
        self.assertNotIn('₹-', text)

    def test_negative_savings_in_the_income_badge_read_the_same_way(self):
        Income.objects.create(user=self.user, amount=D('1000'), base_amount=D('1000'), currency='₹', date=self.today,
                              account=self.bank, source_type='Salary', source='Salary')
        Expense.objects.create(user=self.user, category='Food', amount=D('5000'), base_amount=D('5000'), currency='₹',
                               date=self.today, account=self.bank)
        text = self.text(self.card(self.home(), 'income'))
        self.assertNotIn('₹-', text)
        self.assertIn('-₹', text)

    def test_a_negative_net_worth_reads_the_same_way(self):
        html = render_to_string('expenses/partials/_dashboard_networth.html', {
            'currency_symbol': '₹',
            'nw': {'net_worth': D('-250000'), 'net_worth_change': D('-10000'), 'net_worth_percent': D('-3.8'),
                   'net_worth_trend': [], 'net_worth_labels': []},
        })
        text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html))
        self.assertIn('-₹2.5L', text)
        self.assertNotIn('₹-', text)


class OtherCardsStillCompareCorrectlyTests(Base):
    def test_income_up_is_a_plus_percentage(self):
        for when, amount in ((self.last_month, '20000'), (self.today, '25000')):
            Income.objects.create(user=self.user, amount=D(amount), base_amount=D(amount), currency='₹', date=when,
                                  account=self.bank, source_type='Salary', source='Salary')
        self.assertIn('+25.0%', self.card(self.home(), 'income'))

    def test_spending_down_is_a_minus_percentage_in_green(self):
        for when, amount in ((self.last_month, '40000'), (self.today, '30000')):
            Expense.objects.create(user=self.user, category='Rent', amount=D(amount), base_amount=D(amount), currency='₹',
                                   date=when, account=self.bank)
        card = self.card(self.home(), 'spent')
        self.assertRegex(card, r'-\d+\.\d%')
        self.assertNotIn('+-', card)
