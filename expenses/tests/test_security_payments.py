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
            status='SUCCESS' # ALREADY SUCCESS
        )

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

        # It should be rejected with 400 Bad Request instead of returning success
        self.assertEqual(response.status_code, 400)
        self.assertIn('Payment already verified', response.json().get('error', ''))

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
