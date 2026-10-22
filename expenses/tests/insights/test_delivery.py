"""The bell and push, and the two commands that generate and deliver."""
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone

from expenses.insights import engine, prefs, sampling
from expenses.insights.adapters import bell
from expenses.insights.types import Module, Surface
from expenses.models import InsightState, Notification, UserProfile

from .base import InsightBase, temp_rule
from .test_engine import StoredBase, insight

DAY = timezone.make_aware(datetime(2026, 10, 22, 10, 0))
NIGHT = timezone.make_aware(datetime(2026, 10, 22, 22, 30))
BOTH = override_settings(INSIGHTS_CHANNELS=frozenset({'bell', 'push'}))


class DeliveryBase(StoredBase):
    def rule(self, rule_id, module=Module.BUDGETS, push=False):
        surfaces = {Surface.DASHBOARD, Surface.BELL} | ({Surface.PUSH} if push else set())
        temp_rule(self, id=rule_id, module=module, surfaces=surfaces, push=push,
                  sample=sampling.default_sample)(lambda ctx: [])
        return surfaces

    def bell_row(self, rule_id, priority=50, key='k', push=False, sensitive=False):
        surfaces = self.rule(rule_id, push=push)
        if sensitive:
            surfaces = surfaces - {Surface.PUSH}
        ins = insight(rule_id=rule_id, module=Module.BUDGETS, priority=priority, key=key, surfaces=surfaces,
                      sensitive=sensitive)
        return self.store(rule_id, ins)

    def profile_(self):
        return UserProfile.objects.get(pk=self.profile.pk)


@BOTH
class BellTests(DeliveryBase):
    def deliver(self, now=DAY):
        return bell.deliver_bell(self.user, self.profile_(), now)

    def test_the_best_insight_becomes_one_bell_notification(self):
        self.bell_row('budgets.low', priority=20)
        row = self.bell_row('budgets.high', priority=80)
        self.assertEqual(self.deliver(), 1)
        note = Notification.objects.get(user=self.user)
        self.assertEqual(note.notification_type, 'INSIGHT')
        self.assertEqual(note.slug, 'insight-budgets.high-k')
        self.assertEqual(note.link, f'/insights/{row.uuid}/go/')
        self.assertEqual(note.title, 'A calm title')
        self.assertIsNotNone(InsightState.objects.get(pk=row.pk).bell_at)

    def test_at_most_one_new_insight_a_day(self):
        self.bell_row('budgets.a')
        self.bell_row('budgets.b')
        self.assertEqual(self.deliver(), 1)
        self.assertEqual(self.deliver(DAY + timedelta(hours=5)), 0)
        self.assertEqual(self.deliver(DAY + timedelta(days=1, hours=1)), 1)
        self.assertEqual(Notification.objects.filter(user=self.user, notification_type='INSIGHT').count(), 2)

    def test_quiet_means_one_a_week_and_off_means_never(self):
        self.bell_row('budgets.a')
        self.bell_row('budgets.b')
        prefs.save(self.profile, enabled=True, modules_off=[], notify='quiet', push=True)
        self.assertEqual(self.deliver(), 1)
        self.assertEqual(self.deliver(DAY + timedelta(days=3)), 0)
        prefs.save(self.profile, enabled=True, modules_off=[], notify='off', push=True)
        self.assertEqual(self.deliver(DAY + timedelta(days=30)), 0)

    def test_running_twice_never_sends_the_same_row_twice(self):
        self.bell_row('budgets.a')
        self.deliver()
        self.deliver(DAY + timedelta(days=2))
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    def test_a_dismissed_insight_is_not_delivered(self):
        row = self.bell_row('budgets.a')
        engine.dismiss(self.user, row.uuid)
        self.assertEqual(self.deliver(), 0)

    @override_settings(INSIGHTS_CHANNELS=frozenset())
    def test_nothing_is_sent_while_the_channel_is_off(self):
        self.bell_row('budgets.a')
        self.assertEqual(self.deliver(), 0)

    def test_the_existing_alerts_are_untouched_and_not_counted(self):
        Notification.objects.create(user=self.user, title='Budget Exceeded: Dining', message='x', slug='budget-exceeded-1-2026-10')
        self.bell_row('budgets.a')
        self.assertEqual(self.deliver(), 1)


