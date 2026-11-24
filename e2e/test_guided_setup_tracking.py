"""Guided Setup reports which step is showing and where a person left (PostHog, only after analytics consent), so we can
see where people drop out. The events carry the setup's key and the step, never what was typed."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

# (__SV tells the page's own analytics snippet that PostHog is already there, so it leaves this stand-in alone)
STUB = ("window.posthog = {__SV: 1, init() {}, identify() {}, "
        "capture: (name, props) => { (window.__captured = window.__captured || []).push([name, props]); }}")


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('wizardtracker')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def open_flow(self, consent):
        page = self.new_page(self.user)
        page.add_init_script(f"localStorage.setItem('cookieConsent', '{consent}')")
        page.add_init_script(STUB)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.wait_for_selector('#stepCounterBadge')
        return page

    def captured(self, page):
        return page.evaluate('window.__captured || []')


class TrackingTests(Base):
    def test_the_first_step_is_reported_on_opening(self):
        page = self.open_flow('accepted')
        name, props = self.captured(page)[0]
        self.assertEqual(name, 'guided_setup_step_viewed')
        self.assertEqual((props['flow'], props['step']), ('fd', 1))
        self.assertEqual(set(props), {'flow', 'step', 'of', 'step_key'})

    def test_going_to_the_next_step_reports_it(self):
        page = self.open_flow('accepted')
        page.fill('input[name="name"]', 'Tracking FD')
        page.fill('input[name="principal"]', '100000')
        page.fill('input[name="annual_rate"]', '7')
        page.locator('button[\\@click="nextStep(1)"]:visible').first.click()
        steps = [p['step'] for n, p in self.captured(page) if n == 'guided_setup_step_viewed']
        self.assertEqual(steps[:2], [1, 2])

    def test_leaving_without_confirming_reports_the_step(self):
        page = self.open_flow('accepted')
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        name, props = self.captured(page)[-1]
        self.assertEqual((name, props['flow'], props['step']), ('guided_setup_left', 'fd', 1))

    def test_nothing_is_reported_without_consent(self):
        page = self.open_flow('declined')
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        self.assertEqual(self.captured(page), [])
