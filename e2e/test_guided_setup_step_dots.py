"""On a phone a Guided Setup shows its steps as numbered circles with the current step's name written out; tablets and
computers keep the tabs."""
import re

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

DOTS = '.wizard-dot:visible'
TITLE = '.wizard-dots-title'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('dots')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def open_flow(self, page, key='fd'):
        self.open(page, reverse('flow-detail', kwargs={'key': key}))
        page.wait_for_selector('#stepCounterBadge')

    def next(self, page, key='fd'):
        page.locator(f'#tmr-flow-form-{key} button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(350)

    def counter(self, page):
        """(current step, total) from the "Step 2 of 5" badge."""
        match = re.search(r'(\d+)\D+(\d+)', page.inner_text('#stepCounterBadge'))
        return int(match.group(1)), int(match.group(2))


class PhoneTests(Base):
    def test_the_steps_are_circles_and_the_tabs_are_hidden(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        self.assertFalse(page.locator('.wizard-steps-tabs-row').is_visible())
        self.assertTrue(page.locator('.wizard-dots').is_visible())

    def test_there_is_a_circle_for_every_step_and_the_review(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        self.assertEqual(page.locator(DOTS).count(), self.counter(page)[1])

    def test_the_current_steps_name_is_written_out_in_full(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        self.assertEqual(page.inner_text(TITLE).strip(), 'Deposit Basics')
        page.fill('input[name="name"]', 'Big FD')
        page.fill('input[name="principal"]', '100000')
        page.fill('input[name="annual_rate"]', '7')
        self.next(page)
        self.assertEqual(page.inner_text(TITLE).strip(), 'Deposit Terms')

    def test_a_finished_step_shows_a_tick_and_the_current_one_is_marked(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        page.fill('input[name="name"]', 'Big FD')
        page.fill('input[name="principal"]', '100000')
        page.fill('input[name="annual_rate"]', '7')
        self.next(page)
        first, second = page.locator(DOTS).nth(0), page.locator(DOTS).nth(1)
        self.assertIn('completed', first.get_attribute('class'))
        self.assertTrue(first.locator('.bi-check-lg').is_visible())
        self.assertIn('active', second.get_attribute('class'))
        self.assertEqual(second.get_attribute('aria-current'), 'step')
        self.assertEqual(second.inner_text().strip(), '2')

    def test_a_finished_circle_goes_back_and_a_later_one_does_not_jump_ahead(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        page.fill('input[name="name"]', 'Big FD')
        page.fill('input[name="principal"]', '100000')
        page.fill('input[name="annual_rate"]', '7')
        self.next(page)
        self.assertTrue(page.locator(DOTS).nth(3).is_disabled())
        page.locator(DOTS).nth(3).click(force=True)
        page.wait_for_timeout(300)
        self.assertEqual(self.counter(page)[0], 2)
        page.locator(DOTS).nth(0).tap()
        page.wait_for_timeout(300)
        self.assertEqual(page.inner_text(TITLE).strip(), 'Deposit Basics')

    def test_the_last_circle_is_the_review(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        self.assertEqual(page.locator(DOTS).last.get_attribute('aria-label'), 'Review')

    def test_the_circles_fit_inside_the_screen(self):
        page = self.new_page(self.user, phone=True)
        self.open_flow(page)
        box = page.locator('.wizard-dots-row').bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], page.viewport_size['width'])
        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > document.documentElement.clientWidth'))

    def test_every_setup_shows_as_many_circles_as_its_steps(self):
        from expenses.flows.registry import FlowRegistry
        for flow in FlowRegistry.all().values():
            if not flow.is_available(self.user):
                continue
            page = self.new_page(self.user, phone=True)
            self.open_flow(page, flow.key)
            self.assertEqual(page.locator(DOTS).count(), self.counter(page)[1], flow.key)
            self.assertTrue(page.inner_text(TITLE).strip(), flow.key)


class LargerScreenTests(Base):
    def test_a_computer_keeps_the_tabs(self):
        page = self.new_page(self.user)
        self.open_flow(page)
        self.assertTrue(page.locator('.wizard-steps-tabs-row').is_visible())
        self.assertFalse(page.locator('.wizard-dots').is_visible())

    def test_a_tablet_keeps_the_tabs(self):
        page = self.new_page(self.user)
        page.set_viewport_size({'width': 700, 'height': 900})
        self.open_flow(page)
        self.assertTrue(page.locator('.wizard-steps-tabs-row').is_visible())
        self.assertFalse(page.locator('.wizard-dots').is_visible())
