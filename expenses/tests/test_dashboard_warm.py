"""After a save the Dashboard is rebuilt in the background, so the page the person lands on is served from the cache."""
import sys
import threading
import time
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone, translation

from expenses import background, dashboard_warm
from expenses.models import Account, Expense


class WarmBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('warm', 'warm@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('1000'))
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('10'), description='x',
                               category='Food', account=self.bank)
        cache.clear()
        self.client.force_login(self.user)


@override_settings(DASHBOARD_WARM_ENABLED=True)
class ScheduleTests(WarmBase):
    def test_nothing_happens_when_switched_off(self):
        with override_settings(DASHBOARD_WARM_ENABLED=False), mock.patch.object(dashboard_warm, 'run_after_response') as run:
            with self.captureOnCommitCallbacks(execute=True):
                dashboard_warm.schedule_dashboard_warm(self.user.pk)
        run.assert_not_called()
        self.assertIsNone(cache.get(dashboard_warm.warm_flag_key(self.user.pk)))

    def test_runs_after_the_commit_with_the_callers_timezone_and_language(self):
        with mock.patch.object(dashboard_warm, 'run_after_response') as run:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                dashboard_warm.schedule_dashboard_warm(self.user.pk)
            run.assert_not_called()                      # not before the save is committed
            for callback in callbacks:
                callback()
        args = run.call_args.args
        self.assertEqual((args[1], args[2], args[3]), (self.user.pk, timezone.get_current_timezone_name(), translation.get_language()))
        self.assertEqual(run.call_args.kwargs['if_busy'], 'skip')

    def test_one_rebuild_at_a_time(self):
        with mock.patch.object(dashboard_warm, 'run_after_response') as run:
            with self.captureOnCommitCallbacks(execute=True):
                dashboard_warm.schedule_dashboard_warm(self.user.pk)
                dashboard_warm.schedule_dashboard_warm(self.user.pk)
        self.assertEqual(run.call_count, 1)

    def test_when_every_thread_is_busy_the_job_is_skipped_and_the_flag_cleared(self):
        taken = [background._slots.acquire(blocking=False) for _ in range(4)]
        try:
            with override_settings(BACKGROUND_TASKS_ENABLED=True), mock.patch.object(dashboard_warm, '_warm') as warm:
                with self.captureOnCommitCallbacks(execute=True):
                    dashboard_warm.schedule_dashboard_warm(self.user.pk)
            warm.assert_not_called()                     # a rebuild only saves time: never run inside the request
            self.assertIsNone(cache.get(dashboard_warm.warm_flag_key(self.user.pk)))
        finally:
            for ok in taken:
                if ok:
                    background._slots.release()


class RebuildTests(WarmBase):
    def test_the_rebuild_renders_the_dashboard_as_that_person_and_clears_its_flag(self):
        cache.set(dashboard_warm.warm_flag_key(self.user.pk), 1, 60)
        with mock.patch('expenses.views.dashboard.home_view') as home:
            dashboard_warm._warm(self.user.pk, 'Asia/Kolkata', 'hi')
        request = home.call_args.args[0]
        self.assertEqual(request.user.pk, self.user.pk)
        self.assertTrue(request.is_dashboard_warm)
        self.assertEqual(request.LANGUAGE_CODE, 'hi')
        self.assertIsNone(cache.get(dashboard_warm.warm_flag_key(self.user.pk)))

    def test_a_failing_rebuild_is_quiet_and_still_clears_its_flag(self):
        cache.set(dashboard_warm.warm_flag_key(self.user.pk), 1, 60)
        with mock.patch('expenses.views.dashboard.home_view', side_effect=RuntimeError('boom')):
            dashboard_warm._warm(self.user.pk, 'UTC', 'en')
        self.assertIsNone(cache.get(dashboard_warm.warm_flag_key(self.user.pk)))

    def test_the_real_dashboard_builds_for_the_rebuild(self):
        dashboard_warm._warm(self.user.pk, 'Asia/Kolkata', 'en')     # no exception, whatever the request lacks


class VersionAndWaitTests(WarmBase):
    def test_every_save_bumps_the_version(self):
        before = dashboard_warm.dashboard_version(self.user.pk)
        Expense.objects.create(user=self.user, date=date.today(), amount=Decimal('5'), description='y',
                               category='Food', account=self.bank)
        self.assertGreater(dashboard_warm.dashboard_version(self.user.pk), before)

    def test_waiting_costs_nothing_without_a_rebuild(self):
        started = time.monotonic()
        self.assertFalse(dashboard_warm.wait_for_warm(self.user.pk))
        self.assertLess(time.monotonic() - started, 0.05)

    def test_a_page_waits_for_a_rebuild_that_is_under_way(self):
        key = dashboard_warm.warm_flag_key(self.user.pk)
        cache.set(key, 1, 60)
        threading.Timer(0.25, lambda: cache.delete(key)).start()
        started = time.monotonic()
        self.assertTrue(dashboard_warm.wait_for_warm(self.user.pk))
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 1.5)

    def test_it_gives_up_waiting_after_a_couple_of_seconds(self):
        cache.set(dashboard_warm.warm_flag_key(self.user.pk), 1, 60)
        with mock.patch.object(dashboard_warm, 'WAIT_SECONDS', 0.2):
            started = time.monotonic()
            self.assertTrue(dashboard_warm.wait_for_warm(self.user.pk))
        self.assertLess(time.monotonic() - started, 1)


class CacheOnlyWhenNothingWasSavedTests(WarmBase):
    """The dashboard keeps its build only when no save happened while it was being built."""

    def build(self, versions):
        with mock.patch.object(sys, 'argv', ['manage.py', 'runserver']), override_settings(TESTING=False), \
                mock.patch('expenses.views.dashboard.dashboard_version', side_effect=versions):
            self.assertEqual(self.client.get('/dashboard/').status_code, 200)

    def test_kept_when_the_version_did_not_move(self):
        self.build([1, 1, 1, 1, 1])
        self.assertIsNotNone(cache.get(f'home_default_data_{self.user.pk}'))

    def test_dropped_when_a_save_landed_during_the_build(self):
        self.build([1, 2, 2, 2, 2])
        self.assertIsNone(cache.get(f'home_default_data_{self.user.pk}'))
