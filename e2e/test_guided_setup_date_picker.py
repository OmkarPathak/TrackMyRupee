"""A date field in a Guided Setup opens its calendar on the first tap and keeps it open.

The setup form asks the server for a preview whenever a field changes. Tapping a date field straight after typing in
another one makes the typed field fire its "changed" event, which starts a preview request; when that request settled,
the page blurred whatever field had the cursor (to keep the phone keyboard from popping up after a swap), which closed
the calendar that had just opened. The second tap worked because nothing was pending by then.
"""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.test_guided_setup import FILL_STEP

ACTIVE = "document.activeElement && document.activeElement.getAttribute('name')"
VISIBLE_DATE = '.wizard-step-content:visible input[type="date"]:visible'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('date-picker')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        self.today = timezone.localdate()

    def available_flows(self):
        from expenses.flows.registry import FlowRegistry
        return [flow for flow in FlowRegistry.all().values() if flow.is_available(self.user)]

    def dates_per_flow(self):
        """{flow key: step titles with a date field}, read from each flow's form."""
        found = {}
        for flow in self.available_flows():
            form = flow.form_class()
            names = {name for name, field in form.fields.items() if field.widget.input_type == 'date'}
            steps = [step.key for step in flow.get_wizard_steps() if names & set(step.fields)]
            if steps:
                found[flow.key] = steps
        return found

    def go_to_date_step(self, page, key):
        """Fill each step on the way and stop on the first one that shows a date field."""
        form = page.locator(f'#tmr-flow-form-{key}')
        for _step in range(10):
            if page.locator(VISIBLE_DATE).count():
                return True
            page.evaluate(FILL_STEP, [self.today.isoformat(), (self.today + timedelta(days=365)).isoformat(), key])
            form.locator('button:has-text("Next") >> visible=true').first.click()
            page.wait_for_timeout(300)
        return page.locator(VISIBLE_DATE).count() > 0

    def type_then_tap_the_date(self, page, key):
        """Type in a text or number field of the same step, then tap the date field straight away."""
        date_input = page.locator(VISIBLE_DATE).first
        name = date_input.get_attribute('name')
        other = page.locator('.wizard-step-content:visible input:visible:not([type="date"]):not([type="checkbox"])'
                             ':not([type="radio"]):not([type="hidden"])').first
        if other.count():
            other.fill('12')                 # leaves the cursor in a field whose blur will fire "change"
        page.tap(f'{VISIBLE_DATE}[name="{name}"]')
        return name


class PhoneTests(Base):
    def test_the_date_field_keeps_focus_after_one_tap_in_every_setup_that_has_one(self):
        failures = []
        for key in self.dates_per_flow():
            page = self.new_page(self.user, phone=True)
            self.open(page, reverse('flow-detail', kwargs={'key': key}))
            if not self.go_to_date_step(page, key):
                failures.append(f'{key}: no date field was reached')
                continue
            name = self.type_then_tap_the_date(page, key)
            page.wait_for_timeout(1500)               # long enough for the preview request to come back and settle
            if page.evaluate(ACTIVE) != name:
                failures.append(f'{key}: the date field "{name}" lost focus (now {page.evaluate(ACTIVE)!r})')
        self.assertEqual(failures, [], 'the calendar closed by itself in:\n' + '\n'.join(failures))

    def test_a_preview_that_settles_while_a_date_field_has_focus_does_not_take_it_away(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.tap('input[name="deposit_start_date"]')
        page.evaluate("document.body.dispatchEvent(new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}))")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate(ACTIVE), 'deposit_start_date')

    def test_a_date_can_still_be_typed_into_the_field_after_tapping_it(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.fill('input[name="name"]', 'Index SIP')
        page.tap('input[name="deposit_start_date"]')
        page.fill('input[name="deposit_start_date"]', '2027-03-05')
        page.wait_for_timeout(300)
        self.assertEqual(page.input_value('input[name="deposit_start_date"]'), '2027-03-05')


class DesktopTests(Base):
    def test_typing_then_clicking_the_date_keeps_focus_on_a_computer_too(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.fill('input[name="name"]', 'Index SIP')
        page.click('input[name="deposit_start_date"]')
        page.wait_for_timeout(1500)
        self.assertEqual(page.evaluate(ACTIVE), 'deposit_start_date')

    def test_the_keyboard_is_still_put_away_after_a_swap_when_a_text_field_has_focus(self):
        """The reason for the blur stays: a swap must not leave the phone keyboard open on a text field."""
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.tap('input[name="name"]')
        page.evaluate("document.body.dispatchEvent(new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}))")
        page.wait_for_timeout(200)
        self.assertNotEqual(page.evaluate(ACTIVE), 'name')
