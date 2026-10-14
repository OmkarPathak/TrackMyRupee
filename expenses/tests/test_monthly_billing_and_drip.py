from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from expenses.models import SubscriptionPlan, UserProfile


class LifecycleEmailTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='dripuser', password='password', email='drip@example.com'
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.tier = 'FREE'
        self.profile.last_drip_email_day = 0
        self.profile.save()
        # Drips go only to confirmed addresses
        from allauth.account.models import EmailAddress
        EmailAddress.objects.update_or_create(
            user=self.user, email=self.user.email, defaults={'primary': True, 'verified': True}
        )

        # Create subscription plans for the pricing context
        SubscriptionPlan.objects.create(
            tier='PLUS', duration='MONTHLY', name='Plus Monthly',
            price=29, is_active=True
        )
        
        # Clear outbox (which contains the welcome email from user creation in setUp)
        mail.outbox.clear()

    def test_day2_email_sent(self):
        """Day 2 email should be sent for a 2-day-old free user."""
        self.user.date_joined = timezone.now() - timedelta(days=2)
        self.user.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        self.assertIn('Sent Day 2 email', out.getvalue())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_drip_email_day, 2)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Tips', mail.outbox[0].subject)

    def test_no_duplicate_emails(self):
        """Should not resend a drip email that has already been sent."""
        self.user.date_joined = timezone.now() - timedelta(days=3)
        self.user.save()
        self.profile.last_drip_email_day = 2
        self.profile.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        # Should NOT send Day 2 again
        self.assertEqual(len(mail.outbox), 0)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_drip_email_day, 2)

    def test_day14_email_sent(self):
        """Day 14 email should be sent for a 14-day-old free user."""
        self.user.date_joined = timezone.now() - timedelta(days=14)
        self.user.save()
        self.profile.last_drip_email_day = 5  # Already received Day 5
        self.profile.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        self.assertIn('Sent Day 14 email', out.getvalue())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_drip_email_day, 14)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Potential', mail.outbox[0].subject)

    def test_paid_users_skipped(self):
        """Paid users should not receive drip emails."""
        self.user.date_joined = timezone.now() - timedelta(days=5)
        self.user.save()
        self.profile.tier = 'PLUS'
        self.profile.is_lifetime = True
        self.profile.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        self.assertEqual(len(mail.outbox), 0)

    def test_catchup_sends_latest(self):
        """If cron was down, should send the latest applicable email, not all missed ones."""
        self.user.date_joined = timezone.now() - timedelta(days=16)
        self.user.save()
        self.profile.last_drip_email_day = 0  # Never received any
        self.profile.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        # Should send Day 14 (the latest applicable), not Day 2 or 5
        self.assertIn('Sent Day 14 email', out.getvalue())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_drip_email_day, 14)
        self.assertEqual(len(mail.outbox), 1)

    def test_day30_email_sent(self):
        """Day 30 email should include user stats."""
        self.user.date_joined = timezone.now() - timedelta(days=31)
        self.user.save()
        self.profile.last_drip_email_day = 14
        self.profile.save()

        out = StringIO()
        call_command('send_lifecycle_emails', stdout=out)

        self.assertIn('Sent Day 30 email', out.getvalue())
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.last_drip_email_day, 30)
        self.assertEqual(len(mail.outbox), 1)


class MonthlyBillingPaymentTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='billinguser', password='password')
        from django.test import Client
        self.client = Client()
        self.client.login(username='billinguser', password='password')

        # Create monthly and yearly plans
        SubscriptionPlan.objects.create(
            tier='PLUS', duration='MONTHLY', name='Plus Monthly',
            price=29, is_active=True
        )
        SubscriptionPlan.objects.create(
            tier='PLUS', duration='YEARLY', name='Plus Yearly',
            price=249, is_active=True
        )

    @patch('expenses.views_payment.razorpay.Client')
    def test_create_order_monthly(self, MockRazorpayClient):
        """Creating an order with MONTHLY duration should use the monthly plan price."""
        import json

        from django.urls import reverse

        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.order.create.return_value = {'id': 'order_monthly_123', 'status': 'created'}

        url = reverse('create-order')
        data = {'plan_type': 'PLUS', 'duration': 'MONTHLY', 'is_recurring': False}
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], 'order_monthly_123')

        # Verify the order was created with monthly price (29 * 100 = 2900 paise)
        call_args = mock_client_instance.order.create.call_args[1]['data']
        self.assertEqual(call_args['amount'], 4900)

    @patch('expenses.views_payment.razorpay.Client')
    def test_create_order_yearly(self, MockRazorpayClient):
        """Creating an order with YEARLY duration should use the yearly plan price."""
        import json

        from django.urls import reverse

        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.order.create.return_value = {'id': 'order_yearly_123', 'status': 'created'}

        url = reverse('create-order')
        data = {'plan_type': 'PLUS', 'duration': 'YEARLY', 'is_recurring': False}
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 200)

        # Verify the order was created with yearly price (249 * 100 = 24900 paise)
        call_args = mock_client_instance.order.create.call_args[1]['data']
        self.assertEqual(call_args['amount'], 49900)

    @patch('expenses.views_payment.razorpay.Client')
    def test_create_order_invalid_duration(self, MockRazorpayClient):
        """Invalid duration should return 400."""
        import json

        from django.urls import reverse

        url = reverse('create-order')
        data = {'plan_type': 'PLUS', 'duration': 'WEEKLY'}
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    @patch('expenses.views_payment.razorpay.Client')
    def test_verify_payment_monthly_sets_30_days(self, MockRazorpayClient):
        """Monthly payment should set subscription_end_date to ~30 days."""
        import json

        from django.urls import reverse

        from expenses.models import PaymentHistory

        PaymentHistory.objects.create(
            user=self.user, order_id='order_mo_123', amount=49,
            tier='PLUS', duration='MONTHLY', status='PENDING'
        )

        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.utility.verify_payment_signature.return_value = None

        url = reverse('verify-payment')
        data = {
            'razorpay_order_id': 'order_mo_123',
            'razorpay_payment_id': 'pay_mo_123',
            'razorpay_signature': 'sig_mo_123',
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])

        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_plus)
        # Check end date is ~30 days from now
        delta = self.user.profile.subscription_end_date - timezone.now()
        self.assertAlmostEqual(delta.days, 30, delta=1)
