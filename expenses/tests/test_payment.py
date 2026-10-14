import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import PaymentHistory, SubscriptionPlan


class PaymentViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        # Create SubscriptionPlan
        SubscriptionPlan.objects.create(tier='PLUS', price=999, is_active=True)

    @patch('expenses.views_payment.razorpay.Client')
    def test_create_order(self, MockRazorpayClient):
        # Mock the client instance and order.create method
        mock_client_instance = MockRazorpayClient.return_value
        mock_client_instance.order.create.return_value = {'id': 'order_123', 'status': 'created'}

        url = reverse('create-order')
        data = {'plan_type': 'PLUS', 'is_recurring': False}
        response = self.client.post(url, json.dumps(data), content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['id'])
        self.assertEqual(response.json()['id'], 'order_123')

    @patch('expenses.views_payment.razorpay.Client')
    def test_verify_payment_success(self, MockRazorpayClient):
        # Create pending payment record
        PaymentHistory.objects.create(
            user=self.user, order_id='order_123', amount=999,
            tier='PLUS', status='PENDING'
        )

        mock_client_instance = MockRazorpayClient.return_value
        # verify_payment_signature returns None on success, raises error on failure
        mock_client_instance.utility.verify_payment_signature.return_value = None

        url = reverse('verify-payment')
        data = {
            'razorpay_order_id': 'order_123',
            'razorpay_payment_id': 'pay_123',
            'razorpay_signature': 'sig_123',
            'plan_type': 'PLUS'
        }
        response = self.client.post(url, json.dumps(data), content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        
        # Verify user profile updated
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.is_plus)
