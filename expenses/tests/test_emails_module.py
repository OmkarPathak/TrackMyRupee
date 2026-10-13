"""Audit of every email the app sends: delivery bookkeeping, sign-up flow, drips, monthly report,
announcements, product updates and the links inside the templates."""
import re
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import Resolver404, resolve
from django.utils import timezone

from expenses.adapters import CustomAccountAdapter
from expenses.email_backends import LoggedEmailBackend, redact_secret_links
from expenses.models import Account, Announcement, EmailLog, Expense, Income

LOGGED = 'expenses.email_backends.LoggedEmailBackend'


def verified_user(username, **kwargs):
    user = User.objects.create_user(username=username, email=f'{username}@example.com', password='pw', **kwargs)
    EmailAddress.objects.update_or_create(
        user=user, email=user.email, defaults={'primary': True, 'verified': True}
    )
    mail.outbox.clear()
    EmailLog.objects.all().delete()
    return user


@override_settings(EMAIL_BACKEND=LOGGED, DEBUG=False)
class LoggedBackendTests(TestCase):
    def send(self, **kw):
        from django.core.mail import send_mail
        return send_mail('Subject', 'Body', 'from@example.com', ['to@example.com'], **kw)

    def test_failure_is_raised_and_logged_with_the_reason(self):
        with patch.object(LoggedEmailBackend, '_get_real_backend') as real:
            real.return_value.send_messages.side_effect = RuntimeError('brevo down')
            with self.assertRaises(RuntimeError):
                self.send()
        log = EmailLog.objects.get()
        self.assertEqual(log.status, 'FAILED')
        self.assertIn('brevo down', log.error_message)

    def test_fail_silently_returns_zero(self):
        with patch.object(LoggedEmailBackend, '_get_real_backend') as real:
            real.return_value.send_messages.side_effect = RuntimeError('brevo down')
            self.assertEqual(self.send(fail_silently=True), 0)
        self.assertEqual(EmailLog.objects.get().status, 'FAILED')

    def test_success_is_logged_once(self):
        with patch.object(LoggedEmailBackend, '_get_real_backend') as real:
            real.return_value.send_messages.side_effect = lambda msgs: len(msgs)
            self.assertEqual(self.send(), 1)
        self.assertEqual(EmailLog.objects.filter(status='SENT').count(), 1)

    def test_one_time_links_are_not_stored(self):
        text = (
            'Confirm: https://trackmyrupee.com/accounts/confirm-email/abc123/ and '
            'reset https://trackmyrupee.com/accounts/password/reset/key/5-xyz/ but keep '
            'https://trackmyrupee.com/pricing/'
        )
        out = redact_secret_links(text)
        self.assertNotIn('abc123', out)
        self.assertNotIn('5-xyz', out)
        self.assertIn('https://trackmyrupee.com/pricing/', out)
        self.assertEqual(redact_secret_links(None), None)

    def test_stored_confirmation_mail_has_no_live_link(self):
        from django.core.mail import EmailMultiAlternatives
        with patch.object(LoggedEmailBackend, '_get_real_backend') as real:
            real.return_value.send_messages.side_effect = lambda msgs: len(msgs)
            msg = EmailMultiAlternatives('Confirm', 'go https://t.com/accounts/confirm-email/KEY1/', 'a@b.com', ['x@y.com'])
            msg.attach_alternative('<a href="https://t.com/accounts/confirm-email/KEY1/">go</a>', 'text/html')
            msg.send()
        log = EmailLog.objects.get()
        self.assertNotIn('KEY1', log.body)
        self.assertNotIn('KEY1', log.html_body)


