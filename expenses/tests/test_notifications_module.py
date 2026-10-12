"""Notifications: the list and read-marking, the navbar badge, and the commands that create them."""

import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    Category,
    Expense,
    GoalContribution,
    Notification,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)

D = Decimal


def today():
    return timezone.now().date()


class NotifBase(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = self.make_user('notif-user', self.tier)
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('100000'), currency='₹')
        cache.clear()

    def make_user(self, name, tier='PRO', email=None):
        user = User.objects.create_user(username=name, password='pass', email=email or f'{name}@example.com')
        profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'currency': '₹'})
        profile.tier = tier
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        user.refresh_from_db()
        return user

    def notify(self, user=None, **kw):
        values = dict(user=user or self.user, title='Hello', message='World', notification_type='SYSTEM')
        values.update(kw)
        return Notification.objects.create(**values)

    def run_command(self, **kw):
        out = StringIO()
        with patch('expenses.management.commands.send_notifications.send_user_notification') as push:
            call_command('send_notifications', stdout=out, **kw)
        self.push = push
        return out.getvalue()


class TestNotificationList(NotifBase):
    def test_it_lists_my_notifications_newest_first_twenty_a_page(self):
        for i in range(25):
            self.notify(title=f'n{i}')
        response = self.client.get(reverse('notification-list'))
        self.assertEqual(len(response.context['notifications']), 20)
        self.assertEqual(response.context['notifications'][0].title, 'n24')
        self.assertEqual(len(self.client.get(reverse('notification-list'), {'page': 2}).context['notifications']), 5)

    def test_other_users_notifications_never_show(self):
        other = self.make_user('someone-else')
        self.notify(other, title='secret')
        self.assertNotContains(self.client.get(reverse('notification-list')), 'secret')

    def test_the_unread_count_is_the_real_count(self):
        for _ in range(12):
            self.notify()
        self.notify(is_read=True)
        self.assertEqual(self.client.get(reverse('notification-list')).context['unread_count'], 12)

    def test_login_is_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('notification-list')).status_code, 302)

    def test_html_in_a_message_is_escaped(self):
        self.notify(title='<script>alert(1)</script>', message='<b>x</b>')
        html = self.client.get(reverse('notification-list')).content.decode()
        self.assertNotIn('<script>alert(1)</script>', html)


class TestReadMarking(NotifBase):
    def test_mark_all_read_only_touches_my_notifications(self):
        other = self.make_user('someone-else')
        mine = [self.notify() for _ in range(3)]
        theirs = self.notify(other)
        response = self.client.post(reverse('mark-all-read'))
        self.assertRedirects(response, reverse('notification-list'))
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 0)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)
        self.assertEqual(len(mine), 3)

    def test_mark_all_read_ignores_a_get(self):
        self.notify()
        self.client.get(reverse('mark-all-read'))
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 1)

    def test_marking_one_read_by_pk_and_by_uuid(self):
        a, b = self.notify(), self.notify()
        self.assertTrue(self.client.post(reverse('mark-single-notification-read', args=[a.pk])).json()['success'])
        self.assertTrue(self.client.post(reverse('mark-single-notification-read', args=[b.uuid])).json()['success'])
        self.assertEqual(Notification.objects.filter(is_read=False).count(), 0)

    def test_someone_elses_notification_is_not_found(self):
        other = self.make_user('someone-else')
        theirs = self.notify(other)
        response = self.client.post(reverse('mark-single-notification-read', args=[theirs.pk]))
        self.assertEqual(response.status_code, 404)
        theirs.refresh_from_db()
        self.assertFalse(theirs.is_read)

    def test_a_get_does_not_change_anything(self):
        n = self.notify()
        self.client.get(reverse('mark-single-notification-read', args=[n.pk]))
        n.refresh_from_db()
        self.assertFalse(n.is_read)

    def test_the_redirect_marks_it_read_and_goes_to_its_link(self):
        n = self.notify(link='/goals/')
        response = self.client.get(reverse('notification-redirect', args=[n.pk]))
        self.assertRedirects(response, '/goals/', fetch_redirect_response=False)
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_a_link_to_another_site_is_not_followed(self):
        n = self.notify(link='https://evil.example/phish')
        response = self.client.get(reverse('notification-redirect', args=[n.pk]))
        self.assertNotIn('evil.example', response['Location'])

    def test_a_notification_without_a_link_goes_back_to_the_list(self):
        n = self.notify(link=None)
        response = self.client.get(reverse('notification-redirect', args=[n.pk]))
        self.assertRedirects(response, reverse('notification-list'), fetch_redirect_response=False)

    def test_an_unknown_notification_redirects_with_a_message(self):
        response = self.client.get(reverse('notification-redirect', args=[999999]))
        self.assertRedirects(response, reverse('notification-list'), fetch_redirect_response=False)

    def test_marking_read_clears_the_cached_navbar_badge(self):
        from expenses.context_processors import global_badge_data_cache_key
        self.notify()
        self.client.get(reverse('account-list'))
        self.assertTrue(cache.get(global_badge_data_cache_key(self.user.id)))
        self.client.post(reverse('mark-all-read'))
        self.assertIsNone(cache.get(global_badge_data_cache_key(self.user.id)))


