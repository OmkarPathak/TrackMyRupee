"""NAV rules: which day's NAV a purchase gets, stamp duty, history lookup, estimated and pending purchases."""
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses import holdings_service as hs
from expenses import nav_provider, nav_rules
from expenses.models import (
    Account,
    FundNAVCache,
    FundNAVHistory,
    Holding,
    HoldingTransaction,
    MarketHoliday,
    Transfer,
    UserProfile,
)

D = Decimal
WED, THU, FRI, SAT, SUN, MON, TUE = (date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17),
                                     date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 20))
# the history MFapi would return on Sat 17 Oct: weekdays only, newest first
HISTORY = [(FRI, D('52.00')), (THU, D('51.00')), (WED, D('50.00')), (date(2026, 10, 13), D('49.00')),
           (date(2026, 10, 12), D('48.50')), (date(2026, 10, 9), D('48.00')), (date(2026, 10, 8), D('47.00'))]


class RulesTests(SimpleTestCase):
    def test_business_days(self):
        self.assertTrue(nav_rules.is_business_day(WED))
        self.assertFalse(nav_rules.is_business_day(SAT))
        self.assertFalse(nav_rules.is_business_day(SUN))
        self.assertFalse(nav_rules.is_business_day(date(2026, 1, 26)))     # Republic Day, fixed
        self.assertFalse(nav_rules.is_business_day(WED, frozenset({WED})))  # a table holiday

    def test_standard_cutoff(self):
        f = nav_rules.applicable_nav_date
        self.assertEqual(f(WED, before_cutoff=True), WED)
        self.assertEqual(f(WED, before_cutoff=False), THU)
        self.assertEqual(f(FRI, before_cutoff=False), MON)
        self.assertEqual(f(SAT, before_cutoff=True), MON)
        self.assertEqual(f(SAT, before_cutoff=False), MON)
        self.assertEqual(f(SUN, before_cutoff=False), MON)

    def test_a_holiday_moves_the_nav_to_the_next_business_day(self):
        holidays = frozenset({THU})
        self.assertEqual(nav_rules.applicable_nav_date(THU, before_cutoff=True, holidays=holidays), FRI)
        self.assertEqual(nav_rules.applicable_nav_date(WED, before_cutoff=False, holidays=holidays), FRI)

    def test_liquid_funds_use_the_previous_business_day_before_the_cutoff(self):
        f = nav_rules.applicable_nav_date
        self.assertEqual(f(WED, before_cutoff=True, category=nav_rules.LIQUID), date(2026, 10, 13))
        self.assertEqual(f(MON, before_cutoff=True, category=nav_rules.LIQUID), FRI)
        self.assertEqual(f(WED, before_cutoff=False, category=nav_rules.LIQUID), WED)
        self.assertEqual(f(SAT, before_cutoff=True, category=nav_rules.LIQUID), FRI)

    def test_fund_category_from_the_name(self):
        for name, expected in (('HDFC Liquid Fund - Direct - Growth', 'LIQUID'), ('Axis Overnight Fund', 'LIQUID'),
                               ('Parag Parikh Flexi Cap', 'STANDARD'), ('Liquidity Plus Equity', 'STANDARD'),
                               (None, 'STANDARD')):
            self.assertEqual(nav_rules.fund_category(name), expected, name)

    def test_stamp_duty_and_units(self):
        self.assertEqual(nav_rules.stamp_duty(D('10000')), D('0.50'))
        self.assertEqual(nav_rules.stamp_duty(D('5000')), D('0.25'))
        self.assertEqual(nav_rules.units_for(D('10000'), D('50')), D('199.990'))
        self.assertEqual(nav_rules.units_for(D('1000'), D('33.33')), D('30.002'))


