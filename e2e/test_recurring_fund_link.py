"""The funds of a recurring transfer open from a "3 funds" link: a modal on a computer, a bottom sheet on a phone."""
from datetime import date
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

MODAL = '#fundSplitsModal.show'
SHEET = '#fundSplitsSheet.show'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses import holdings_service as hs
        from expenses.models import Account, RecurringFundSplit, RecurringTransaction
        self.user = make_user('fund-link')
        self.bank = make_account(self.user, 'HDFC Bank', 'SAVINGS_ACCOUNT', 900000)
        self.account = Account.objects.create(user=self.user, name='Mutual Funds', account_type='MUTUAL_FUND',
                                              balance=Decimal('0'), currency='₹')
        alpha = hs.create_holding(self.account, name='Alpha Small Cap Fund', units='10', price='40', scheme_code='111')
        beta = hs.create_holding(self.account, name='Beta ELSS Tax Saver Fund', units='10', price='40', scheme_code='222')
        gamma = hs.create_holding(self.account, name='Gamma Large and Midcap Fund', units='10', price='40', scheme_code='333')

        def schedule(name, amount, splits):
            rt = RecurringTransaction.objects.create(
                user=self.user, transaction_type='TRANSFER', amount=Decimal(amount), currency='₹', description=name,
                frequency='MONTHLY', start_date=date(2026, 8, 5), from_account=self.bank, to_account=self.account,
                is_active=True)
            for holding, part in splits:
                RecurringFundSplit.objects.create(recurring=rt, holding=holding, amount=Decimal(part))
            return rt
        self.sip = schedule('Monthly SIP', '45000', [(alpha, '15000'), (beta, '15000'), (gamma, '15000')])
        self.small = schedule('Index SIP', '5000', [(alpha, '5000')])

    def open_list(self, page):
        self.open(page, reverse('recurring-list'))


class ComputerTests(Base):
    def test_the_link_opens_a_modal_with_the_funds(self):
        page = self.new_page(self.user)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').click()
        page.wait_for_selector(MODAL)
        text = page.inner_text(MODAL)
        for name in ('Monthly SIP', 'Alpha Small Cap Fund', 'Beta ELSS Tax Saver Fund', 'Gamma Large and Midcap Fund',
                     '₹45,000', 'From HDFC Bank to Mutual Funds'):
            self.assertIn(name, text)
        self.assertEqual(text.count('₹15,000'), 3)
        self.assertEqual(page.locator(SHEET).count(), 0)

    def test_the_modal_closes(self):
        page = self.new_page(self.user)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').click()
        page.wait_for_selector(MODAL)
        page.wait_for_timeout(500)                         # (it slides in first; it takes Escape once it has settled)
        page.keyboard.press('Escape')
        page.wait_for_selector(MODAL, state='detached')

    def test_each_schedule_opens_its_own_funds(self):
        page = self.new_page(self.user)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.small.pk}"] >> visible=true').click()
        page.wait_for_selector(MODAL)
        text = page.inner_text(MODAL)
        self.assertIn('Index SIP', text)
        self.assertNotIn('Beta ELSS', text)
        page.wait_for_timeout(500)                         # (it slides in first; it takes Escape once it has settled)
        page.keyboard.press('Escape')
        page.wait_for_selector(MODAL, state='detached')
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').click()
        page.wait_for_selector(MODAL)
        self.assertIn('Beta ELSS', page.inner_text(MODAL))

    def test_the_type_column_no_longer_lists_the_fund_names(self):
        page = self.new_page(self.user)
        self.open_list(page)
        row = page.locator('tr', has_text='Monthly SIP').first
        self.assertIn('3 funds', row.inner_text())
        self.assertNotIn('Alpha Small Cap Fund', row.inner_text())

    def test_change_funds_goes_to_the_edit_form(self):
        page = self.new_page(self.user)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').click()
        page.wait_for_selector(MODAL)
        page.click(f'{MODAL} a:has-text("Change funds")')
        page.wait_for_url(lambda url: f'/recurring/{self.sip.uuid}/edit/' in url)

    def test_the_row_actions_menu_still_works(self):
        page = self.new_page(self.user)
        self.open_list(page)
        row = page.locator('tr', has_text='Monthly SIP').first
        row.locator('button[aria-label="Subscription actions"]').click()
        self.assertTrue(row.locator('.dropdown-menu.show').is_visible())


class PhoneTests(Base):
    def test_the_link_is_on_the_card_and_opens_a_bottom_sheet(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').tap()
        page.wait_for_selector(SHEET)
        text = page.inner_text(SHEET)
        for name in ('Monthly SIP', 'Alpha Small Cap Fund', 'Beta ELSS Tax Saver Fund', '₹45,000'):
            self.assertIn(name, text)
        self.assertEqual(page.locator(MODAL).count(), 0)

    def test_the_sheet_sits_inside_the_screen(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true').tap()
        page.wait_for_selector(SHEET)
        page.wait_for_timeout(500)                         # (its slide-in)
        box = page.locator(SHEET).bounding_box()
        viewport = page.viewport_size
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], viewport['width'])
        self.assertLessEqual(box['y'] + box['height'], viewport['height'])

    def test_the_sheet_closes_with_its_button_and_with_the_backdrop(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        link = page.locator(f'[data-fund-open="{self.sip.pk}"] >> visible=true')
        link.tap()
        page.wait_for_selector(SHEET)
        page.wait_for_timeout(400)
        page.tap(f'{SHEET} .btn-close-drawer')
        page.wait_for_selector(SHEET, state='detached')
        link.tap()
        page.wait_for_selector(SHEET)
        page.wait_for_timeout(400)
        page.mouse.click(190, 80)                          # the dimmed page above the sheet
        page.wait_for_selector(SHEET, state='detached')

    def test_the_card_shows_the_count_and_not_the_names(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        card = page.locator('.expense-card', has_text='Monthly SIP').first
        self.assertIn('3 funds', card.inner_text())
        self.assertNotIn('Alpha Small Cap Fund', card.inner_text())
