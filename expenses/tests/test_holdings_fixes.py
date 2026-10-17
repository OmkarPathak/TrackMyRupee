"""Holdings small fixes: which accounts accept holdings, stock vs fund handling, cache freshness, manual price."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, Holding, UserProfile, Valuation

D = Decimal
HOLDING_TYPES = ['MUTUAL_FUND', 'DEMAT', 'ETF', 'SGB', 'ULIP', 'GOLD']
NON_HOLDING_TYPES = ['NPS', 'PPF', 'EPF', 'SAVINGS_ACCOUNT', 'FD', 'CREDIT_CARD', 'RD']


class HoldingsBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='holder', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)

    def account(self, account_type, name=None):
        return Account.objects.create(user=self.user, name=name or account_type.title(), account_type=account_type,
                                      balance=D('0'), currency='₹')

    def add(self, account, **kw):
        data = {'instrument_name': 'Some Holding', 'scheme_code': '', 'units': '10', 'avg_cost': '50',
                'account_id': account.pk, 'next': '/holdings/'}
        data.update(kw)
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            return self.client.post(reverse('holding-create-global'), data)

    def holding(self, account, **kw):
        values = dict(account=account, instrument_name='Reliance', units=D('10'), avg_cost=D('100'), currency='₹',
                      instrument_type='STOCK')
        values.update(kw)
        return Holding.objects.create(**values)


class AccountsThatTakeHoldingsTests(HoldingsBase):
    def test_add_form_lists_only_accounts_valued_from_holdings(self):
        for t in HOLDING_TYPES + NON_HOLDING_TYPES:
            self.account(t)
        response = self.client.get(reverse('holding-list'))
        listed = {a.account_type for a in response.context['user_accounts']}
        self.assertEqual(listed, set(HOLDING_TYPES))

    def test_inactive_and_other_users_accounts_are_not_listed(self):
        mine = self.account('DEMAT', 'Active')
        gone = self.account('DEMAT', 'Closed')
        gone.is_active = False
        gone.save()
        other = User.objects.create_user(username='someone', password='password')
        Account.objects.create(user=other, name='Theirs', account_type='DEMAT', balance=D('0'))
        response = self.client.get(reverse('holding-list'))
        self.assertEqual([a.pk for a in response.context['user_accounts']], [mine.pk])

    def test_holdings_are_refused_on_accounts_that_ignore_them(self):
        for t in NON_HOLDING_TYPES:
            with self.subTest(account_type=t):
                acct = self.account(t)
                response = self.add(acct)
                self.assertEqual(response.status_code, 302)
                self.assertFalse(Holding.objects.filter(account=acct).exists())

    def test_the_per_account_url_applies_the_same_rule(self):
        nps = self.account('NPS')
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            self.client.post(reverse('holding-create', args=[nps.uuid]),
                             {'instrument_name': 'X', 'units': '1', 'avg_cost': '1', 'next': '/holdings/'})
        self.assertFalse(Holding.objects.filter(account=nps).exists())

    def test_holdings_are_accepted_on_every_valued_from_holdings_type(self):
        for t in HOLDING_TYPES:
            with self.subTest(account_type=t):
                acct = self.account(t)
                self.add(acct, scheme_code='123' if t == 'MUTUAL_FUND' else '')
                self.assertEqual(Holding.objects.filter(account=acct).count(), 1)


class InstrumentTypeTests(HoldingsBase):
    def test_instrument_type_follows_the_account(self):
        expected = {'MUTUAL_FUND': 'MF', 'ULIP': 'MF', 'DEMAT': 'STOCK', 'ETF': 'STOCK', 'SGB': 'OTHER', 'GOLD': 'OTHER'}
        for t, instrument in expected.items():
            with self.subTest(account_type=t):
                acct = self.account(t)
                self.add(acct)
                self.assertEqual(Holding.objects.get(account=acct).instrument_type, instrument)

    def test_a_stock_name_is_never_matched_to_a_fund(self):
        from expenses.models import AMFIScheme
        AMFIScheme.objects.create(scheme_code='777', scheme_name='Reliance Pharma Fund Direct Growth')
        demat = self.account('DEMAT')
        with patch('requests.get') as network:
            self.add(demat, instrument_name='Reliance')
        network.assert_not_called()
        self.assertIsNone(Holding.objects.get(account=demat).scheme_code)

    def test_a_fund_name_is_still_matched_when_the_code_is_blank(self):
        from expenses.models import AMFIScheme
        AMFIScheme.objects.create(scheme_code='888', scheme_name='Index Fund Direct Growth')
        funds = self.account('MUTUAL_FUND')
        self.add(funds, instrument_name='Index Fund')
        self.assertEqual(Holding.objects.get(account=funds).scheme_code, '888')


class NetWorthCacheTests(HoldingsBase):
    def key(self):
        return f'account_net_worth_{self.user.id}'

    def test_adding_a_holding_clears_the_cached_net_worth(self):
        demat = self.account('DEMAT')
        cache.set(self.key(), 'stale', 300)
        self.holding(demat)
        self.assertIsNone(cache.get(self.key()))

    def test_removing_a_holding_clears_the_cached_net_worth(self):
        demat = self.account('DEMAT')
        h = self.holding(demat)
        cache.set(self.key(), 'stale', 300)
        self.client.post(reverse('holding-delete', args=[h.pk]), {'next': '/holdings/'})
        self.assertIsNone(cache.get(self.key()))

    def test_a_new_valuation_clears_the_cached_net_worth(self):
        h = self.holding(self.account('DEMAT'))
        cache.set(self.key(), 'stale', 300)
        Valuation.objects.create(holding=h, value=D('1200.00'), as_of_date=timezone.localdate(), unit_nav=D('120'))
        self.assertIsNone(cache.get(self.key()))

    def test_another_users_change_leaves_this_cache_alone(self):
        cache.set(self.key(), 'mine', 300)
        other = User.objects.create_user(username='someone', password='password')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='DEMAT', balance=D('0'))
        Holding.objects.create(account=theirs, instrument_name='X', units=D('1'), avg_cost=D('1'))
        self.assertEqual(cache.get(self.key()), 'mine')


class ManualValuationTests(HoldingsBase):
    def setUp(self):
        super().setUp()
        self.demat = self.account('DEMAT')
        self.stock = self.holding(self.demat)
        self.url = reverse('holding-valuation', args=[self.stock.pk])

    def post(self, **kw):
        data = {'unit_price': '125.50', 'next': '/holdings/'}
        data.update(kw)
        return self.client.post(self.url, data)

    def test_saves_a_unit_price_and_value_that_follows_the_units(self):
        response = self.post()
        self.assertRedirects(response, '/holdings/', fetch_redirect_response=False)
        v = Valuation.objects.get(holding=self.stock)
        self.assertEqual((v.unit_nav, v.value, v.source, v.as_of_date),
                         (D('125.500000'), D('1255.00'), 'MANUAL', timezone.localdate()))
        self.stock.units = D('20')
        self.assertEqual(v.value_for(self.stock.units), D('2510.00'))

    def test_the_same_day_is_updated_not_duplicated(self):
        self.post(unit_price='100')
        self.post(unit_price='110')
        self.assertEqual(Valuation.objects.filter(holding=self.stock).count(), 1)
        self.assertEqual(Valuation.objects.get(holding=self.stock).value, D('1100.00'))

    def test_an_earlier_date_is_accepted_and_the_latest_date_wins(self):
        today = timezone.localdate()
        self.post(unit_price='90', as_of_date=(today - timedelta(days=30)).isoformat())
        self.post(unit_price='110')
        self.assertEqual(Valuation.objects.filter(holding=self.stock).count(), 2)
        self.assertEqual(Valuation.objects.filter(holding=self.stock).first().unit_nav, D('110'))

    def test_bad_input_is_refused(self):
        for bad in ({'unit_price': ''}, {'unit_price': '0'}, {'unit_price': '-5'}, {'unit_price': 'abc'},
                    {'unit_price': 'NaN'}, {'unit_price': 'Infinity'}, {'unit_price': '99999999999'},
                    {'as_of_date': (timezone.localdate() + timedelta(days=2)).isoformat()}):
            with self.subTest(bad=bad):
                self.post(**bad)
        self.assertFalse(Valuation.objects.filter(holding=self.stock).exists())

    def test_a_garbage_date_falls_back_to_today(self):
        self.post(as_of_date='not-a-date')
        self.assertEqual(Valuation.objects.get(holding=self.stock).as_of_date, timezone.localdate())

    def test_needs_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_priced_from_the_nav_feed_is_refused(self):
        fund = self.holding(self.account('MUTUAL_FUND'), scheme_code='123', instrument_type='MF')
        self.client.post(reverse('holding-valuation', args=[fund.pk]), {'unit_price': '10', 'next': '/holdings/'})
        self.assertFalse(Valuation.objects.filter(holding=fund).exists())

    def test_another_users_holding_is_not_found(self):
        other = User.objects.create_user(username='someone', password='password')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='DEMAT', balance=D('0'))
        h = Holding.objects.create(account=theirs, instrument_name='X', units=D('1'), avg_cost=D('1'))
        response = self.client.post(reverse('holding-valuation', args=[h.pk]), {'unit_price': '10'})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Valuation.objects.filter(holding=h).exists())

    def test_a_removed_holding_cannot_be_valued(self):
        self.stock.is_active = False
        self.stock.save()
        self.assertEqual(self.post().status_code, 404)

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.post().status_code, 302)
        self.assertFalse(Valuation.objects.exists())

    def test_next_must_stay_on_this_site(self):
        response = self.post(next='https://evil.example/x')
        self.assertNotIn('evil.example', response['Location'])

    def test_net_worth_uses_the_manual_price(self):
        self.post(unit_price='125.50')
        total, balances = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(D(str(balances[self.demat.id])), D('1255.00'))

    def test_the_page_offers_update_value_for_priced_by_hand_and_refresh_for_feed_holdings(self):
        self.holding(self.account('MUTUAL_FUND'), instrument_name='Feed Fund', scheme_code='123', instrument_type='MF')
        response = self.client.get(reverse('holding-list'))
        self.assertContains(response, reverse('holding-valuation', args=[self.stock.pk]))
        self.assertContains(response, 'Update value')
        self.assertContains(response, 'Refresh NAV')
