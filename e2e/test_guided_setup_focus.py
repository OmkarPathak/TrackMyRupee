"""A field of a Guided Setup keeps the cursor once it is clicked.

The setup form asks the server for a preview whenever a field changes, which also happens when the person clicks out
of one field into the next. The page used to blur whichever field had the cursor when that preview settled, so the
field just clicked lost focus a moment later and had to be clicked again. Typing into it did nothing in between.
"""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

ACTIVE = "document.activeElement && document.activeElement.getAttribute('name')"
# Names of the typing fields (text, number...) on the step that is showing
TYPING_FIELDS = """
() => {
  const step = Array.from(document.querySelectorAll('.wizard-step-content')).find(el => el.offsetParent !== null);
  if (!step) return [];
  return Array.from(step.querySelectorAll('input'))
    .filter(el => el.offsetParent !== null && !el.disabled && !el.readOnly &&
                  ['text', 'number', 'email', 'search', 'tel', 'url'].includes(el.type) && el.name)
    .map(el => el.name);
}
"""


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('focus')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def available_flows(self):
        from expenses.flows.registry import FlowRegistry
        return [flow for flow in FlowRegistry.all().values() if flow.is_available(self.user)]

    def click_from_one_field_to_the_next(self, page, key, *, phone):
        """Type in the first typing field of the step, click the second. Returns (second field, what has focus after)."""
        names = page.evaluate(TYPING_FIELDS)
        if len(names) < 2:
            return None, None
        first, second = (page.locator(f'#tmr-flow-form-{key} input[name="{name}"]') for name in names[:2])
        first.click()
        page.keyboard.type('12', delay=20)
        (second.tap() if phone else second.click())
        page.wait_for_timeout(1500)                    # long enough for the preview request to come back and settle
        return names[1], page.evaluate(ACTIVE)


class EveryFlowTests(Base):
    def check_every_flow(self, phone):
        failures, checked = [], 0
        for flow in self.available_flows():
            page = self.new_page(self.user, phone=phone)
            self.open(page, reverse('flow-detail', kwargs={'key': flow.key}))
            second, active = self.click_from_one_field_to_the_next(page, flow.key, phone=phone)
            if second is None:
                continue
            checked += 1
            if active != second:
                failures.append(f'{flow.key}: "{second}" lost focus (now {active!r})')
        self.assertGreaterEqual(checked, 5, 'too few setups had two typing fields on their first step')
        self.assertEqual(failures, [], 'the field clicked second lost focus in:\n' + '\n'.join(failures))

    def test_the_field_clicked_second_keeps_focus_in_every_setup_on_a_computer(self):
        self.check_every_flow(phone=False)

    def test_the_field_tapped_second_keeps_focus_in_every_setup_on_a_phone(self):
        self.check_every_flow(phone=True)


class TypingTests(Base):
    def test_what_is_typed_into_the_second_field_lands_in_it(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.click('input[name="name"]')
        page.keyboard.type('Big FD', delay=20)
        page.click('input[name="principal"]')
        page.wait_for_timeout(1000)                    # the preview for the name field has settled by now
        page.keyboard.type('250000', delay=20)         # (no second click needed)
        self.assertEqual(page.eval_on_selector('input[name="principal"]', 'el => el.value'), '250000')

    def test_what_is_typed_while_the_preview_is_still_on_its_way_is_kept(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.click('input[name="name"]')
        page.keyboard.type('Big FD', delay=20)
        page.click('input[name="principal"]')
        page.keyboard.type('250000', delay=150)        # keeps typing across the moment the preview settles
        self.assertEqual(page.eval_on_selector('input[name="principal"]', 'el => el.value'), '250000')

    def test_a_dropdown_changing_does_not_take_the_cursor_from_the_next_field(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        self.set_select(page, 'select[name="instrument_type"]', 'RD')
        page.click('input[name="name"]')
        page.wait_for_timeout(1200)
        self.assertEqual(page.evaluate(ACTIVE), 'name')


class SwapStillPutsTheKeyboardAwayTests(Base):
    """The reason for the blur stays: a swap that replaces the field the cursor is in must not leave the keyboard up."""

    def test_a_swap_around_the_focused_field_still_blurs_it(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.tap('input[name="name"]')
        page.evaluate("document.body.dispatchEvent(new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}))")
        page.wait_for_timeout(200)
        self.assertNotEqual(page.evaluate(ACTIVE), 'name')

    def test_a_swap_elsewhere_on_the_page_leaves_the_focused_field_alone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.tap('input[name="name"]')
        page.evaluate("document.querySelector('#tmr-review-sip').dispatchEvent("
                      "new CustomEvent('htmx:afterSettle', {bubbles: true, detail: {}}))")
        page.wait_for_timeout(200)
        self.assertEqual(page.evaluate(ACTIVE), 'name')
