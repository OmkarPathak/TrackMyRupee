from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Category


class AnalyticsForecastCleanupTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.profile = self.user.profile
        self.profile.tier = 'PRO'
        self.profile.is_lifetime = True
        self.profile.save()
        self.client.force_login(self.user)
        self.category, _ = Category.objects.get_or_create(user=self.user, name='Food')

    def test_forecast_and_dead_analytics_context_keys_removed(self):
        """
        Verify that deprecated forecast keys (forecast_income, forecast_expenses, forecast_labels)
        and dead analytics context keys (analytics_capital_annotations, include_capital_events, total_balance_ytd)
        have been removed from the AnalyticsView context.
        """
        response = self.client.get(reverse('analytics'))
        self.assertEqual(response.status_code, 200)

        # Forecast keys removed
        self.assertNotIn('forecast_income', response.context)
        self.assertNotIn('forecast_expenses', response.context)
        self.assertNotIn('forecast_labels', response.context)

        # Dead analytics context keys removed
        self.assertNotIn('analytics_capital_annotations', response.context)
        self.assertNotIn('include_capital_events', response.context)
        self.assertNotIn('total_balance_ytd', response.context)
