"""Guided Setup on a phone: a short list of one-tap rows, the done ones folded away; the desktop layout is unchanged."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

CARDS_ABOVE_THE_FOLD = """() => Array.from(document.querySelectorAll('.gcard'))
    .filter(c => c.offsetParent && c.getBoundingClientRect().top < innerHeight - 90).length"""


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('phone-setup')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)


class PhoneTests(Base):
    def test_the_list_is_short_enough_to_see_several_setups_without_scrolling(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        page.wait_for_selector('.gcard')
        self.assertGreaterEqual(page.evaluate(CARDS_ABOVE_THE_FOLD), 4)
        self.assertLess(page.evaluate('document.documentElement.scrollHeight'), 2400)

    def test_tapping_the_row_itself_opens_the_setup(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        box = page.locator('.gcard:not(.gcard--done)').first.locator('p').bounding_box()
        page.mouse.click(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)      # a tap on the text, not a button
        page.wait_for_url('**/flows/salary/')
        self.assertTrue(page.locator('#tmr-flow-form-salary').count())

    def test_the_buttons_tags_and_side_panels_are_hidden_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        self.assertFalse(page.locator('.gcard:not(.gcard--done) .gbtn').first.is_visible())
        self.assertFalse(page.locator('.gcard:not(.gcard--done) .tags').first.is_visible())
        self.assertFalse(page.locator('.tmr-flow-side').is_visible())
        self.assertIn('set up', page.inner_text('.tmr-flow-progress'))

    def test_start_here_marks_three_rows(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        self.assertEqual(page.locator('.gstart:visible').count(), 3)

    def test_done_setups_are_folded_until_the_row_is_tapped(self):
        make_account(self.user, 'Index Funds', 'MUTUAL_FUND', 0)               # the SIP setup is now done
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        self.assertFalse(page.locator('.gcard--done').first.is_visible())
        page.locator('.tmr-flow-done-toggle').click()
        page.locator('.gcard--done').first.wait_for(state='visible')
        self.assertTrue(page.locator('.gcard--done a:has-text("Edit existing")').first.is_visible())
        page.locator('.tmr-flow-done-toggle').click()
        page.locator('.gcard--done').first.wait_for(state='hidden')

    def test_a_filter_chip_still_narrows_the_list(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('tmr-flows'))
        page.locator('.tmr-flow-cat:has-text("Debt")').click()
        page.wait_for_timeout(500)
        shown = page.locator('.gcard:not(.gcard--done):visible').count()
        self.assertTrue(0 < shown < 11)


class DesktopTests(Base):
    def test_the_desktop_page_keeps_its_buttons_tags_panels_and_request_card(self):
        page = self.new_page(self.user)
        self.open(page, reverse('tmr-flows'))
        self.assertTrue(page.locator('.gcard:not(.gcard--done) .gbtn').first.is_visible())
        self.assertTrue(page.locator('.gcard:not(.gcard--done) .tags').first.is_visible())
        self.assertTrue(page.locator('.tmr-flow-side').is_visible())
        self.assertTrue(page.locator('.reqcard').is_visible())
        self.assertFalse(page.locator('.gcard-tap').first.is_visible())
        self.assertFalse(page.locator('.tmr-flow-progress').is_visible())

    def test_the_most_wanted_setups_come_first(self):
        page = self.new_page(self.user)
        self.open(page, reverse('tmr-flows'))
        first = page.locator('.gcard:not(.gcard--done) h3').all_inner_texts()[:3]
        self.assertEqual([t.strip() for t in first], ['I started a new job', 'I took a loan', 'I started a SIP'])

    def test_clicking_a_card_still_previews_it_and_the_button_opens_it(self):
        page = self.new_page(self.user)
        self.open(page, reverse('tmr-flows'))
        page.locator('.gcard:not(.gcard--done)').nth(1).locator('p').click()
        self.assertIn('I took a loan', page.inner_text('#whatCreates'))
        page.locator('.gcard:not(.gcard--done)').nth(1).locator('.gbtn').click()
        page.wait_for_url('**/flows/loan/')