@patch('expenses.nav_provider.fetch_scheme_history', return_value=HISTORY)
class HistoryLookupTests(TestCase):
    def today(self, day):
        return patch('django.utils.timezone.localdate', return_value=day)

    def test_finds_the_first_nav_on_or_after_the_date(self, fetch):
        self.assertEqual(nav_provider.nav_on_or_after('111', WED), (WED, D('50.00')))
        self.assertEqual(nav_provider.nav_on_or_after('111', SAT), None)            # nothing published after Friday

    def test_a_weekend_lands_on_the_next_published_nav(self, fetch):
        fetch.return_value = HISTORY + [(MON, D('53.00'))]
        self.assertEqual(nav_provider.nav_on_or_after('111', SAT), (MON, D('53.00')))

    def test_history_is_fetched_once_and_then_read_from_the_database(self, fetch):
        nav_provider.nav_on_or_after('111', WED)
        nav_provider.nav_on_or_after('111', THU)
        nav_provider.nav_on_or_after('111', FRI)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(FundNAVHistory.objects.filter(scheme_code='111').count() > 0, True)

    def test_a_missing_nav_is_not_refetched_within_the_hour(self, fetch):
        nav_provider.nav_on_or_after('111', SAT)
        nav_provider.nav_on_or_after('111', SAT)
        self.assertEqual(fetch.call_count, 1)

    def test_an_older_date_than_our_history_fetches_again(self, fetch):
        nav_provider.nav_on_or_after('111', WED)
        cache = FundNAVCache.objects.get(scheme_code='111')
        self.assertLessEqual(cache.history_from, WED - timedelta(days=31) + timedelta(days=0))
        nav_provider.nav_on_or_after('111', WED - timedelta(days=200))
        self.assertEqual(fetch.call_count, 2)

    def test_a_gap_in_history_is_not_mistaken_for_a_nav(self, fetch):
        fetch.return_value = [(date(2026, 10, 30), D('60.00'))]
        self.assertIsNone(nav_provider.nav_on_or_after('111', WED))

    def test_a_failed_fetch_returns_none(self, fetch):
        fetch.return_value = None
        self.assertIsNone(nav_provider.nav_on_or_after('111', WED))

    def test_no_fetch_when_told_not_to(self, fetch):
        self.assertIsNone(nav_provider.nav_on_or_after('111', WED, fetch=False))
        fetch.assert_not_called()


class FakeProvider(nav_provider.BaseNAVProvider):
    name = 'FAKE'

    def __init__(self, nav, nav_date):
        self.result = (nav, nav_date, 'Alpha Fund', None)

    def fetch_latest(self, scheme_code):
        return self.result


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='navuser', password='password')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')
        self.funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                            balance=D('0'), currency='₹')
        self.fund = hs.create_holding(self.funds, name='Alpha Fund', units='10', price='40', scheme_code='111')
        self.history = patch('expenses.nav_provider.fetch_scheme_history', return_value=list(HISTORY))
        self.history.start()
        self.addCleanup(self.history.stop)
        self.today_patch = patch('django.utils.timezone.localdate', return_value=SAT)
        self.today_patch.start()
        self.addCleanup(self.today_patch.stop)


