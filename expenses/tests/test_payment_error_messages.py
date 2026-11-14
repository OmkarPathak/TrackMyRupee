"""When a payment step fails, the buyer gets a plain message and the cause goes to the log: exception text can carry keys,
internal URLs or field names, and was being sent back to the browser."""
import json
from unittest.mock import patch

import razorpay
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses import views_payment

SECRET = 'rzp_live_SECRETKEY at https://internal.example/path (password hunter2)'
RZP = dict(RAZORPAY_KEY_ID='k', RAZORPAY_KEY_SECRET='s')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', **RZP)
class PaymentFailureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='failing-buyer', email='buyer@example.com', password='pw')
        self.client.force_login(self.user)

    def post(self, name, body):
        return self.client.post(reverse(name), json.dumps(body), content_type='application/json')

    def assert_plain(self, response, status, message):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json(), {'error': message})
        for leaked in ('SECRETKEY', 'internal.example', 'hunter2'):
            self.assertNotIn(leaked, response.content.decode())

    def test_a_failure_creating_an_order_says_nothing_about_the_cause(self):
        with patch('expenses.views_payment.razorpay.Client') as gateway, self.assertLogs('expenses.views_payment', 'ERROR') as logs:
            gateway.return_value.order.create.side_effect = RuntimeError(SECRET)
            response = self.post('create-order', {'plan_type': 'PLUS', 'duration': 'YEARLY', 'is_recurring': False})
        self.assert_plain(response, 500, views_payment.PAYMENT_ERROR_MESSAGE)
        self.assertIn('SECRETKEY', str(logs.records[0].exc_info[1]))            # the cause is still in the log

    def test_a_failure_verifying_a_payment_says_nothing_about_the_cause(self):
        with patch('expenses.views_payment.razorpay.Client') as gateway, self.assertLogs('expenses.views_payment', 'ERROR') as logs:
            gateway.return_value.utility.verify_payment_signature.side_effect = RuntimeError(SECRET)
            response = self.post('verify-payment', {'razorpay_order_id': 'order_1', 'razorpay_payment_id': 'pay_1',
                                                    'razorpay_signature': 'x'})
        self.assert_plain(response, 500, views_payment.PAYMENT_ERROR_MESSAGE)
        self.assertIn('SECRETKEY', str(logs.records[0].exc_info[1]))

    def test_a_wrong_signature_is_still_refused_with_its_own_clear_message(self):
        with patch('expenses.views_payment.razorpay.Client') as gateway:
            gateway.return_value.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError('bad')
            response = self.post('verify-payment', {'razorpay_order_id': 'order_1', 'razorpay_payment_id': 'pay_1',
                                                    'razorpay_signature': 'x'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'error': 'Signature Verification Failed'})

    def subscribed(self):
        profile = self.user.profile
        profile.razorpay_subscription_id = 'sub_123'
        profile.save()

    def test_a_failure_cancelling_says_nothing_about_the_cause(self):
        self.subscribed()
        with patch('expenses.views_payment.razorpay.Client') as gateway, self.assertLogs('expenses.views_payment', 'ERROR') as logs:
            gateway.return_value.subscription.cancel.side_effect = RuntimeError(SECRET)
            response = self.client.post(reverse('cancel-subscription'))
        self.assert_plain(response, 500, views_payment.CANCEL_ERROR_MESSAGE)
        self.assertIn('SECRETKEY', str(logs.records[0].exc_info[1]))

    def test_a_gateway_complaint_about_the_request_is_not_passed_on_word_for_word(self):
        self.subscribed()
        with patch('expenses.views_payment.razorpay.Client') as gateway:
            gateway.return_value.subscription.cancel.side_effect = razorpay.errors.BadRequestError(SECRET)
            response = self.client.post(reverse('cancel-subscription'))
        self.assert_plain(response, 400, views_payment.CANCEL_ERROR_MESSAGE)

    def test_an_already_cancelled_subscription_is_still_treated_as_success(self):
        self.subscribed()
        with patch('expenses.views_payment.razorpay.Client') as gateway:
            gateway.return_value.subscription.cancel.side_effect = razorpay.errors.BadRequestError(
                'This subscription is already cancelled')
            response = self.client.post(reverse('cancel-subscription'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