class TestNavbarBadge(NotifBase):
    def badge(self):
        cache.clear()
        return self.client.get(reverse('account-list')).context['unread_notifications_count']

    def test_it_counts_unread_notifications(self):
        for _ in range(4):
            self.notify()
        self.notify(is_read=True)
        self.assertEqual(self.badge(), 4)

    def test_it_is_the_real_count_even_beyond_the_nine_shown_in_the_menu(self):
        for _ in range(14):
            self.notify()
        context = self.client.get(reverse('account-list')).context
        self.assertEqual(context['unread_notifications_count'], 14)
        self.assertEqual(len(context['notifications']), 9)

    def test_a_large_count_reads_nine_plus(self):
        for _ in range(14):
            self.notify()
        html = self.client.get(reverse('account-list')).content.decode()
        self.assertIn('9+', html)


class TestMilestoneAlerts(NotifBase):
    def goal(self, current, target='1000', name='Trip'):
        return SavingsGoal.objects.create(user=self.user, name=name, target_amount=D(target), currency='₹',
                                          current_amount=D(str(current)))

    def milestones(self):
        return list(Notification.objects.filter(user=self.user, notification_type='MILESTONE'))

    def test_halfway_ninety_and_complete_each_fire_once_at_the_highest_level_reached(self):
        goal = self.goal(500)
        self.run_command()
        self.assertEqual([n.slug for n in self.milestones()], [f'milestone-{goal.id}-50'])
        SavingsGoal.objects.filter(pk=goal.pk).update(current_amount=D('950'))
        self.run_command()
        self.assertIn(f'milestone-{goal.id}-90', [n.slug for n in self.milestones()])

    def test_a_goal_that_has_been_reached_says_so(self):
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('1000'), currency='₹')
        GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('1000'), date=today())
        goal.refresh_from_db()
        self.assertTrue(goal.is_completed)
        self.run_command()
        self.assertEqual([n.slug for n in self.milestones()], [f'milestone-{goal.id}-100'])
        self.assertIn('Goal Completed', self.milestones()[0].message)

    def test_the_same_milestone_is_never_sent_twice_even_months_later(self):
        goal = self.goal(500)
        self.run_command()
        Notification.objects.filter(user=self.user).update(created_at=timezone.now() - datetime.timedelta(days=40))
        self.run_command()
        self.assertEqual(Notification.objects.filter(slug=f'milestone-{goal.id}-50').count(), 1)

    def test_a_goal_below_half_sends_nothing(self):
        self.goal(100)
        self.run_command()
        self.assertEqual(self.milestones(), [])

    def test_the_plan_limit_only_notifies_for_open_goals(self):
        user = self.make_user('free-user', 'FREE')
        first = SavingsGoal.objects.create(user=user, name='A', target_amount=D('100'), current_amount=D('60'), currency='₹')
        SavingsGoal.objects.create(user=user, name='B', target_amount=D('100'), current_amount=D('60'), currency='₹')
        self.run_command()
        slugs = [n.slug for n in Notification.objects.filter(user=user)]
        self.assertEqual(slugs, [f'milestone-{first.id}-50'])