class EstimatePurchaseTests(Base):
    def test_amount_only_is_priced_from_the_nav_after_stamp_duty(self):
        tx = hs.add_purchase(self.fund, date=WED, amount='10000')
        self.assertEqual((tx.status, tx.nav_date, tx.price, tx.units, tx.stamp_duty),
                         ('ESTIMATED', WED, D('50.000000'), D('199.990000'), D('0.50')))
        self.fund.refresh_from_db()
        self.assertEqual(self.fund.units, D('209.990000'))
        # cost includes the duty: 400 opening + 10,000 over the units
        self.assertEqual(self.fund.cost_basis, D('10400.00'))

    def test_after_the_cutoff_is_priced_a_day_later(self):
        tx = hs.add_purchase(self.fund, date=WED, amount='10000', before_cutoff=False)
        self.assertEqual((tx.nav_date, tx.price), (THU, D('51.000000')))
        self.assertFalse(tx.before_cutoff)

    def test_a_saturday_order_gets_mondays_nav_which_is_not_published_yet(self):
        tx = hs.add_purchase(self.fund, date=SAT, amount='10000')
        self.assertEqual((tx.status, tx.units, tx.price, tx.nav_date), ('PENDING', None, None, MON))
        self.fund.refresh_from_db()
        self.assertEqual(self.fund.units, D('10.000000'))      # pending money is not units yet

    def test_pending_purchases_still_count_against_the_transfer(self):
        t = Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.funds,
                                    amount=D('10000'), date=SAT)
        hs.add_purchase(self.fund, date=SAT, amount='10000', transfer=t)
        self.assertEqual(hs.unallocated_amount(t), D('0.00'))

    def test_pending_is_filled_when_the_nav_is_published(self):
        tx = hs.add_purchase(self.fund, date=SAT, amount='10000')
        self.history.stop()
        with patch('expenses.nav_provider.fetch_scheme_history', return_value=[(MON, D('55.00'))] + HISTORY):
            FundNAVCache.objects.filter(scheme_code='111').update(history_synced_at=timezone.now() - timedelta(hours=2))
            self.assertEqual(hs.resolve_pending(), 1)
        self.history.start()
        tx.refresh_from_db()
        self.assertEqual((tx.status, tx.nav_date, tx.price, tx.units), ('ESTIMATED', MON, D('55.000000'), D('181.809000')))
        self.fund.refresh_from_db()
        self.assertEqual(self.fund.units, D('191.809000'))

    def test_still_unpublished_stays_pending(self):
        tx = hs.add_purchase(self.fund, date=SAT, amount='10000')
        self.assertEqual(hs.resolve_pending(), 0)
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'PENDING')

    def test_an_amount_for_a_holding_without_a_feed_is_refused(self):
        stock = Holding.objects.create(account=self.funds, instrument_name='NoFeed', units=D('1'), avg_cost=D('1'), currency='₹')
        with self.assertRaises(hs.HoldingError):
            hs.add_purchase(stock, date=WED, amount='500')

    def test_units_given_means_confirmed_and_no_nav_lookup(self):
        self.history.stop()
        with patch('expenses.nav_provider.fetch_scheme_history') as fetch:
            tx = hs.add_purchase(self.fund, date=WED, units='20', amount='1000')
        self.history.start()
        fetch.assert_not_called()
        self.assertEqual(tx.status, 'CONFIRMED')

    def test_editing_with_units_confirms_and_amount_only_estimates_again(self):
        tx = hs.add_purchase(self.fund, date=WED, amount='10000')
        hs.update_purchase(tx, date=WED, units='199.5', amount='10000')
        tx.refresh_from_db()
        self.assertEqual((tx.status, tx.units), ('CONFIRMED', D('199.500000')))
        hs.update_purchase(tx, date=WED, amount='10000', before_cutoff=False)
        tx.refresh_from_db()
        self.assertEqual((tx.status, tx.nav_date, tx.price), ('ESTIMATED', THU, D('51.000000')))

    def test_liquid_funds_use_the_earlier_cutoff_rule(self):
        liquid = hs.create_holding(self.funds, name='Alpha Liquid Fund', units='1', price='10', scheme_code='222')
        tx = hs.add_purchase(liquid, date=WED, amount='10000')
        self.assertEqual(tx.nav_date, date(2026, 10, 13))

    def test_a_market_holiday_in_the_table_moves_the_nav(self):
        MarketHoliday.objects.create(date=WED, name='Test holiday')
        tx = hs.add_purchase(self.fund, date=WED, amount='10000')
        self.assertEqual(tx.nav_date, THU)

    def test_a_future_order_is_refused(self):
        with self.assertRaises(hs.HoldingError):
            hs.add_purchase(self.fund, date=SUN, amount='10000')

    def test_new_fund_from_just_an_amount(self):
        h = hs.create_holding(self.funds, name='Beta Fund', amount='5000', date=WED, scheme_code='333')
        tx = h.transactions.get()
        self.assertEqual((tx.status, tx.units), ('ESTIMATED', D('99.995000')))

    def test_new_fund_with_no_scheme_code_and_no_units_is_refused(self):
        with patch('expenses.holdings_service.resolve_scheme_code', return_value=''), self.assertRaises(hs.HoldingError):
            hs.create_holding(self.funds, name='Mystery', amount='5000', date=WED)
        self.assertFalse(Holding.objects.filter(instrument_name='Mystery').exists())


class FetchSchemeResolvesPendingTests(Base):
    def test_a_published_nav_fills_waiting_purchases_and_is_kept_in_history(self):
        tx = hs.add_purchase(self.fund, date=SAT, amount='10000')
        self.history.stop()
        FundNAVCache.objects.filter(scheme_code='111').update(history_synced_at=timezone.now() - timedelta(hours=2))
        with patch('expenses.nav_provider.fetch_scheme_history', return_value=[(MON, D('55.00'))] + HISTORY):
            service = nav_provider.NAVFetchService(primary_provider=FakeProvider(D('55.00'), MON))
            _cache, ok = service.fetch_scheme('111', force=True)
        self.history.start()
        self.assertTrue(ok)
        self.assertTrue(FundNAVHistory.objects.filter(scheme_code='111', nav_date=MON, nav=D('55.00')).exists())
        tx.refresh_from_db()
        self.assertEqual((tx.status, tx.nav_date), ('ESTIMATED', MON))