@BOTH
class PushTests(DeliveryBase):
    def push(self, now=DAY, has_push=True):
        with mock.patch('webpush.send_user_notification') as send:
            sent = bell.deliver_push(self.user, self.profile_(), now, has_push=has_push)
        return sent, send

    def test_a_time_sensitive_insight_is_pushed(self):
        row = self.bell_row('budgets.runout', push=True)
        sent, send = self.push()
        self.assertEqual(sent, 1)
        payload = send.call_args.kwargs['payload']
        # a lock screen gets no names or amounts: a generic line that names the part of the app
        self.assertEqual(payload['head'], 'TrackMyRupee insight')
        self.assertNotIn('A calm title', payload['body'])
        self.assertIn('worth a look', payload['body'])
        self.assertTrue(payload['url'].endswith(f'/insights/{row.uuid}/go/'))
        self.assertIsNotNone(InsightState.objects.get(pk=row.pk).push_at)

    def test_only_rules_marked_time_sensitive_are_pushed(self):
        self.bell_row('budgets.calm', push=False)
        self.assertEqual(self.push()[0], 0)

    def test_never_a_sensitive_insight(self):
        self.bell_row('budgets.sensitive', push=False, sensitive=True)
        self.assertEqual(self.push()[0], 0)

    def test_at_most_two_a_week(self):
        for name in 'abc':
            self.bell_row(f'budgets.{name}', push=True)
        self.assertEqual(self.push(DAY)[0], 1)
        self.assertEqual(self.push(DAY + timedelta(days=1))[0], 1)
        self.assertEqual(self.push(DAY + timedelta(days=2))[0], 0)
        self.assertEqual(self.push(DAY + timedelta(days=8))[0], 1)

    def test_never_at_night(self):
        self.bell_row('budgets.runout', push=True)
        self.assertEqual(self.push(NIGHT)[0], 0)
        self.assertEqual(self.push(DAY.replace(hour=7, minute=30))[0], 0)
        self.assertEqual(self.push(DAY.replace(hour=8, minute=0))[0], 1)

    def test_only_for_people_who_allow_it_and_have_a_device(self):
        self.bell_row('budgets.runout', push=True)
        self.assertEqual(self.push(has_push=False)[0], 0)
        prefs.save(self.profile, enabled=True, modules_off=[], notify='normal', push=False)
        self.assertEqual(self.push()[0], 0)
        prefs.save(self.profile, enabled=True, modules_off=[], notify='quiet', push=True)
        self.assertEqual(self.push()[0], 0)

    def test_a_failed_push_is_released_so_a_later_run_can_try_again(self):
        row = self.bell_row('budgets.runout', push=True)
        with mock.patch('webpush.send_user_notification', side_effect=RuntimeError('gone')):
            self.assertEqual(bell.deliver_push(self.user, self.profile_(), DAY, has_push=True), 0)
        self.assertIsNone(InsightState.objects.get(pk=row.pk).push_at)

    @override_settings(INSIGHTS_CHANNELS=frozenset({'bell'}))
    def test_push_stays_off_until_its_channel_is_on(self):
        self.bell_row('budgets.runout', push=True)
        self.assertEqual(self.push()[0], 0)


class CommandTests(InsightBase):
    def run_cmd(self, name, *args):
        out = StringIO()
        call_command(name, *args, stdout=out)
        return out.getvalue()

    def active_user(self, name, **profile):
        user = User.objects.create_user(name, password='pw')
        user.last_login = timezone.now()
        user.save()
        UserProfile.objects.filter(user=user).update(**profile)
        return user

    @override_settings(INSIGHTS_ENGINE_MODE='on')
    def test_generate_is_idempotent_and_reports_counts_only(self):
        self.life(today=timezone.localdate())
        User.objects.filter(pk=self.user.pk).update(last_login=timezone.now())
        first = self.run_cmd('generate_insights')
        count = InsightState.objects.count()
        self.assertEqual(self.run_cmd('generate_insights').split()[:3], first.split()[:3])
        self.assertEqual(InsightState.objects.count(), count)
        for secret in ('₹', 'Dining', 'Rent'):
            self.assertNotIn(secret, first)

    @override_settings(INSIGHTS_ENGINE_MODE='on')
    def test_generate_skips_people_who_have_not_signed_in_lately(self):
        User.objects.filter(pk=self.user.pk).update(last_login=timezone.now() - timedelta(days=200))
        self.assertIn('users=0', self.run_cmd('generate_insights'))
        self.assertIn('users=1', self.run_cmd('generate_insights', '--user-id', str(self.user.pk)))

    @override_settings(INSIGHTS_ENGINE_MODE='off', INSIGHTS_ENGINE_USER_IDS=frozenset())
    def test_generate_does_nothing_for_everyone_while_the_engine_is_off(self):
        User.objects.filter(pk=self.user.pk).update(last_login=timezone.now())
        self.assertIn('users=0', self.run_cmd('generate_insights'))
        with override_settings(INSIGHTS_ENGINE_USER_IDS=frozenset({self.user.pk})):
            self.assertIn('users=1', self.run_cmd('generate_insights'))

    @override_settings(INSIGHTS_ENGINE_MODE='on')
    def test_one_persons_failure_never_stops_the_batch(self):
        self.active_user('second')
        User.objects.filter(pk=self.user.pk).update(last_login=timezone.now())
        real = engine.refresh

        def flaky(user, **kw):
            if user.pk == self.user.pk:
                raise ValueError('Dining ₹1')
            return real(user, **kw)
        with mock.patch('expenses.management.commands.generate_insights.engine.refresh', side_effect=flaky):
            with self.assertLogs('expenses.management.commands.generate_insights', level='WARNING') as logs:
                out = self.run_cmd('generate_insights')
        self.assertIn('users=1', out)
        self.assertIn('failed=1', out)
        self.assertNotIn('Dining', '\n'.join(logs.output))

    def test_deliver_says_so_when_every_channel_is_off(self):
        self.assertIn('off', self.run_cmd('deliver_insights'))

    @BOTH
    def test_deliver_batches_and_is_safe_to_rerun(self):
        temp_rule(self, id='budgets.cmd', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.BELL},
                  sample=sampling.default_sample)(lambda ctx: [])
        ins = insight(rule_id='budgets.cmd', module=Module.BUDGETS, surfaces={Surface.DASHBOARD, Surface.BELL})
        for name in ('a', 'b'):
            user = self.active_user(name)
            InsightState.objects.create(user=user, rule_id='budgets.cmd', dedupe_key='k', payload=ins.to_payload(),
                                        priority=50, valid_until=timezone.localdate() + timedelta(days=5))
        first = self.run_cmd('deliver_insights', '--batch-size', '1')
        self.assertIn('bell=2', first)
        self.assertIn('bell=0', self.run_cmd('deliver_insights'))
        self.assertEqual(Notification.objects.filter(notification_type='INSIGHT').count(), 2)
