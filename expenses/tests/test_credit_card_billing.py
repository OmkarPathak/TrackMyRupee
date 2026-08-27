from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import AccountForm
from expenses.models import Account, Notification, UserProfile
from expenses.utils import get_safe_date


class GetSafeDateTestCase(TestCase):
    def test_get_safe_date_normal_day(self):
        self.assertEqual(get_safe_date(2026, 8, 15), date(2026, 8, 15))

    def test_get_safe_date_february_non_leap(self):
        # 2027 is non-leap year (28 days in Feb)
        self.assertEqual(get_safe_date(2027, 2, 31), date(2027, 2, 28))

    def test_get_safe_date_february_leap(self):
        # 2028 is leap year (29 days in Feb)
        self.assertEqual(get_safe_date(2028, 2, 31), date(2028, 2, 29))

    def test_get_safe_date_30_day_month(self):
        # April has 30 days
        self.assertEqual(get_safe_date(2026, 4, 31), date(2026, 4, 30))


class CreditCardBillingModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password123')

    def test_next_billing_date_future_same_month(self):
        # Today is 2026-08-10, billing day is 20 -> next billing date is 2026-08-20
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 10, 10, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='HDFC Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=20,
            )
            self.assertEqual(acc.next_billing_date, date(2026, 8, 20))

    def test_next_billing_date_today(self):
        # Today is 2026-08-20, billing day is 20 -> next billing date is today (2026-08-20)
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='HDFC Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=20,
            )
            self.assertEqual(acc.next_billing_date, date(2026, 8, 20))

    def test_next_billing_date_passed_same_month(self):
        # Today is 2026-08-25, billing day is 20 -> next billing date is next month 2026-09-20
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 25, 10, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='HDFC Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=20,
            )
            self.assertEqual(acc.next_billing_date, date(2026, 9, 20))

    def test_next_billing_date_passed_december_rollover(self):
        # Today is 2026-12-25, billing day is 15 -> next billing date is next year 2027-01-15
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 12, 25, 10, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='HDFC Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=15,
            )
            self.assertEqual(acc.next_billing_date, date(2027, 1, 15))

    def test_next_billing_date_none_when_unset(self):
        acc = Account.objects.create(
            user=self.user,
            name='HDFC Credit Card',
            account_type='CREDIT_CARD',
            credit_card_billing_day=None,
        )
        self.assertIsNone(acc.next_billing_date)

    def test_next_billing_date_none_for_non_revolving_credit(self):
        # Even if billing day is set, if strategy is BALANCE or DEPOSIT, return None
        acc = Account.objects.create(
            user=self.user,
            name='HDFC Bank Savings',
            account_type='SAVINGS_ACCOUNT',
            credit_card_billing_day=15,
        )
        self.assertIsNone(acc.next_billing_date)


class CreditCardNotificationCommandTestCase(TestCase):
    def setUp(self):
        self.free_user = User.objects.create_user(username='freeuser', email='free@example.com', password='password123')
        self.free_profile, _ = UserProfile.objects.get_or_create(user=self.free_user)
        self.free_profile.tier = 'FREE'
        self.free_profile.save()

        self.plus_user = User.objects.create_user(username='plususer', email='plus@example.com', password='password123')
        self.plus_profile, _ = UserProfile.objects.get_or_create(user=self.plus_user)
        self.plus_profile.tier = 'PLUS'
        self.plus_profile.save()

    def test_send_notifications_reminds_3_days_in_advance(self):
        # Frozen date: 2026-08-20. 3 days in advance = 2026-08-23.
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

            acc = Account.objects.create(
                user=self.free_user,
                name='Amazon Pay ICICI Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=23,
            )

            call_command('send_notifications')

            notifications = Notification.objects.filter(user=self.free_user, slug=f"billing-{acc.id}-2026-8")
            self.assertEqual(notifications.count(), 1)
            notif = notifications.first()
            self.assertIn("Upcoming Credit Card Bill", notif.title)
            self.assertIn("Amazon Pay ICICI Card", notif.message)
            self.assertIn("Aug 23", notif.message)

    def test_send_notifications_deduplication_same_run(self):
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

            acc = Account.objects.create(
                user=self.free_user,
                name='Axis Flipkart Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=23,
            )

            # Run command twice
            call_command('send_notifications')
            call_command('send_notifications')

            # Expect exactly 1 notification created due to slug deduplication
            notifications = Notification.objects.filter(user=self.free_user, slug=f"billing-{acc.id}-2026-8")
            self.assertEqual(notifications.count(), 1)

    def test_send_notifications_tier_gated_email(self):
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)

            # Free user account
            Account.objects.create(
                user=self.free_user,
                name='Free User CC',
                account_type='CREDIT_CARD',
                credit_card_billing_day=23,
            )
            # Plus user account
            Account.objects.create(
                user=self.plus_user,
                name='Plus User CC',
                account_type='CREDIT_CARD',
                credit_card_billing_day=23,
            )

            mail.outbox = []
            call_command('send_notifications')

            # Free user gets UI notification, but no email
            self.assertTrue(Notification.objects.filter(user=self.free_user).exists())
            # Plus user gets UI notification and email
            self.assertTrue(Notification.objects.filter(user=self.plus_user).exists())
            self.assertEqual(len(mail.outbox), 1)
            self.assertEqual(mail.outbox[0].to, ['plus@example.com'])


class CreditCardFormAndTemplateTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='formuser', password='password123')

    def test_account_form_saves_billing_day_for_revolving_credit(self):
        form_data = {
            'name': 'SBI SimplyClick',
            'account_type': 'CREDIT_CARD',
            'balance': '-5000.00',
            'currency': '₹',
            'credit_limit': '50000.00',
            'credit_card_billing_day': 15,
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        self.assertEqual(account.credit_card_billing_day, 15)

    def test_account_form_cleans_billing_day_for_non_revolving_credit(self):
        form_data = {
            'name': 'HDFC Savings',
            'account_type': 'SAVINGS_ACCOUNT',
            'balance': '10000.00',
            'currency': '₹',
            'credit_card_billing_day': 15,  # Invalid for savings account
        }
        form = AccountForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        account = form.save()
        self.assertIsNone(account.credit_card_billing_day)

    def test_account_list_rendering_badge(self):
        self.client.force_login(self.user)
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='OneCard Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=18,
            )

            response = self.client.get(reverse('account-list'), HTTP_HX_REQUEST='true')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Next Bill')
            self.assertContains(response, '18 Aug')

    def test_account_detail_rendering(self):
        self.client.force_login(self.user)
        with patch('django.utils.timezone.now') as mock_now:
            mock_now.return_value = timezone.datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
            acc = Account.objects.create(
                user=self.user,
                name='OneCard Credit Card',
                account_type='CREDIT_CARD',
                credit_card_billing_day=18,
            )

            response = self.client.get(reverse('account-detail', args=[acc.uuid]), HTTP_HX_REQUEST='true')
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Next Bill:')
            self.assertContains(response, '18 Aug 2026')
