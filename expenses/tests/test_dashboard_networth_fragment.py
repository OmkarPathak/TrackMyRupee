"""The Dashboard's Net Worth and Asset Allocation cards are fetched after the page paints (when the Insights Engine is
on) and included directly once their figures are cached."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.models import Account, Expense
from expenses.views.dashboard_networth import cached_net_worth_card, networth_cache_key

FRAGMENT_URL = 'dashboard-networth'


class NetWorthFragmentBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('nwf', 'nwf@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('50000'))
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('100'), description='x',
                               category='Food', account=self.bank)
        self.client.force_login(self.user)
        cache.clear()

    def home(self):
        return self.client.get(reverse('home'))


@override_settings(INSIGHTS_ENGINE_MODE='on')
class EngineOnTests(NetWorthFragmentBase):
    def test_cold_page_asks_for_the_card_instead_of_working_it_out(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.home()
        self.assertContains(response, f'hx-get="{reverse(FRAGMENT_URL)}"')
        self.assertContains(response, 'id="nw-slot"')
        self.assertIsNone(response.context['nw'])
        self.assertNotIn('net_worth', response.context)
        self.assertFalse([q for q in ctx.captured_queries if 'journalline' in q['sql']],
                         'the ledger is read by the fragment, not by the page')

    def test_fragment_returns_the_figures_and_the_allocation_and_caches_them(self):
        response = self.client.get(reverse(FRAGMENT_URL))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('bento-metric-value', html)
        self.assertIn('id="bentoNwSparkline"', html)
        self.assertIn('id="net-worth-trend"', html)
        self.assertIn('id="nw-allocation-slot" hx-swap-oob="innerHTML"', html)
        self.assertIn('allocation-segment', html)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        nw = cache.get(networth_cache_key(self.user.pk))
        self.assertEqual(nw['net_worth'], Decimal('49900'))
        self.assertEqual(len(nw['net_worth_trend']), len(nw['net_worth_labels']))

    def test_fragment_costs_no_context_processor_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse(FRAGMENT_URL))
        sql = ' '.join(q['sql'] for q in ctx.captured_queries)
        for table in ('expenses_notification', 'expenses_announcement', 'webpush_pushinformation'):
            self.assertNotIn(table, sql)

    def test_page_includes_the_cached_figures_without_asking(self):
        self.client.get(reverse(FRAGMENT_URL))
        response = self.home()
        self.assertNotContains(response, f'hx-get="{reverse(FRAGMENT_URL)}"')
        self.assertEqual(response.context['nw']['net_worth'], Decimal('49900'))
        self.assertEqual(response.context['net_worth'], Decimal('49900'))
        self.assertContains(response, 'id="bentoNwSparkline"')
        self.assertContains(response, 'allocation-segment')

    def test_a_new_transaction_retires_the_cached_figures(self):
        self.client.get(reverse(FRAGMENT_URL))
        self.assertIsNotNone(cached_net_worth_card(self.user.pk))
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('500'), description='y',
                               category='Food', account=self.bank)
        self.assertIsNone(cached_net_worth_card(self.user.pk))
        self.assertContains(self.home(), f'hx-get="{reverse(FRAGMENT_URL)}"')

    def test_another_persons_figures_are_never_served(self):
        self.client.get(reverse(FRAGMENT_URL))
        other = User.objects.create_user('nwf2', 'nwf2@example.com', 'pw')
        other.profile.has_seen_tutorial = True
        other.profile.save()
        Expense.objects.create(user=other, date=date.today(), amount=Decimal('1'), description='z', category='Food')
        self.client.force_login(other)
        self.assertIsNone(cached_net_worth_card(other.pk))
        self.assertContains(self.home(), f'hx-get="{reverse(FRAGMENT_URL)}"')

    def test_fragment_needs_a_login(self):
        self.client.logout()
        response = self.client.get(reverse(FRAGMENT_URL))
        self.assertEqual(response.status_code, 302)

    def test_figures_are_worked_out_again_for_another_language(self):
        self.client.get(reverse(FRAGMENT_URL))
        nw = cache.get(networth_cache_key(self.user.pk))
        nw['language'] = 'xx'
        cache.set(networth_cache_key(self.user.pk), nw)
        self.assertIsNone(cached_net_worth_card(self.user.pk))


@override_settings(INSIGHTS_ENGINE_MODE='off')
class EngineOffTests(NetWorthFragmentBase):
    def test_page_works_net_worth_out_itself_and_never_asks(self):
        response = self.home()
        self.assertNotContains(response, f'hx-get="{reverse(FRAGMENT_URL)}"')
        self.assertEqual(response.context['net_worth'], Decimal('49900'))
        self.assertEqual(response.context['nw']['net_worth'], Decimal('49900'))
        self.assertIn('net_worth_before_liabilities', response.context)
        self.assertContains(response, 'id="bentoNwSparkline"')
