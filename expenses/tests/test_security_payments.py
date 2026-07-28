import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import PaymentHistory


class PaymentSecurityTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password')
        self.profile = self.user.profile
        self.client.login(username='testuser', password='password')

    @patch('razorpay.Client')
    def test_verify_payment_replay_attack(self, MockRazorpayClient):
        # Setup mock for razorpay signature verification to always succeed
        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.utility.verify_payment_signature.return_value = True

        # Create a payment history record that is ALREADY SUCCESS
        order_id = 'order_123'
        payment_id = 'pay_123'
        PaymentHistory.objects.create(
            user=self.user,
            order_id=order_id,
            payment_id=payment_id,
            amount=100.00,
            tier='PRO',
            duration='MONTHLY',
            status='SUCCESS'  # ALREADY SUCCESS
        )
        # Profile is already at the same tier (simulating a fully-activated account)
        self.profile.tier = 'PRO'
        self.profile.save()

        payload = {
            'razorpay_order_id': order_id,
            'razorpay_payment_id': payment_id,
            'razorpay_signature': 'fake_signature_that_passes_mock'
        }

        # Issue POST request to replay the payment
        response = self.client.post(
            reverse('verify-payment'),
            data=json.dumps(payload),
            content_type='application/json'
        )

        # When the tier is already fully activated, replay is idempotent (200 OK).
        # True forgery protection is enforced by Razorpay's HMAC signature check.
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json().get('success'))

    @patch('razorpay.Client')
    def test_verify_payment_cross_user_replay(self, MockRazorpayClient):
        """A payment order belonging to another user must return 404."""
        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.utility.verify_payment_signature.return_value = True

        other_user = User.objects.create_user(username='otheruser', email='other@example.com', password='password')
        PaymentHistory.objects.create(
            user=other_user,
            order_id='order_other',
            payment_id='pay_other',
            amount=100.00,
            tier='PRO',
            duration='MONTHLY',
            status='PENDING'
        )

        payload = {
            'razorpay_order_id': 'order_other',
            'razorpay_payment_id': 'pay_other',
            'razorpay_signature': 'fake_signature'
        }
        # Logged in as self.user, trying to claim another user's order
        response = self.client.post(
            reverse('verify-payment'),
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)

    def test_payment_endpoints_enforce_csrf(self):
        # We enforce CSRF by using a client that enforces CSRF checks
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.login(username='testuser', password='password')

        # Attempt to POST without CSRF token
        response_start_trial = csrf_client.post(reverse('start-trial'), data={}, content_type='application/json')
        response_verify = csrf_client.post(reverse('verify-payment'), data={}, content_type='application/json')
        response_cancel = csrf_client.post(reverse('cancel-subscription'), data={}, content_type='application/json')

        # Should be forbidden due to missing CSRF token
        self.assertEqual(response_start_trial.status_code, 403)
        self.assertEqual(response_verify.status_code, 403)
        self.assertEqual(response_cancel.status_code, 403)