class TestRecurringAndBillingAlerts(NotifBase):
    def test_a_recurring_item_due_in_three_days_is_announced_once_a_month(self):
        due = today() + datetime.timedelta(days=3)
        rt = RecurringTransaction.objects.create(
            user=self.user, account=self.bank, transaction_type='EXPENSE', amount=D('499'), description='Netflix',
            frequency='MONTHLY', start_date=due, next_due_date=due, currency='₹', is_active=True)
        self.run_command()
        self.run_command()
        notes = Notification.objects.filter(user=self.user, notification_type='RECURRING')
        self.assertEqual(notes.count(), 1)
        self.assertIn('Netflix', notes[0].title)
        self.assertEqual(notes[0].link, '/expenses/')
        self.assertEqual(notes[0].related_transaction_id, rt.id)

    def test_items_due_on_other_days_are_not_announced(self):
        for days in (0, 2, 4, 10):
            due = today() + datetime.timedelta(days=days)
            RecurringTransaction.objects.create(
                user=self.user, account=self.bank, transaction_type='EXPENSE', amount=D('10'),
                description=f'Item{days}', frequency='MONTHLY', start_date=due, next_due_date=due,
                currency='₹', is_active=True)
        self.run_command()
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)

    def test_inactive_schedules_are_skipped(self):
        due = today() + datetime.timedelta(days=3)
        RecurringTransaction.objects.create(
            user=self.user, account=self.bank, transaction_type='EXPENSE', amount=D('10'), description='Paused',
            frequency='MONTHLY', start_date=due, next_due_date=due, currency='₹', is_active=False)
        self.run_command()
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)

    def test_transfers_and_income_link_to_their_own_pages(self):
        due = today() + datetime.timedelta(days=3)
        for kind, link in (('INCOME', '/income/list/'),):
            RecurringTransaction.objects.create(
                user=self.user, account=self.bank, transaction_type=kind, amount=D('10'), description=kind,
                frequency='MONTHLY', start_date=due, next_due_date=due, currency='₹', is_active=True)
            self.run_command()
            self.assertEqual(Notification.objects.get(user=self.user).link, link)

    def test_a_card_billing_in_three_days_is_announced(self):
        billing = today() + datetime.timedelta(days=3)
        Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('-500'),
                               currency='₹', credit_card_billing_day=billing.day)
        self.run_command()
        note = Notification.objects.get(user=self.user)
        self.assertIn('Card', note.title)
        self.assertTrue(note.link.startswith('/accounts/'))


class TestBudgetAlerts(NotifBase):
    def setUp(self):
        super().setUp()
        self.cat, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Category.objects.filter(pk=self.cat.pk).update(limit=D('1000'))

    def spend(self, amount):
        Expense.objects.create(user=self.user, account=self.bank, amount=D(str(amount)), category='Food',
                               date=today(), currency='₹')

    def analytics_notes(self):
        return list(Notification.objects.filter(user=self.user, notification_type='ANALYTICS'))

    def test_nothing_below_the_warning_level(self):
        self.spend(100)
        self.run_command()
        self.assertEqual(self.analytics_notes(), [])

    def test_a_warning_then_an_exceeded_alert_each_once_a_month(self):
        self.spend(900)
        self.run_command()
        self.run_command()
        self.assertEqual([n.title for n in self.analytics_notes()], ['Budget Alert: Food'])
        self.spend(300)
        self.run_command()
        self.run_command()
        self.assertEqual(sorted(n.title for n in self.analytics_notes()), ['Budget Alert: Food', 'Budget Exceeded: Food'])

    def test_the_alert_names_the_spend_and_the_limit(self):
        self.spend(1200)
        self.run_command()
        self.assertIn('₹1200', self.analytics_notes()[0].message)
        self.assertIn('₹1000', self.analytics_notes()[0].message)


class TestSubscriptionReminder(NotifBase):
    def test_two_days_before_expiry_a_reminder_is_sent_once(self):
        end = timezone.now() + datetime.timedelta(days=2)
        UserProfile.objects.filter(user=self.user).update(tier='PLUS', subscription_end_date=end, is_lifetime=False)
        self.run_command()
        self.run_command()
        notes = Notification.objects.filter(user=self.user, notification_type='SYSTEM')
        self.assertEqual(notes.count(), 1)
        self.assertEqual(notes[0].link, '/pricing/')
        self.assertTrue(UserProfile.objects.get(user=self.user).expiry_reminder_sent)

    def test_lifetime_and_free_users_get_none(self):
        end = timezone.now() + datetime.timedelta(days=2)
        UserProfile.objects.filter(user=self.user).update(tier='PRO', subscription_end_date=end, is_lifetime=True)
        self.run_command()
        UserProfile.objects.filter(user=self.user).update(tier='FREE', is_lifetime=False)
        self.run_command()
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 0)


