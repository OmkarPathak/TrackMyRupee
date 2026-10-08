from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from expenses.context_processors import global_badge_data, global_badge_data_cache_key
from expenses.models import Notification, SavingsGoal

BADGE_KEYS = [
    'notifications', 'has_unread_notifications', 'unread_notifications_count',
    'active_goals_count', 'upcoming_subscriptions_count', 'calendar_this_week_count',
    'active_loans_count', 'sidebar_accounts', 'sidebar_accounts_count',
    'has_more_accounts', 'is_webpush_subscribed', 'vapid_public_key', 'active_announcement',
]


class GlobalBadgeDataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('badge', 'b@example.com', 'pw')
        self.client.force_login(self.user)

    def _badge_queries(self, ctx):
        # PushInformation / SavingsGoal are only read by global_badge_data on settings-home
        return [q for q in ctx.captured_queries
                if 'webpush_pushinformation' in q['sql'] or 'expenses_savingsgoal' in q['sql']]

    def test_second_settings_render_issues_no_badge_queries(self):
        self.client.get(reverse('settings-home'))  # warm
        cache.delete(global_badge_data_cache_key(self.user.id))
        with CaptureQueriesContext(connection) as cold:
            self.assertEqual(self.client.get(reverse('settings-home')).status_code, 200)
        with CaptureQueriesContext(connection) as warm:
            self.assertEqual(self.client.get(reverse('settings-home')).status_code, 200)
        self.assertTrue(self._badge_queries(cold))
        self.assertFalse(self._badge_queries(warm), [q['sql'] for q in self._badge_queries(warm)])

    def test_all_template_variables_present_and_populated(self):
        SavingsGoal.objects.create(user=self.user, name='G', target_amount=100)
        Notification.objects.create(user=self.user, title='t', message='m')
        rf = self.client.get(reverse('settings-home')).wsgi_request
        ctx = global_badge_data(rf)
        for key in BADGE_KEYS:
            self.assertIn(key, ctx)
        self.assertEqual(ctx['active_goals_count'], 1)
        self.assertEqual(ctx['unread_notifications_count'], 1)
        self.assertTrue(ctx['has_unread_notifications'])
        self.assertFalse(ctx['is_webpush_subscribed'])

    def test_notification_save_invalidates_cache(self):
        rf = self.client.get(reverse('settings-home')).wsgi_request
        self.assertEqual(global_badge_data(rf)['unread_notifications_count'], 0)
        self.assertIsNotNone(cache.get(global_badge_data_cache_key(self.user.id)))
        Notification.objects.create(user=self.user, title='t', message='m')
        self.assertIsNone(cache.get(global_badge_data_cache_key(self.user.id)))
        self.assertEqual(global_badge_data(rf)['unread_notifications_count'], 1)

    def test_mark_all_read_invalidates_cache(self):
        Notification.objects.create(user=self.user, title='t', message='m')
        rf = self.client.get(reverse('settings-home')).wsgi_request
        self.assertEqual(global_badge_data(rf)['unread_notifications_count'], 1)
        self.client.post(reverse('mark-all-read'))
        self.assertEqual(global_badge_data(rf)['unread_notifications_count'], 0)