@override_settings(EMAIL_BACKEND=LOGGED, DEBUG=False)
class SignupEmailFlowTests(TestCase):
    """The welcome email is logged as SENT a moment before the confirmation email is requested."""

    def _confirm_request(self, user):
        request = RequestFactory().get('/')
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        request.user = user
        return request

    def test_welcome_email_does_not_block_the_confirmation_email(self):
        with patch.object(LoggedEmailBackend, '_get_real_backend') as real:
            real.return_value.send_messages.side_effect = lambda msgs: len(msgs)
            user = User.objects.create_user('newbie', 'newbie@example.com', 'pw')
            address = EmailAddress.objects.get_or_create(
                user=user, email=user.email, defaults={'primary': True, 'verified': False}
            )[0]
            address.send_confirmation(self._confirm_request(user), signup=True)
        subjects = list(EmailLog.objects.values_list('subject', flat=True))
        self.assertTrue(any('Welcome' in s for s in subjects), subjects)
        self.assertTrue(any('Confirm Your Email Address' in s for s in subjects), subjects)

    def test_resend_right_after_signup_is_allowed(self):
        user = User.objects.create_user('resender', 'resender@example.com', 'pw')
        EmailLog.objects.create(
            user=user, to_email=user.email, subject='Welcome to TrackMyRupee! 🎉', body='hi', status='SENT'
        )
        blocked, _ = CustomAccountAdapter().is_email_bomb_risk(user.email, user=user)
        self.assertFalse(blocked)

    def test_other_emails_do_not_use_up_the_daily_verification_cap(self):
        user = User.objects.create_user('busy', 'busy@example.com', 'pw')
        for i in range(5):
            EmailLog.objects.create(
                user=user, to_email=user.email, subject=f'Your Daily Financial Digest {i}', body='x', status='SENT'
            )
        blocked, _ = CustomAccountAdapter().is_email_bomb_risk(user.email, user=user)
        self.assertFalse(blocked)

    def test_verification_cooldown_still_applies(self):
        user = User.objects.create_user('eager', 'eager@example.com', 'pw')
        EmailLog.objects.create(
            user=user, to_email=user.email, subject='[trackmyrupee.com] Confirm Your Email Address - TrackMyRupee',
            body='x', status='SENT',
        )
        blocked, message = CustomAccountAdapter().is_email_bomb_risk(user.email, user=user)
        self.assertTrue(blocked)
        self.assertIn('15 minutes', str(message))

    def test_signup_template_subject_is_what_the_cooldown_looks_for(self):
        subject = Path(settings.BASE_DIR, 'templates/account/email/email_confirmation_signup_subject.txt').read_text()
        self.assertIn(CustomAccountAdapter.VERIFICATION_SUBJECT, subject)

    def test_delivery_failure_does_not_break_the_signup_request(self):
        user = User.objects.create_user('unlucky', 'unlucky@example.com', 'pw')
        with patch('allauth.account.adapter.DefaultAccountAdapter.send_mail', side_effect=RuntimeError('down')) as send:
            with self.assertLogs('expenses.adapters', level='ERROR'):
                self.assertIsNone(CustomAccountAdapter().send_mail('account/email/password_reset_key', user.email, {'user': user}))
        send.assert_called_once()