class TestDeliveryChannels(NotifBase):
    def setUp(self):
        super().setUp()
        due = today() + datetime.timedelta(days=3)
        RecurringTransaction.objects.create(
            user=self.user, account=self.bank, transaction_type='EXPENSE', amount=D('10'), description='Rent',
            frequency='MONTHLY', start_date=due, next_due_date=due, currency='₹', is_active=True)

    def subscribe(self, user=None):
        from webpush.models import PushInformation, SubscriptionInfo
        info = SubscriptionInfo.objects.create(browser='chrome', endpoint='https://push.example/abc',
                                               auth='a', p256dh='p')
        PushInformation.objects.create(user=user or self.user, subscription=info)

    def test_a_subscribed_user_gets_a_push_with_an_absolute_url(self):
        self.subscribe()
        self.run_command()
        self.assertEqual(self.push.call_count, 1)
        payload = self.push.call_args.kwargs['payload']
        self.assertTrue(payload['url'].startswith('http'))
        self.assertIn('Rent', payload['head'])

    def test_an_unsubscribed_user_gets_no_push(self):
        self.run_command()
        self.assertEqual(self.push.call_count, 0)

    def test_a_push_failure_does_not_stop_the_run(self):
        self.subscribe()
        out = StringIO()
        with patch('expenses.management.commands.send_notifications.send_user_notification', side_effect=RuntimeError('gone')):
            call_command('send_notifications', stdout=out)
        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_paid_plans_get_one_digest_email_and_free_plans_none(self):
        mail.outbox.clear()
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Rent', mail.outbox[0].subject)
        mail.outbox.clear()
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        Notification.objects.all().delete()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_several_alerts_share_one_digest(self):
        mail.outbox.clear()
        due = today() + datetime.timedelta(days=3)
        RecurringTransaction.objects.create(
            user=self.user, account=self.bank, transaction_type='EXPENSE', amount=D('10'), description='Gym',
            frequency='MONTHLY', start_date=due, next_due_date=due, currency='₹', is_active=True)
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'Your Daily Financial Digest')

    def test_the_demo_user_is_skipped(self):
        demo = self.make_user('demo', 'PRO')
        due = today() + datetime.timedelta(days=3)
        RecurringTransaction.objects.create(
            user=demo, transaction_type='EXPENSE', amount=D('10'), description='DemoRent', frequency='MONTHLY',
            start_date=due, next_due_date=due, currency='₹', is_active=True)
        self.run_command()
        self.assertEqual(Notification.objects.filter(user=demo).count(), 0)

    def test_old_notifications_are_cleaned_up_after_ninety_days(self):
        old = self.notify(title='old')
        recent = self.notify(title='recent')
        Notification.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(days=91))
        self.run_command()
        self.assertFalse(Notification.objects.filter(pk=old.pk).exists())
        self.assertTrue(Notification.objects.filter(pk=recent.pk).exists())


class TestDailyReminder(NotifBase):
    def run_reminders(self):
        out = StringIO()
        with patch('expenses.management.commands.send_daily_reminders.send_user_notification') as push:
            call_command('send_daily_reminders', stdout=out)
        return push

    def subscribe(self, user):
        from webpush.models import PushInformation, SubscriptionInfo
        info = SubscriptionInfo.objects.create(browser='chrome', endpoint=f'https://push.example/{user.id}',
                                               auth='a', p256dh='p')
        PushInformation.objects.create(user=user, subscription=info)

    def test_only_subscribed_users_with_the_setting_on_are_pushed(self):
        self.subscribe(self.user)
        quiet = self.make_user('quiet')
        self.subscribe(quiet)
        UserProfile.objects.filter(user=quiet).update(daily_reminder=False)
        self.make_user('unsubscribed')
        push = self.run_reminders()
        self.assertEqual(push.call_count, 1)
        self.assertEqual(push.call_args.kwargs['user'], self.user)

    def test_someone_who_already_logged_an_expense_today_is_not_nagged(self):
        self.subscribe(self.user)
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food',
                               date=today(), currency='₹')
        self.assertEqual(self.run_reminders().call_count, 0)

    def test_an_expense_from_yesterday_does_not_count(self):
        self.subscribe(self.user)
        Expense.objects.create(user=self.user, account=self.bank, amount=D('10'), category='Food',
                               date=today() - datetime.timedelta(days=1), currency='₹')
        self.assertEqual(self.run_reminders().call_count, 1)


class TestReminderSetting(NotifBase):
    def save(self, **extra):
        data = {'auth_email': self.user.email, 'first_name': 'A', 'last_name': 'B', 'salary_date': '1'}
        data.update(extra)
        return self.client.post(reverse('profile-settings'), data)

    def test_the_daily_reminder_is_on_by_default_and_can_be_turned_off_and_on(self):
        self.assertTrue(UserProfile.objects.get(user=self.user).daily_reminder)
        self.assertEqual(self.save().status_code, 302)  # checkbox absent = off
        self.assertFalse(UserProfile.objects.get(user=self.user).daily_reminder)
        self.save(daily_reminder='on')
        self.assertTrue(UserProfile.objects.get(user=self.user).daily_reminder)
