"""Guided Setup forms: an empty field is not an error until the person confirms, the per-fund amounts are grouped, and the
page carries the phone step circles and the waiting state of the review."""
import re

from django.urls import reverse

from expenses.flows.registry import FlowRegistry
from expenses.tests.test_flows_end_to_end import FlowTestBase


class RequiredErrorsOnConfirmTests(FlowTestBase):
    """While the form is being filled, an empty field is not an error yet: "required" shows only on confirming."""

    def post(self, name, data, htmx=True):
        extra = {'HTTP_HX_REQUEST': 'true'} if htmx else {}
        return self.client.post(reverse(name, kwargs={'key': 'fd'}), data, **extra).content.decode()

    def error_in(self, html, field):
        match = re.search(rf'id="field-errors-{field}" hx-swap-oob="true">(.*?)</div>\s*(?=<div id=|$)', html, re.S)
        self.assertIsNotNone(match, field)
        return match.group(1)

    def test_a_preview_of_a_form_with_only_the_name_shows_no_required_errors(self):
        html = self.post('flow-preview', {'name': 'Baliraja 25L FD'})
        self.assertNotIn('This field is required', html)

    def test_confirming_an_incomplete_form_shows_them(self):
        html = self.post('flow-commit', {'name': 'Baliraja 25L FD'})
        self.assertIn('This field is required', self.error_in(html, 'principal'))
        self.assertIn('This field is required', self.error_in(html, 'annual_rate'))

    def test_an_invalid_value_still_shows_as_the_person_types(self):
        data = {'name': 'FD', 'principal': '100000', 'annual_rate': '7', 'deposit_start_date': '2026-10-01',
                'maturity_date': '2026-09-01', 'deposit_compounding': 'QUARTERLY', 'from_account': self.cash.pk}
        html = self.post('flow-preview', data)
        self.assertIn('Maturity date must be after the start date.', self.error_in(html, 'maturity_date'))

    def test_a_field_that_was_flagged_is_cleared_once_it_is_fixed(self):
        data = {'name': 'FD', 'principal': '100000', 'annual_rate': '7', 'deposit_start_date': '2026-10-01',
                'maturity_date': '2026-09-01', 'deposit_compounding': 'QUARTERLY', 'from_account': self.cash.pk}
        self.assertIn('Maturity date', self.post('flow-preview', data))
        data['maturity_date'] = '2027-10-01'
        self.assertNotIn('Maturity date must be after', self.post('flow-preview', data))

    def test_every_field_has_its_error_spot_so_an_old_message_can_be_cleared(self):
        html = self.post('flow-preview', {'name': 'x'})
        for field in ('name', 'principal', 'annual_rate', 'from_account', 'maturity_date'):
            self.assertIn(f'id="field-errors-{field}"', html)


class PageMarkupTests(FlowTestBase):
    def test_the_per_fund_amount_boxes_are_grouped_like_other_amounts(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'sip'})).content.decode()
        self.assertIn('data-f="amount" data-money', html)

    def test_the_phone_step_circles_are_on_the_page_once_per_step_and_the_review(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        steps = len(FlowRegistry.get('fd').get_wizard_steps())
        self.assertEqual(len(re.findall(r'class="wizard-dot"', html)), steps + 1)
        self.assertIn('wizard-dots-title', html)

    def test_the_circles_carry_each_steps_name(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        for title in ('Deposit Basics', 'Interest payout'):
            self.assertIn(f'data-title="{title}"', html)

    def test_the_review_box_can_show_that_it_is_waiting(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        self.assertIn("'tmr-review-loading': reviewLoading", html)
