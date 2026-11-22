"""A Guided Setup that opens an account cannot reuse the name of one the person already has: the form says so on the
name, instead of failing at Confirm as if a plan limit had been reached."""
import re
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.urls import reverse

from expenses.flows.registry import FlowRegistry
from expenses.models import Account
from expenses.tests.test_flows_end_to_end import FlowTestBase, valid_payloads

# The setups that open an account named by the "name" field
NAMED = ('insurance', 'sip', 'fd', 'ppfepfnps', 'car', 'gold')


class FormTests(FlowTestBase):
    def test_a_name_an_active_account_already_has_is_refused_on_the_name(self):
        for key in NAMED:
            payload = valid_payloads(self)[key]
            Account.objects.create(user=self.user, name=payload['name'], account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
            form = FlowRegistry.get(key).form_class(data=payload, user=self.user)
            self.assertFalse(form.is_valid(), key)
            self.assertIn('name', form.errors, key)
            self.assertIn('already', str(form.errors['name']).lower(), key)

    def test_the_check_ignores_case(self):
        payload = dict(valid_payloads(self)['fd'])
        Account.objects.create(user=self.user, name=payload['name'].upper(), account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        self.assertIn('name', FlowRegistry.get('fd').form_class(data=payload, user=self.user).errors)

    def test_a_closed_account_does_not_block_the_name(self):
        payload = valid_payloads(self)['fd']
        Account.objects.create(user=self.user, name=payload['name'], account_type='SAVINGS_ACCOUNT', balance=0, currency='₹',
                               is_active=False)
        self.assertTrue(FlowRegistry.get('fd').form_class(data=payload, user=self.user).is_valid())

    def test_another_persons_account_does_not_block_the_name(self):
        from django.contrib.auth.models import User
        other = User.objects.create_user('other', password='x')
        payload = valid_payloads(self)['fd']
        Account.objects.create(user=other, name=payload['name'], account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        self.assertTrue(FlowRegistry.get('fd').form_class(data=payload, user=self.user).is_valid())

    def test_a_fresh_name_is_fine_for_every_setup(self):
        for key in NAMED:
            self.assertTrue(FlowRegistry.get(key).form_class(data=valid_payloads(self)[key], user=self.user).is_valid(), key)


class ConfirmTests(FlowTestBase):
    def commit(self, key='fd', **extra):
        payload = dict(valid_payloads(self)[key], **extra)
        return self.client.post(reverse('flow-commit', kwargs={'key': key}), payload, HTTP_HX_REQUEST='true')

    def test_a_clashing_name_is_shown_on_the_name_and_is_not_a_plan_limit(self):
        Account.objects.create(user=self.user, name='SBI FD', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        response = self.commit()
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        spot = re.search(r'id="field-errors-name" hx-swap-oob="true">(.*?)</div>\s*(?=<div id=|$)', html, re.S).group(1)
        self.assertIn('already', spot.lower())
        self.assertNotIn('tmr-limit-warning', html)
        self.assertEqual(Account.objects.filter(user=self.user, name='SBI FD').count(), 1)

    def test_a_problem_found_while_creating_is_shown_as_an_error_not_a_plan_limit(self):
        flow = FlowRegistry.get('fd')
        with patch.object(type(flow), 'commit', side_effect=ValidationError('Something went wrong while saving.')):
            html = self.commit().content.decode()
        self.assertIn('Something went wrong while saving.', html)
        self.assertNotIn('tmr-limit-warning', html)
        self.assertIn('tmr-commit-error', html)

    def test_a_real_plan_limit_still_gets_the_plans_banner(self):
        flow = FlowRegistry.get('fd')
        from expenses.flows.base import PlanLimitError
        with patch.object(type(flow), 'commit', side_effect=PlanLimitError('You have reached your limit of accounts.')):
            html = self.commit().content.decode()
        self.assertIn('tmr-limit-warning', html)
        self.assertIn('View plans', html)
        self.assertNotIn('tmr-commit-error', html)


class RetryTests(FlowTestBase):
    def post(self, key):
        payload = dict(valid_payloads(self)['fd'], idempotency_key=key)
        return self.client.post(reverse('flow-commit', kwargs={'key': 'fd'}), payload, HTTP_HX_REQUEST='true')

    def test_confirming_twice_with_the_same_key_answers_the_second_time_as_the_first(self):
        import uuid
        key = str(uuid.uuid4())
        first, second = self.post(key), self.post(key)
        self.assertEqual((first.status_code, second.status_code), (204, 204))
        self.assertEqual(first['HX-Redirect'], second['HX-Redirect'])
        self.assertEqual(Account.objects.filter(user=self.user, name='SBI FD').count(), 1)

    def test_a_second_setup_with_the_same_name_and_a_new_key_is_refused(self):
        import uuid
        self.assertEqual(self.post(str(uuid.uuid4())).status_code, 204)
        response = self.post(str(uuid.uuid4()))
        self.assertEqual(response.status_code, 200)
        self.assertIn('already', response.content.decode().lower())
        self.assertEqual(Account.objects.filter(user=self.user, name='SBI FD').count(), 1)
