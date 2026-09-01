import io
import time
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.cookie import CookieStorage
from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from expenses.models import Announcement, EmailLog, UserProfile
from expenses.views.notifications import (
    _dispatch_cron_command,
    trigger_announcements,
    trigger_daily_reminders_view,
    trigger_lifecycle_emails,
    trigger_monthly_reports_view,
    trigger_notifications,
)
from expenses.context_processors import active_announcement


class CronConcurrencyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()

    def test_dispatch_cron_command_locked(self):
        lock_key = 'cron_lock_send_notifications'
        cache.set(lock_key, 1, timeout=60)

        with patch('expenses.views.notifications.call_command') as mock_call:
            response = _dispatch_cron_command('send_notifications')
            self.assertEqual(response.status_code, 409)
            mock_call.assert_not_called()

    def test_dispatch_cron_command_success_and_unlock(self):
        with patch('expenses.views.notifications.call_command') as mock_call:
            response = _dispatch_cron_command('send_notifications', lock_timeout=60)
            self.assertEqual(response.status_code, 200)

            # Wait briefly for thread to finish
            for _ in range(20):
                if cache.get('cron_lock_send_notifications') is None:
                    break
                time.sleep(0.05)

            self.assertIsNone(cache.get('cron_lock_send_notifications'))

    def test_trigger_views_unauthorized(self):
        cron_views = [
            trigger_notifications,
            trigger_lifecycle_emails,
            trigger_monthly_reports_view,
            trigger_daily_reminders_view,
            trigger_announcements,
        ]
        for view in cron_views:
            req = self.factory.post('/api/cron/')
            resp = view(req)
            self.assertEqual(resp.status_code, 403)


class DailyRemindersOptimizationTest(TestCase):
    def setUp(self):
        # Create multiple user profiles
        for i in range(5):
            u = User.objects.create_user(username=f'user{i}', email=f'user{i}@example.com', password='password')
            u.profile.daily_reminder = True
            u.profile.save()

    def test_push_check_query_count(self):
        from expenses.management.commands.send_daily_reminders import Command
        cmd = Command()
        
        # Run command under query check
        # With pre-fetching subscribed_user_ids, query count for PushInformation is exactly 1 regardless of profile count
        with patch('expenses.management.commands.send_daily_reminders.send_user_notification'):
            with self.assertNumQueries(2):  # 1 for UserProfile+User, 1 for PushInformation values_list
                cmd.handle()


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MonthlyReportDeduplicationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testreport', email='report@example.com', password='password')
        try:
            from allauth.account.models import EmailAddress
            EmailAddress.objects.update_or_create(user=self.user, defaults={'email': self.user.email, 'verified': True})
        except Exception:
            pass

        # Create an account with balance and income/expense in previous month
        today = timezone.now().date()
        first_day_curr_month = today.replace(day=1)
        prev_month_date = first_day_curr_month - timedelta(days=5)

        from expenses.models import Account, Expense, Income
        acc = Account.objects.create(user=self.user, name='Bank', balance=1000, currency='₹')
        Income.objects.create(user=self.user, account=acc, amount=500, base_amount=500, date=prev_month_date, source_type='Salary', currency='₹', exchange_rate=1.0)
        Expense.objects.create(user=self.user, account=acc, amount=100, base_amount=100, date=prev_month_date, category='Food', currency='₹', exchange_rate=1.0)

    def test_monthly_report_does_not_double_send(self):
        from expenses.management.commands.send_monthly_report import Command
        cmd = Command()

        mail.outbox.clear()
        cmd.handle(user_id=self.user.id)
        self.assertEqual(len(mail.outbox), 1)

        # Run command second time for same user and month
        mail.outbox.clear()
        cmd.handle(user_id=self.user.id)
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FeatureAnnouncementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='password')
        self.free_user = User.objects.create_user(username='freeuser', email='free@example.com', password='password')
        self.free_user.profile.tier = 'FREE'
        self.free_user.profile.save()

        self.plus_user = User.objects.create_user(username='plususer', email='plus@example.com', password='password')
        self.plus_user.profile.tier = 'PLUS'
        self.plus_user.profile.is_lifetime = True
        self.plus_user.profile.save()

    def test_file_extension_validation(self):
        invalid_exe = SimpleUploadedFile("malicious.exe", b"binary content")
        ann = Announcement(title="Test", body="Body", image=invalid_exe)
        with self.assertRaises(ValidationError):
            ann.clean()

        invalid_pdf = SimpleUploadedFile("document.pdf", b"pdf content")
        ann_pdf = Announcement(title="Test PDF", body="Body", image=invalid_pdf)
        with self.assertRaises(ValidationError):
            ann_pdf.clean()

        valid_gif = SimpleUploadedFile("animated.gif", b"GIF89a...", content_type="image/gif")
        ann_gif = Announcement(title="Test GIF", body="Body", image=valid_gif)
        # Clean should not raise
        ann_gif.clean()

    def test_send_announcements_audience_resolution_and_status(self):
        ann = Announcement.objects.create(
            title="Paid Upgrade!",
            body="New features for paid members.",
            audience='PAID',
            send_email=True,
            status='QUEUED'
        )

        from expenses.management.commands.send_announcements import Command
        cmd = Command()
        mail.outbox.clear()
        cmd.handle()

        ann.refresh_from_db()
        self.assertEqual(ann.status, 'SENT')
        self.assertIsNotNone(ann.sent_at)

        # Email should only be sent to plus_user (PAID tier), not free_user
        recipients = [m.to[0] for m in mail.outbox]
        self.assertIn(self.plus_user.email, recipients)
        self.assertNotIn(self.free_user.email, recipients)

    def test_send_test_to_self_admin_action(self):
        ann = Announcement.objects.create(
            title="Test Announcement",
            body="Admin test body",
            audience='FREE',  # Configured for FREE audience
            send_email=True,
            status='DRAFT'
        )

        from expenses.admin import AnnouncementAdmin
        admin_obj = AnnouncementAdmin(Announcement, None)

        rf = RequestFactory()
        request = rf.post('/admin/expenses/announcement/')
        request.user = self.admin  # Admin running the action
        setattr(request, '_messages', CookieStorage(request))

        mail.outbox.clear()
        admin_obj.send_test_to_self(request, Announcement.objects.filter(id=ann.id))

        # Must send strictly to request.user (the admin), ignoring the announcement's audience setting ('FREE')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to[0], self.admin.email)

    def test_active_announcement_context_processor(self):
        ann = Announcement.objects.create(
            title="Modal Announcement",
            body="Welcome modal!",
            show_modal=True,
            status='SENT'
        )

        rf = RequestFactory()
        req = rf.get('/')
        req.user = self.free_user

        ctx = active_announcement(req)
        self.assertEqual(ctx['active_announcement'], ann)

        # Expired announcement should not return
        ann.expires_at = timezone.now() - timedelta(days=1)
        ann.save()

        ctx_expired = active_announcement(req)
        self.assertIsNone(ctx_expired['active_announcement'])
