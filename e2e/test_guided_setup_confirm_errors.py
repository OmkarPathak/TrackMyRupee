"""Confirming a Guided Setup that has a problem on an earlier step takes the person back to that step and says what is
wrong, instead of doing nothing."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import AMFIScheme
        self.user = make_user('confirmerrors')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)
        AMFIScheme.objects.create(scheme_code='120716', scheme_name='UTI Nifty 50 Index Fund - Direct Plan - Growth')

    def next(self, page):
        page.locator('#tmr-flow-form-sip button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(350)

    def sip_with_funds_adding_up_to_more(self, page):
        """A 15,000 SIP whose one fund is given 20,000, taken all the way to the review."""
        self.open(page, reverse('flow-detail', kwargs={'key': 'sip'}))
        page.fill('input[name="name"]', 'Index SIP')
        page.click('input[name="amount"]')
        page.keyboard.type('15000', delay=10)
        self.next(page)
        page.wait_for_selector('#flowSplitAdd')
        page.click('#flowSplitAdd')
        row = page.locator('#flowSplitRows .split-row').first
        row.locator('[data-f=name]').fill('uti nifty 50')
        row.locator('[data-f=suggest] a').first.click()
        row.locator('[data-f=amount]').click()
        page.keyboard.type('20000', delay=10)
        while page.locator('[data-step-key="review"]').is_hidden():
            self.next(page)
        page.wait_for_selector('[data-confirm-btn] >> visible=true')

    def confirm(self, page):
        page.locator('[data-confirm-btn] >> visible=true').first.click()

    def current_step_key(self, page):
        return page.locator('.wizard-step-content:visible').first.get_attribute('data-step-key')


class SipFundsTests(Base):
    def test_funds_adding_up_to_more_than_the_instalment_take_the_person_back_to_the_funds(self):
        page = self.new_page(self.user)
        self.sip_with_funds_adding_up_to_more(page)
        self.confirm(page)
        self.wait_until(lambda: self.current_step_key(page) != 'review', 'still on the review', seconds=15)
        self.assertIn('more than', page.inner_text('#field-errors-fund_splits').lower())
        self.assertTrue(page.locator('#field-errors-fund_splits').is_visible())

    def test_nothing_is_created(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.sip_with_funds_adding_up_to_more(page)
        before = Account.objects.filter(user=self.user).count()
        self.confirm(page)
        page.wait_for_timeout(1500)
        self.assertEqual(Account.objects.filter(user=self.user).count(), before)

    def test_it_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.sip_with_funds_adding_up_to_more(page)
        self.confirm(page)
        self.wait_until(lambda: self.current_step_key(page) != 'review', 'still on the review', seconds=15)
        self.assertTrue(page.locator('#field-errors-fund_splits').is_visible())


class ConfirmButtonTests(Base):
    def test_the_confirm_button_goes_back_to_its_icon_after_a_confirm_that_found_a_problem(self):
        page = self.new_page(self.user)
        self.sip_with_funds_adding_up_to_more(page)
        self.confirm(page)
        self.wait_until(lambda: self.current_step_key(page) != 'review', 'still on the review', seconds=15)
        classes = page.evaluate("""[...document.querySelectorAll('[data-confirm-btn] i')].map((i) => i.className)""")
        self.assertTrue(classes)
        for name in classes:
            self.assertNotIn('bi-check-circle-fill', name)