class AllocateWithEstimatesTests(Base):
    def post(self, rows, amount='10000', when=WED, **kw):
        data = {'date': when.isoformat(), 'amount': amount, 'from_account': self.bank.pk,
                'to_account': self.funds.pk, 'description': 'SIP', 'client_dedup_key': str(uuid.uuid4()),
                'allocations': json.dumps(rows)}
        data.update(kw)
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme', return_value=(None, True)):
            return self.client.post(reverse('transfer-create'), data)

    def test_rows_without_units_are_priced_from_the_nav(self):
        response = self.post([{'holding_id': self.fund.pk, 'amount': '6000'},
                              {'holding_id': None, 'name': 'Gamma Fund', 'scheme_code': '444', 'amount': '4000'}])
        self.assertEqual(response.status_code, 302)
        rows = {tx.holding.instrument_name: tx for tx in HoldingTransaction.objects.filter(transfer__isnull=False)}
        self.assertEqual({k: (v.status, v.nav_date) for k, v in rows.items()},
                         {'Alpha Fund': ('ESTIMATED', WED), 'Gamma Fund': ('ESTIMATED', WED)})
        self.assertEqual(rows['Alpha Fund'].units, D('119.994000'))

    def test_the_cutoff_choice_is_used(self):
        self.post([{'holding_id': self.fund.pk, 'amount': '6000', 'cutoff': 'after'}])
        self.assertEqual(HoldingTransaction.objects.get(transfer__isnull=False).nav_date, THU)

    def test_a_transfer_made_today_after_close_waits_for_the_nav(self):
        self.post([{'holding_id': self.fund.pk, 'amount': '6000'}], when=SAT)
        tx = HoldingTransaction.objects.get(transfer__isnull=False)
        self.assertEqual((tx.status, tx.units), ('PENDING', None))

    def test_a_row_for_a_holding_without_a_feed_needs_units(self):
        stock = Holding.objects.create(account=self.funds, instrument_name='NoFeed', units=D('1'), avg_cost=D('1'), currency='₹')
        response = self.post([{'holding_id': stock.pk, 'amount': '600'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transfer.objects.exists())

    def test_mixed_rows_with_and_without_units(self):
        self.post([{'holding_id': self.fund.pk, 'amount': '6000', 'units': '100'},
                   {'holding_id': None, 'name': 'Gamma Fund', 'scheme_code': '444', 'amount': '4000'}])
        statuses = sorted(HoldingTransaction.objects.filter(transfer__isnull=False).values_list('status', flat=True))
        self.assertEqual(statuses, ['CONFIRMED', 'ESTIMATED'])

    def test_the_estimate_is_looked_up_before_saving_so_a_failure_saves_nothing(self):
        with patch('expenses.holdings_service.estimate_purchase_terms', side_effect=hs.HoldingError('no NAV')):
            response = self.post([{'holding_id': self.fund.pk, 'amount': '6000'}])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transfer.objects.exists())


class HoldingPageStatusTests(Base):
    def test_page_shows_estimated_and_pending_purchases(self):
        hs.add_purchase(self.fund, date=WED, amount='10000')
        hs.add_purchase(self.fund, date=SAT, amount='2000')
        response = self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        self.assertContains(response, 'Estimated')
        self.assertContains(response, 'Waiting for NAV')
        self.assertEqual(response.context['pending_count'], 1)
        self.assertEqual(response.context['pending_amount'], D('2000.00'))
        self.assertContains(response, 'waiting for its NAV')

    def test_visiting_the_page_fills_a_purchase_whose_nav_has_arrived(self):
        tx = hs.add_purchase(self.fund, date=SAT, amount='10000')
        FundNAVHistory.objects.create(scheme_code='111', nav_date=MON, nav=D('55'))
        FundNAVCache.objects.filter(scheme_code='111').update(history_synced_at=timezone.now())
        self.client.get(reverse('holding-detail', args=[self.fund.pk]))
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'ESTIMATED')

    def test_buy_more_with_only_an_amount_and_a_cutoff_choice(self):
        response = self.client.post(reverse('holding-buy', args=[self.fund.pk]), {
            'date': WED.isoformat(), 'amount': '10000', 'cutoff': 'after'})
        self.assertEqual(response.status_code, 302)
        tx = self.fund.transactions.order_by('-id').first()
        self.assertEqual((tx.status, tx.nav_date, tx.before_cutoff), ('ESTIMATED', THU, False))

    def test_editing_an_estimate_with_the_modal_values_confirms_it(self):
        tx = hs.add_purchase(self.fund, date=WED, amount='10000')
        self.client.post(reverse('holding-purchase-edit', args=[tx.pk]), {
            'date': WED.isoformat(), 'units': str(tx.units), 'price': str(tx.price), 'amount': '10000'})
        tx.refresh_from_db()
        self.assertEqual(tx.status, 'CONFIRMED')

    def test_the_modal_offers_the_cutoff_only_for_funds_with_a_feed(self):
        self.assertContains(self.client.get(reverse('holding-detail', args=[self.fund.pk])), 'id="purchaseCutoff"')
        stock = Holding.objects.create(account=self.funds, instrument_name='NoFeed', units=D('1'), avg_cost=D('1'), currency='₹')
        self.assertNotContains(self.client.get(reverse('holding-detail', args=[stock.pk])), 'id="purchaseCutoff"')
