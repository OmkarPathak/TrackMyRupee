from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from expenses.account_valuation import get_current
from expenses.models import Account, FundNAVCache, Holding, Valuation
from expenses.nav_provider import BaseNAVProvider, NAVFetchService

User = get_user_model()


class MockSuccessProvider(BaseNAVProvider):
    name = 'MOCK_PRIMARY'
    def __init__(self, nav=Decimal('150.25'), nav_date=None, name='Mock Equity Fund', isin='INF123K01234'):
        self.nav = nav
        self.nav_date = nav_date or date.today()
        self.scheme_name = name
        self.isin = isin
        self.call_count = 0

    def fetch_latest(self, scheme_code: str):
        self.call_count += 1
        return (self.nav, self.nav_date, self.scheme_name, self.isin)


class MockFailureProvider(BaseNAVProvider):
    name = 'MOCK_FAIL'
    def __init__(self):
        self.call_count = 0

    def fetch_latest(self, scheme_code: str):
        self.call_count += 1
        return None


class NAVIntegrationTests(TestCase):

    def setUp(self):
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        self.account_a = Account.objects.create(
            user=self.user_a,
            name='User A Mutual Funds',
            account_type='MUTUAL_FUND',
            currency='₹',
        )
        self.account_b = Account.objects.create(
            user=self.user_b,
            name='User B Mutual Funds',
            account_type='MUTUAL_FUND',
            currency='₹',
        )

        self.holding_a = Holding.objects.create(
            account=self.account_a,
            instrument_name='HDFC Top 100 Fund',
            instrument_type='MF',
            units=Decimal('100.000000'),
            avg_cost=Decimal('120.00'),
            scheme_code='101234',
            is_active=True,
        )

    def test_provider_fallback(self):
        """SPEC §3a: Primary provider fails -> Fallback provider used -> Valuation created."""
        primary_fail = MockFailureProvider()
        fallback_success = MockSuccessProvider(nav=Decimal('180.50'))

        service = NAVFetchService(primary_provider=primary_fail, fallback_provider=fallback_success)
        cache, success = service.fetch_scheme('101234')

        self.assertTrue(success)
        self.assertIsNotNone(cache)
        self.assertEqual(cache.latest_nav, Decimal('180.50'))
        self.assertEqual(primary_fail.call_count, 3)  # Retried 3 times
        self.assertEqual(fallback_success.call_count, 1)

        val = Valuation.objects.filter(holding=self.holding_a, source='AUTOMATED_NAV').first()
        self.assertIsNotNone(val)
        self.assertEqual(val.unit_nav, Decimal('180.50'))
        self.assertEqual(val.value, Decimal('18050.00'))  # 100 units * 180.50

    def test_idempotent_rerun(self):
        """SPEC §3a: Running the fetch job twice for the same day produces exactly 1 Valuation per (holding, as_of_date)."""
        provider = MockSuccessProvider(nav=Decimal('150.25'))
        service = NAVFetchService(primary_provider=provider, fallback_provider=MockFailureProvider())

        # Run 1
        service.fetch_scheme('101234')
        count_1 = Valuation.objects.filter(holding=self.holding_a, as_of_date=provider.nav_date).count()
        self.assertEqual(count_1, 1)

        # Run 2 for same day
        service.fetch_scheme('101234')
        count_2 = Valuation.objects.filter(holding=self.holding_a, as_of_date=provider.nav_date).count()
        self.assertEqual(count_2, 1)

    def test_dedup_across_users(self):
        """
        SPEC §3a & COST_AND_TOKEN_DISCIPLINE:
        Two Holdings (different users) referencing the same scheme_code -> exactly 1 provider call in a single run.
        """
        holding_b = Holding.objects.create(
            account=self.account_b,
            instrument_name='HDFC Top 100 Fund (User B)',
            instrument_type='MF',
            units=Decimal('50.000000'),
            avg_cost=Decimal('120.00'),
            scheme_code='101234',
            is_active=True,
        )

        primary = MockSuccessProvider(nav=Decimal('150.25'))
        service = NAVFetchService(primary_provider=primary, fallback_provider=MockFailureProvider())

        results = service.sync_active_holdings_navs()

        self.assertEqual(results['total_schemes'], 1)
        self.assertEqual(results['successful'], 1)
        # Proven cost-discipline assertion: 1 call for scheme 101234 despite 2 holdings across 2 users
        self.assertEqual(primary.call_count, 1)

        val_a = Valuation.objects.filter(holding=self.holding_a).first()
        val_b = Valuation.objects.filter(holding=holding_b).first()
        self.assertIsNotNone(val_a)
        self.assertIsNotNone(val_b)
        self.assertEqual(val_a.value, Decimal('15025.00'))  # 100 * 150.25
        self.assertEqual(val_b.value, Decimal('7512.50'))   # 50 * 150.25

    def test_bounded_scope(self):
        """SPEC §3a: A scheme_code with no active Holding referencing it is never fetched."""
        # Active holding with scheme_code 101234 (setUp)
        # Inactive holding with scheme_code 999999
        Holding.objects.create(
            account=self.account_a,
            instrument_name='Old Inactive Fund',
            instrument_type='MF',
            units=Decimal('10.00'),
            avg_cost=Decimal('50.00'),
            scheme_code='999999',
            is_active=False,
        )

        primary = MockSuccessProvider()
        service = NAVFetchService(primary_provider=primary, fallback_provider=MockFailureProvider())

        results = service.sync_active_holdings_navs()

        self.assertEqual(results['total_schemes'], 1)
        # Assert only 101234 was requested, 999999 was skipped
        self.assertEqual(primary.call_count, 1)
        self.assertFalse(FundNAVCache.objects.filter(scheme_code='999999').exists())

    def test_staleness_fallback(self):
        """SPEC §3a: Repeated failures -> get_current uses last known cached NAV (not 0, not cost basis fallback)."""
        cache = FundNAVCache.objects.create(
            scheme_code='101234',
            latest_nav=Decimal('200.00'),
            nav_as_of_date=date.today() - timedelta(days=5),
            consecutive_failure_count=3,
        )

        # No Valuation row exists for holding_a yet
        current_val = get_current(self.account_a)
        # 100 units * 200.00 cached_nav + 0 ledger cash = 20000.00
        self.assertEqual(current_val, Decimal('20000.00'))

    @override_settings(CRON_SECRET='test-cron-secret', CRON_ALLOW_QUERY_SECRET=True)
    def test_cron_nav_sync_endpoint(self):
        """HTTP Cron Endpoint /api/cron/sync-nav/ triggers NAV sync cleanly when authorized."""
        # 1. Unauthorized request (no secret) -> 403
        resp = self.client.get('/api/cron/sync-nav/')
        self.assertEqual(resp.status_code, 403)

        # 2. Authorized request with X-Cron-Secret header -> 200
        resp = self.client.get('/api/cron/sync-nav/', HTTP_X_CRON_SECRET='test-cron-secret')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertIn('summary', data)

        # 3. Authorized request with query secret ?secret= -> 200
        resp = self.client.get('/api/cron/sync-nav/?secret=test-cron-secret')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])

    def test_circuit_breaker_backoff(self):
        """SPEC §3a: Circuit-breaker skips persistent failures during normal schedule, but force=True bypasses it."""
        fail_provider = MockFailureProvider()
        service = NAVFetchService(primary_provider=fail_provider, fallback_provider=fail_provider)

        # Create cache row with 3 consecutive failures 1 hour ago
        FundNAVCache.objects.create(
            scheme_code='101234',
            consecutive_failure_count=3,
            last_fetch_attempt_at=timezone.now() - timedelta(hours=1),
        )

        # Normal run (force=False) -> skipped
        results = service.sync_active_holdings_navs(force=False)
        self.assertEqual(results['skipped'], 1)
        self.assertEqual(fail_provider.call_count, 0)

        # Manual "refresh now" (force=True) -> bypasses circuit breaker
        cache, success = service.fetch_scheme('101234', force=True)
        self.assertFalse(success)
        self.assertEqual(fail_provider.call_count, 6)  # 3 primary + 3 fallback

    def test_manual_entry_coexistence(self):
        """SPEC §3a: Manually entered Valuation isn't overwritten by automated fetch; newest created_at wins."""
        # 1. Create manual valuation for today
        manual_val = Valuation.objects.create(
            holding=self.holding_a,
            value=Decimal('25000.00'),
            as_of_date=date.today(),
            unit_nav=Decimal('250.00'),
            source='MANUAL',
        )

        # 2. Automated fetch runs for today
        provider = MockSuccessProvider(nav=Decimal('150.00'), nav_date=date.today())
        service = NAVFetchService(primary_provider=provider, fallback_provider=MockFailureProvider())
        service.fetch_scheme('101234')

        # Assert manual valuation row still exists
        self.assertTrue(Valuation.objects.filter(pk=manual_val.pk).exists())
        # Both valuations exist for today
        vals = list(Valuation.objects.filter(holding=self.holding_a, as_of_date=date.today()))
        self.assertEqual(len(vals), 2)

    def test_holdings_list_view(self):
        """Test dedicated Holdings & Investment Portfolio page rendering."""
        self.client.login(username='user_a', password='password123')
        resp = self.client.get('/holdings/')
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, 'expenses/holding_list.html')
        self.assertIn('holdings', resp.context)
        self.assertIn('total_valuation', resp.context)
        self.assertContains(resp, 'HDFC Top 100 Fund')

