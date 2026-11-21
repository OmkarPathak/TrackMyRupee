"""On a phone the Guided Setup's number fields bring up a number pad."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('phonewiz')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def open_flow(self, key='fd'):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': key}))
        page.wait_for_selector('#stepCounterBadge')
        return page

    def box(self, page, selector):
        return page.locator(selector).first.bounding_box()


class KeyboardTests(Base):
    def mode(self, page, name):
        return page.get_attribute(f'input[name="{name}"]', 'inputmode')

    def test_a_rate_gets_the_pad_with_a_point(self):
        page = self.open_flow('fd')
        self.assertEqual(self.mode(page, 'annual_rate'), 'decimal')

    def test_a_whole_number_gets_the_digit_pad(self):
        page = self.open_flow('loan')
        self.assertEqual(self.mode(page, 'tenure_months'), 'numeric')

    def test_an_amount_gets_the_pad_with_a_point(self):
        page = self.open_flow('fd')
        self.assertEqual(self.mode(page, 'principal'), 'decimal')

    def test_a_text_field_keeps_the_letter_keyboard(self):
        page = self.open_flow('fd')
        self.assertIsNone(self.mode(page, 'name'))
