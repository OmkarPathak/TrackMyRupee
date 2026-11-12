"""Invested = money moved into investments minus money taken out; moves between investments are neither."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.investing import invested_totals
from expenses.models import Account, Expense, Transfer, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('net-invested', password='pass12345!')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.today = timezone.localdate()
        self.bank = self.account('ICICI Savings', 'SAVINGS_ACCOUNT', 500000)
        self.fd1 = self.account('FD 1', 'FD', 0)
        self.fd2 = self.account('FD 2', 'FD', 0)
        self.client.force_login(self.user)

    def account(self, name, kind, balance):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency='₹')

    def move(self, source, destination, amount, when=None):
        return Transfer.objects.create(user=self.user, from_account=source, to_account=destination, amount=D(amount),
                                       converted_amount=D(amount), date=when or self.today)

    def totals(self):
        return invested_totals(Transfer.objects.filter(user=self.user))


class TotalsTests(Base):
    def test_money_going_in_counts(self):
        self.move(self.bank, self.fd1, 100000)
        self.assertEqual(self.totals(), {'inflow': D('100000'), 'outflow': D('0.00'), 'net': D('100000')})

    def test_an_fd_that_matures_and_is_renewed_is_not_new_money_twice(self):
        self.move(self.bank, self.fd1, 100000)        # 1L into FD 1
        self.move(self.fd1, self.bank, 110000)        # it matures: 1.1L comes back
        self.move(self.bank, self.fd2, 110000)        # and goes into a new FD
        found = self.totals()
        self.assertEqual((found['inflow'], found['outflow'], found['net']), (D('210000'), D('110000'), D('100000')))

    def test_a_move_between_two_investments_is_neither(self):
        self.move(self.bank, self.fd1, 100000)
        self.move(self.fd1, self.fd2, 100000)
        self.assertEqual(self.totals()['net'], D('100000'))

    def test_more_out_than_in_is_negative(self):
        self.move(self.fd1, self.bank, 50000)
        self.assertEqual(self.totals()['net'], D('-50000'))

    def test_ordinary_transfers_do_not_count(self):
        self.move(self.bank, self.account('Cash', 'CASH_WALLET', 0), 5000)
        self.assertEqual(self.totals()['net'], D('0.00'))


class DashboardTests(Base):
    def setUp(self):
        super().setUp()
        # (the Dashboard sends someone with no entries at all to the onboarding)
        Expense.objects.create(user=self.user, category='Food', amount=D('10'), base_amount=D('10'), currency='₹',
                               date=self.today, account=self.bank)

    def home(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_invested_tile_is_net(self):
        self.move(self.bank, self.fd1, 100000)
        self.move(self.fd1, self.bank, 110000)
        self.move(self.bank, self.fd2, 110000)
        context = self.home().context
        self.assertEqual(context['total_investments'], D('100000'))
        self.assertEqual((context['invested_in'], context['invested_out']), (D('210000'), D('110000')))
        self.assertContains(self.home(), 'bento-invested-inout')

    def test_a_negative_month_says_so_and_does_not_inflate_savings(self):
        self.move(self.fd1, self.bank, 50000)
        response = self.home()
        self.assertEqual(response.context['total_investments'], D('-50000'))
        self.assertContains(response, 'More taken out than put in')
        self.assertGreaterEqual(response.context['remaining_savings'], response.context['savings'])  # (not savings + 50,000)
        self.assertEqual(response.context['remaining_savings'], response.context['savings'])

    def test_no_in_out_line_when_nothing_came_out(self):
        self.move(self.bank, self.fd1, 100000)
        self.assertNotContains(self.home(), 'bento-invested-inout')

    def test_the_tooltip_explains_the_rule(self):
        self.move(self.bank, self.fd1, 100000)
        self.assertContains(self.home(), 'Money you moved into investments, minus money you took out.')


class AnalyticsTests(Base):
    def tile(self, name='analytics'):
        response = self.client.get(reverse(name) + '?time_period=this_month')
        self.assertEqual(response.status_code, 200)
        return {t['key']: t for t in response.context['tiles']}['invested']

    def test_the_invested_tile_is_net_and_explained(self):
        self.move(self.bank, self.fd1, 100000)
        self.move(self.fd1, self.bank, 110000)
        self.move(self.bank, self.fd2, 110000)
        tile = self.tile()
        self.assertIn('1,00,000', tile['value'])
        self.assertIn('minus money you took out', tile['help'])

    def test_negative_says_more_was_taken_out(self):
        self.move(self.fd1, self.bank, 50000)
        tile = self.tile()
        self.assertIn('−₹50,000', tile['value'])
        self.assertEqual(tile['delta'], 'More taken out than put in')

    def test_the_cashflow_page_still_draws_with_a_negative_month(self):
        self.move(self.fd1, self.bank, 50000)
        self.assertEqual(self.client.get(reverse('analytics-cashflow') + '?time_period=this_month').status_code, 200)

    def test_a_move_between_investments_is_not_invested(self):
        self.move(self.fd1, self.fd2, 80000)
        self.assertIn('0', self.tile()['value'])
        self.assertNotIn('80,000', self.tile()['value'])
