"""Query-budget regression tests for the dashboard and the Server-Timing middleware.

If a budget fails because you legitimately changed the dashboard, re-measure and update
the ceiling consciously - the point is that it never grows by accident.
"""
import sys
from datetime import date, timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.models import Account, Expense, Income

# Measured on a small dataset: cold ~80 queries, warm 5. Ceilings leave modest headroom.
COLD_QUERY_CEILING = 110
WARM_QUERY_CEILING = 4  # session, user, profile (+1 headroom); was 5 before the has_any_data/category caching


class DashboardQueryBudgetTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('budget_user', 'budget@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('1000'))
        for i in range(10):
            Expense.objects.create(user=self.user, date=date.today() - timedelta(days=i), amount=Decimal('100'),
                                   description=f'e{i}', category='Food', account=self.account)
        Income.objects.create(user=self.user, date=date.today(), amount=Decimal('5000'), source='Salary', account=self.account)
        self.client.force_login(self.user)

    def _queries(self):
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('home'))
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_cold_dashboard_query_ceiling(self):
        # Tests bypass the dashboard's warm cache, so every request is a cold build.
        self.assertLessEqual(self._queries(), COLD_QUERY_CEILING)

    @override_settings(TESTING=False)
    def test_warm_dashboard_query_ceiling(self):
        # The view disables its cache under 'test' in sys.argv, so mask argv for this test.
        with mock.patch.object(sys, 'argv', ['manage.py', 'runserver']):
            self.client.get(reverse('home'))  # populate
            self.assertLessEqual(self._queries(), WARM_QUERY_CEILING)


class ServerTimingMiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('st_user', 'st@example.com', 'pw')
        self.client.force_login(self.user)

    def _build(self):
        from django.http import HttpResponse

        from finance_tracker.server_timing import ServerTimingMiddleware
        return ServerTimingMiddleware(lambda request: HttpResponse('ok'))

    @override_settings(ENABLE_SERVER_TIMING=False)
    def test_disabled_by_default(self):
        from django.core.exceptions import MiddlewareNotUsed
        with self.assertRaises(MiddlewareNotUsed):
            self._build()

    @override_settings(ENABLE_SERVER_TIMING=True)
    def test_header_has_only_numbers_and_counts_queries(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from finance_tracker.server_timing import ServerTimingMiddleware

        def view(request):
            User.objects.count()
            User.objects.count()
            return HttpResponse('ok')

        resp = ServerTimingMiddleware(view)(RequestFactory().get('/'))
        header = resp['Server-Timing']
        self.assertIn('total;dur=', header)
        self.assertIn('db;dur=', header)
        self.assertIn('queries;desc="2"', header)
        self.assertNotIn('SELECT', header)

    def test_header_absent_when_disabled_end_to_end(self):
        self.assertNotIn('Server-Timing', self.client.get('/').headers)


class SlowQueryLogTests(TestCase):
    def _run(self, env):
        import logging  # noqa: F401

        from django.http import HttpResponse
        from django.test import RequestFactory

        from finance_tracker.server_timing import ServerTimingMiddleware

        def view(request):
            User.objects.filter(username='secret-value-123').count()
            return HttpResponse('ok')

        with override_settings(ENABLE_SERVER_TIMING=True), mock.patch.dict('os.environ', env):
            with self.assertLogs('finance_tracker.slow_query', level='WARNING') as cm:
                ServerTimingMiddleware(view)(RequestFactory().get('/some/path/'))
        return cm.output

    def test_logs_call_site_and_sql_template_but_never_parameters(self):
        # threshold 0.0001ms => every query counts as slow
        out = self._run({'SLOW_QUERY_LOG_MS': '0.0001'})
        self.assertEqual(len(out), 1)
        line = out[0]
        self.assertIn('slow_query ms=', line)
        self.assertIn('path=/some/path/', line)
        self.assertIn('test_dashboard_query_budget.py:', line)  # first project frame = the view above
        self.assertIn('SELECT COUNT', line)
        self.assertNotIn('secret-value-123', line)

    def test_silent_when_unset(self):
        from django.http import HttpResponse
        from django.test import RequestFactory

        from finance_tracker.server_timing import ServerTimingMiddleware

        def view(request):
            User.objects.count()
            return HttpResponse('ok')

        from finance_tracker import server_timing

        with override_settings(ENABLE_SERVER_TIMING=True), mock.patch.dict('os.environ', {'SLOW_QUERY_LOG_MS': ''}):
            with mock.patch.object(server_timing.slow_query_logger, 'warning') as warn:
                ServerTimingMiddleware(view)(RequestFactory().get('/'))
        warn.assert_not_called()