class LifecycleEmailTests(TestCase):
    def setUp(self):
        self.user = verified_user('dripuser')
        User.objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=3))

    def run_command(self):
        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)
        return out.getvalue()

    def test_day_two_tip_is_sent_once(self):
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('3 Tips', mail.outbox[0].subject)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.last_drip_email_day, 2)
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)

    def test_plain_text_part_is_the_email_text_not_a_generic_line(self):
        self.run_command()
        body = mail.outbox[0].body
        self.assertNotIn('<', body)
        self.assertNotEqual(body, "Check out what's new on TrackMyRupee!")
        self.assertGreater(len(body), 80)

    def test_failed_send_is_retried_instead_of_marked_sent(self):
        with patch('expenses.management.commands.send_lifecycle_emails.send_mail', return_value=0):
            self.run_command()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.last_drip_email_day, 0)
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)

    def test_raising_backend_is_retried_too(self):
        with patch('expenses.management.commands.send_lifecycle_emails.send_mail', side_effect=RuntimeError('x')):
            self.run_command()
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.last_drip_email_day, 0)

    def test_inactive_users_get_nothing(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_unverified_addresses_get_nothing(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_paid_users_get_no_drip(self):
        profile = self.user.profile
        profile.tier = 'PLUS'
        profile.save()
        self.run_command()
        self.assertEqual(len(mail.outbox), 0)

    def test_catch_up_sends_only_the_latest_applicable_email(self):
        User.objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=20))
        self.run_command()
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Unlock', mail.outbox[0].subject)
        self.assertIn('₹', mail.outbox[0].alternatives[0][0])

    def test_day_thirty_summary_counts_the_users_own_activity(self):
        User.objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=31))
        acc = Account.objects.create(user=self.user, name='Bank', balance=0, currency='₹')
        for _i in range(3):
            Expense.objects.create(user=self.user, account=acc, amount=10, base_amount=10,
                                   date=timezone.now().date(), category='Food', currency='₹', exchange_rate=1)
        other = verified_user('other')
        Expense.objects.create(user=other, amount=10, base_amount=10, date=timezone.now().date(),
                               category='Food', currency='₹', exchange_rate=1)
        mail.outbox.clear()
        self.run_command()
        mine = [m for m in mail.outbox if m.to == [self.user.email]]
        self.assertEqual(len(mine), 1)
        self.assertIn('Review', mine[0].subject)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class MonthlyReportEmailTests(TestCase):
    def setUp(self):
        self.user = verified_user('reporter')
        today = timezone.now().date()
        self.start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        acc = Account.objects.create(user=self.user, name='Bank', balance=1000, currency='₹')
        Income.objects.create(user=self.user, account=acc, amount=500, base_amount=500, date=self.start,
                              source_type='Salary', currency='₹', exchange_rate=1)
        Expense.objects.create(user=self.user, account=acc, amount=100, base_amount=100, date=self.start,
                               category='Food', currency='₹', exchange_rate=1)

    def test_failed_send_is_not_logged_as_sent_so_it_is_retried(self):
        with patch('expenses.management.commands.send_monthly_report.send_mail', return_value=0):
            call_command('send_monthly_report', user_id=self.user.id, stdout=StringIO())
        self.assertEqual(EmailLog.objects.filter(status='SENT').count(), 0)
        call_command('send_monthly_report', user_id=self.user.id, stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)

    def test_dashboard_button_points_at_a_real_page(self):
        call_command('send_monthly_report', user_id=self.user.id, stdout=StringIO())
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('https://trackmyrupee.com/dashboard/', html)
        self.assertNotIn('trackmyrupee.com/home', html)

    def test_plain_text_part_is_not_just_the_subject(self):
        call_command('send_monthly_report', user_id=self.user.id, stdout=StringIO())
        self.assertNotEqual(mail.outbox[0].body, mail.outbox[0].subject)
        self.assertNotIn('<', mail.outbox[0].body)

    def test_net_worth_change_is_ledger_end_minus_ledger_start(self):
        """Opening net worth comes from the ledger the day before the month, not from income - outflow."""
        from expenses.management.commands.send_monthly_report import Command
        end = self.start.replace(day=28)
        calls = []

        def fake(user, as_of=None, **kw):
            calls.append(as_of)
            return (Decimal('1500') if as_of == end else Decimal('1000')), {}

        with patch('expenses.management.commands.send_monthly_report.LedgerReadService.get_net_worth', side_effect=fake):
            data = Command().get_report_data(self.user, self.start, end)
        self.assertIn(self.start - timedelta(days=1), calls)
        self.assertEqual(data['nw_change'], Decimal('500'))
        self.assertEqual(data['nw_change_pct'], 50.0)

    def test_card_in_credit_is_not_pending(self):
        from expenses.management.commands.send_monthly_report import Command
        card = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=0, currency='₹')
        owed = Account.objects.create(user=self.user, name='Card2', account_type='CREDIT_CARD', balance=0, currency='₹')
        balances = {card.id: Decimal('250'), owed.id: Decimal('-400')}
        end = self.start.replace(day=28)
        with patch('expenses.management.commands.send_monthly_report.LedgerReadService.get_net_worth',
                   return_value=(Decimal('1000'), balances)):
            data = Command().get_report_data(self.user, self.start, end)
        self.assertEqual(data['credit_card_pending'], Decimal('400'))

    def test_insight_share_is_of_regular_spending(self):
        """A loan or capital outflow must not shrink the category's share of what you spent."""
        from expenses.management.commands.send_monthly_report import Command
        from expenses.models import CapitalEvent
        CapitalEvent.objects.create(user=self.user, subtype='LAPTOP_PURCHASE', amount=900, base_amount=900,
                                    date=self.start, is_deleted=False, exclude_from_averages=True)
        data = Command().get_report_data(self.user, self.start, self.start.replace(day=28))
        self.assertIn('<b>100%</b>', data['ai_insight'])

    def test_unverified_and_inactive_users_get_no_report(self):
        EmailAddress.objects.filter(user=self.user).update(verified=False)
        call_command('send_monthly_report', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)
        EmailAddress.objects.filter(user=self.user).update(verified=True)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        call_command('send_monthly_report', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AnnouncementEmailTests(TestCase):
    def setUp(self):
        self.user = verified_user('reader')
        self.ann = Announcement.objects.create(title='Big news', body='**New** thing', send_email=True,
                                               audience='ALL', status='QUEUED')

    def test_each_announcement_is_mailed_once_even_if_the_command_runs_twice(self):
        call_command('send_announcements', stdout=StringIO())
        call_command('send_announcements', stdout=StringIO())
        self.assertEqual(len([m for m in mail.outbox if m.to == [self.user.email]]), 1)
        self.ann.refresh_from_db()
        self.assertEqual(self.ann.status, 'SENT')
        self.assertIsNotNone(self.ann.sent_at)

    def test_an_announcement_that_is_not_queued_is_not_sent(self):
        Announcement.objects.filter(pk=self.ann.pk).update(status='DRAFT')
        call_command('send_announcements', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)

    def test_inactive_users_and_demo_are_skipped(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        User.objects.create_user('demo', 'demo@example.com', 'pw')
        mail.outbox.clear()
        call_command('send_announcements', stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)

    def test_failed_sends_are_not_counted(self):
        out = StringIO()
        with patch('django.core.mail.EmailMultiAlternatives.send', return_value=0):
            call_command('send_announcements', stdout=out)
        self.assertIn('0 emails', out.getvalue())

    def test_body_is_rendered_markdown_and_plain_text_is_stripped(self):
        call_command('send_announcements', stdout=StringIO())
        msg = [m for m in mail.outbox if m.to == [self.user.email]][0]
        self.assertIn('<strong>New</strong>', msg.alternatives[0][0])
        self.assertNotIn('**', msg.body)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProductUpdateEmailTests(TestCase):
    def setUp(self):
        self.user = verified_user('updatee')

    def test_default_template_exists_and_renders(self):
        call_command(
            'send_product_update', subject='Hello there', heading='Big heading', body='Line one\nLine two',
            cta_link='https://trackmyrupee.com/pricing/', cta_text='See plans', user_id=self.user.id, stdout=StringIO(),
        )
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('Big heading', html)
        self.assertIn('See plans', html)
        self.assertIn('https://trackmyrupee.com/pricing/', html)

    def test_user_text_is_escaped(self):
        call_command('send_product_update', subject='S', body='<script>alert(1)</script>', user_id=self.user.id,
                     stdout=StringIO())
        self.assertNotIn('<script>', mail.outbox[0].alternatives[0][0])

    def test_dry_run_sends_nothing(self):
        call_command('send_product_update', subject='S', dry_run=True, stdout=StringIO())
        self.assertEqual(len(mail.outbox), 0)


class OneOffEmailTests(TestCase):
    def test_welcome_email_content(self):
        User.objects.create_user('fresh', 'fresh@example.com', 'pw')
        welcome = [m for m in mail.outbox if 'Welcome' in m.subject]
        self.assertEqual(len(welcome), 1)
        self.assertEqual(welcome[0].to, ['fresh@example.com'])
        self.assertIn('fresh', welcome[0].alternatives[0][0])

    def test_welcome_text_part_is_the_full_guide_with_real_plan_numbers(self):
        from finance_tracker.plans import PLAN_DETAILS
        User.objects.create_user('thorough', 'thorough@example.com', 'pw')
        msg = [m for m in mail.outbox if 'Welcome' in m.subject][0]
        free = PLAN_DETAILS['FREE']['limits']
        for text in (msg.body, msg.alternatives[0][0]):
            self.assertIn(f"{free['accounts']} accounts", text)
            self.assertIn(f"{free['expenses_per_month']} expenses a month", text)
            self.assertIn(f"₹{PLAN_DETAILS['PLUS']['price_monthly']} a month", text)
            self.assertIn('support@trackmyrupee.com', text)
        for feature in ('Net worth', 'Loans', 'Capital events', 'Budgets', 'goals', 'Calendar', 'Recurring'):
            self.assertIn(feature.lower(), msg.body.lower(), feature)
        self.assertGreater(len(msg.body), 1500)
        self.assertNotIn('<', msg.body)
        self.assertIn('https://trackmyrupee.com/dashboard/', msg.body)
        self.assertIn('https://trackmyrupee.com/dashboard/', msg.alternatives[0][0])

    def test_demo_user_gets_no_welcome(self):
        User.objects.create_user('demo', 'demo@example.com', 'pw')
        self.assertEqual(len([m for m in mail.outbox if m.to == ['demo@example.com']]), 0)

    def test_account_deletion_confirmation_is_sent_after_deleting(self):
        from expenses.views.settings import delete_user_account
        user = User.objects.create_user('leaver', 'leaver@example.com', 'pw')
        mail.outbox.clear()
        self.assertTrue(delete_user_account(user.id))
        self.assertFalse(User.objects.filter(pk=user.pk).exists())
        sent = [m for m in mail.outbox if m.to == ['leaver@example.com']]
        self.assertEqual(len(sent), 1)
        self.assertIn('Account Deleted', sent[0].subject)
        self.assertIn('leaver', sent[0].body)


class EmailTemplateLinkTests(TestCase):
    """Every site link in an email must open a real page (the monthly report button once went to a 404)."""

    LINK = re.compile(r'https://trackmyrupee\.com(/[^"\'\s<>{]*)?')

    def test_every_link_in_every_email_template_resolves(self):
        root = Path(settings.BASE_DIR, 'templates')
        files = [*root.glob('email/*'), *root.glob('emails/*'), *root.glob('account/email/*')]
        self.assertGreater(len(files), 10)
        bad = []
        for path in files:
            for match in self.LINK.finditer(path.read_text()):
                url_path = match.group(1) or '/'
                try:
                    resolve(url_path)
                except Resolver404:
                    bad.append(f'{path.name}: {url_path}')
        self.assertEqual(bad, [])

    def test_notification_fallback_links_resolve(self):
        source = Path(settings.BASE_DIR, 'expenses/management/commands/send_notifications.py').read_text()
        links = set(re.findall(r'"(/[a-z\-/]+/)"', source))
        self.assertTrue(links)
        for url_path in links:
            self.assertTrue(resolve(url_path), url_path)

    def test_every_email_template_renders_with_minimal_context(self):
        from django.template.loader import render_to_string
        user = User.objects.create_user('renderer', 'renderer@example.com', 'pw')
        context = {
            'user': user, 'username': 'renderer', 'subject': 'S', 'heading': 'H', 'body_text': 'B', 'body_html': '<p>B</p>',
            'announcement': Announcement(title='T', body='B'), 'notifications': [
                {'title': 'a', 'message': 'b', 'type': 'RECURRING', 'link': '/recurring/'}],
            'today': date.today(), 'currency_symbol': '₹', 'base_url': 'https://trackmyrupee.com',
            'monthly_price': 99, 'expense_count': 1, 'income_count': 1, 'category_count': 1,
            'data': {'has_data': True}, 'start_date': date.today(), 'end_date': date.today(),
            'activate_url': 'https://trackmyrupee.com/accounts/confirm-email/k/',
            'password_reset_url': 'https://trackmyrupee.com/accounts/password/reset/key/1-k/',
        }
        for name in ['email/welcome_email.html', 'email/account_deleted_email.html', 'email/announcement.html',
                     'email/drip_tips.html', 'email/drip_discover.html', 'email/drip_upgrade.html',
                     'email/drip_summary.html', 'email/recurring_reminder.html', 'email/product_update.html',
                     'emails/monthly_report.html', 'account/email/email_confirmation_signup_message.html',
                     'account/email/email_confirmation_message.html', 'account/email/password_reset_key_message.html']:
            self.assertTrue(render_to_string(name, context).strip(), name)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DripCopyTests(TestCase):
    """What the emails promise must match what the plans give."""

    def drip(self, days):
        user = verified_user(f'copy{days}')
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=days))
        call_command('send_lifecycle_emails', stdout=StringIO())
        return [m for m in mail.outbox if m.to == [user.email]][0], user

    def test_upgrade_email_numbers_come_from_the_plan_table(self):
        from finance_tracker.plans import PLAN_DETAILS
        msg, _user = self.drip(14)
        html = msg.alternatives[0][0]
        plus = PLAN_DETAILS['PLUS']['limits']
        self.assertIn(f"{plus['budget_categories']} Budget Categories", html)
        self.assertIn(f"{plus['recurring_transactions']} Recurring Transactions", html)
        self.assertIn(f"{plus['savings_goals']} Savings Goals", html)
        self.assertIn(f"₹{PLAN_DETAILS['PLUS']['price_monthly']}/month", html)

    def test_upgrade_email_does_not_sell_ai_as_a_paid_feature(self):
        from finance_tracker.plans import PLAN_DETAILS
        self.assertTrue(all(PLAN_DETAILS[t]['limits']['ai_insights'] for t in PLAN_DETAILS))
        for days in (14, 30):
            msg, _user = self.drip(days)
            self.assertNotIn('AI', msg.alternatives[0][0].replace('MAIL', ''))

    def test_recurring_tip_does_not_promise_emails_to_free_users(self):
        from finance_tracker.plans import PLAN_DETAILS
        self.assertFalse(PLAN_DETAILS['FREE']['limits']['email_notifications'])
        msg, _user = self.drip(5)
        self.assertNotIn('email, push', msg.alternatives[0][0])

    def test_tips_email_does_not_ask_for_replies_to_a_no_reply_address(self):
        msg, _user = self.drip(2)
        html = msg.alternatives[0][0]
        self.assertNotIn('reply to this email', html)
        self.assertIn('support@trackmyrupee.com', html)

    def test_first_month_counts_categories_actually_used(self):
        user = verified_user('cats')
        User.objects.filter(pk=user.pk).update(date_joined=timezone.now() - timedelta(days=31))
        for category in ('Food', 'Food', 'Fuel'):
            Expense.objects.create(user=user, amount=5, base_amount=5, date=timezone.now().date(),
                                   category=category, currency='₹', exchange_rate=1)
        mail.outbox.clear()
        call_command('send_lifecycle_emails', stdout=StringIO())
        html = [m for m in mail.outbox if m.to == [user.email]][0].alternatives[0][0]
        stats = re.findall(r'font-size: 28px[^>]*>\s*(\d+)\s*<', html)
        self.assertEqual(stats, ['3', '0', '2'])
