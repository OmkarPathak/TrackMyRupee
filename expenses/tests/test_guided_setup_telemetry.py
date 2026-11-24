"""Guided Setup is measured so we can see where people leave it: which setups are opened, which are finished, and what
Confirm complains about. Events carry the setup's key and counts only, never what the person typed."""
from unittest.mock import patch

from django.urls import reverse

from expenses.models import Account
from expenses.tests.test_flows_end_to_end import FlowTestBase, valid_payloads


class Base(FlowTestBase):
    def setUp(self):
        super().setUp()
        patcher = patch('expenses.flows.telemetry.ph_capture')
        self.capture = patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self):
        return [(c.args[1], c.args[2]) for c in self.capture.call_args_list]


class OpeningTests(Base):
    def test_the_landing_page_reports_how_much_is_set_up(self):
        self.client.get(reverse('flow-landing'))
        (name, props), = self.sent()
        self.assertEqual(name, 'guided_setup_landing_viewed')
        self.assertEqual(set(props), {'configured', 'total'})
        self.assertGreater(props['total'], 0)

    def test_opening_a_setup_reports_which_one(self):
        self.client.get(reverse('flow-detail', kwargs={'key': 'fd'}))
        self.assertEqual(self.sent(), [('guided_setup_started', {'flow': 'fd'})])

    def test_the_form_being_posted_back_is_not_a_second_start(self):
        self.client.post(reverse('flow-detail', kwargs={'key': 'fd'}), valid_payloads(self)['fd'])
        self.assertEqual(self.sent(), [])


class ConfirmTests(Base):
    def commit(self, key='fd', **extra):
        payload = dict(valid_payloads(self)[key], **extra)
        return self.client.post(reverse('flow-commit', kwargs={'key': key}), payload, HTTP_HX_REQUEST='true')

    def test_a_finished_setup_is_reported(self):
        self.commit()
        self.assertEqual(self.sent(), [('guided_setup_completed', {'flow': 'fd'})])

    def test_a_confirm_that_found_a_problem_says_what_kind_and_how_many_fields(self):
        Account.objects.create(user=self.user, name='SBI FD', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        self.commit()
        (name, props), = self.sent()
        self.assertEqual(name, 'guided_setup_confirm_failed')
        self.assertEqual(props, {'flow': 'fd', 'kind': 'invalid', 'field_count': 1, 'first_field': 'name'})

    def test_a_plan_limit_is_told_apart_from_an_error(self):
        from expenses.flows.base import PlanLimitError
        from expenses.flows.registry import FlowRegistry
        flow = FlowRegistry.get('fd')
        with patch.object(type(flow), 'commit', side_effect=PlanLimitError('limit')):
            self.commit()
        self.assertEqual(self.sent(), [('guided_setup_confirm_failed', {'flow': 'fd', 'kind': 'plan_limit'})])

    def test_nothing_the_person_typed_is_in_an_event(self):
        Account.objects.create(user=self.user, name='SBI FD', account_type='SAVINGS_ACCOUNT', balance=0, currency='₹')
        self.commit()
        self.assertNotIn('SBI', repr(self.sent()))


class ConsentTests(FlowTestBase):
    def test_nothing_reaches_posthog_without_consent(self):
        from unittest.mock import MagicMock
        from expenses import posthog_utils
        client = MagicMock()
        with patch.object(posthog_utils, '_get_client', return_value=client):
            self.client.get(reverse('flow-detail', kwargs={'key': 'fd'}))
        client.capture.assert_not_called()
