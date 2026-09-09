from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, PhysicalAsset, Loan, AssetValuation


class LandingDemoWidgetTest(TestCase):
    def setUp(self):
        cache.clear()
        User.objects.filter(username='demo').delete()

    def tearDown(self):
        cache.clear()

    def test_missing_demo_user_returns_empty_list_gracefully(self):
        """When the demo user does not exist in DB, LandingPageView shouldn't crash."""
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('demo_networth_rows', response.context)
        self.assertEqual(response.context['demo_networth_rows'], [])
        self.assertContains(response, 'id="networth-demo-data"')

    def test_landing_page_includes_demo_breakdown(self):
        """When setup_demo_user runs, LandingPageView includes populated breakdown in context."""
        call_command('setup_demo_user')
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)
        
        rows = response.context['demo_networth_rows']
        self.assertTrue(len(rows) > 0)
        
        category_labels = [r['category_label'] for r in rows]
        # Check assets are present
        self.assertIn('Cash & Bank', category_labels)
        self.assertIn('Fixed-Income', category_labels)
        self.assertIn('Investments', category_labels)
        self.assertIn('Physical Assets', category_labels)
        # Check liabilities are present
        self.assertIn('Credit Card', category_labels)
        self.assertIn('Loans', category_labels)

        # Check ordering: Assets first, liabilities last
        liab_seen = False
        for r in rows:
            if r['is_liability']:
                liab_seen = True
            else:
                self.assertFalse(liab_seen, "Assets must come before liabilities in row breakdown")

    def test_demo_breakdown_caching(self):
        """The demo net worth breakdown is cached in Django cache after first view request."""
        call_command('setup_demo_user')
        cache_key = 'landing_demo_networth_breakdown'
        self.assertIsNone(cache.get(cache_key))

        # First request populates cache
        response1 = self.client.get(reverse('landing'))
        cached_data = cache.get(cache_key)
        self.assertIsNotNone(cached_data)
        self.assertEqual(response1.context['demo_networth_rows'], cached_data)

        # Second request uses cache directly
        response2 = self.client.get(reverse('landing'))
        self.assertEqual(response2.context['demo_networth_rows'], cached_data)

    def test_landing_page_query_count_with_cache(self):
        """Landing page query count remains low and constant when demo breakdown is cached."""
        call_command('setup_demo_user')
        # Warm cache
        self.client.get(reverse('landing'))

        # Cold landing page check query count
        with self.assertNumQueries(3):  # 2 SubscriptionPlan queries + 1 User count query
            self.client.get(reverse('landing'))
