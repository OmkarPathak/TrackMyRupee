"""The Investments page: the tiles, allocation bar and market holdings, the print report link, and the phone layout."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Holding, RecurringTransaction, Valuation
        self.user = make_user('investor')
        today = timezone.localdate()
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)
        self.funds = make_account(self.user, 'Mutual Funds', 'MUTUAL_FUND', 0)
        holding = Holding.objects.create(account=self.funds, instrument_name='Winner Fund', units=D('100'), avg_cost=D('100'),
                                         instrument_type='MF', currency='₹')
        Valuation.objects.create(holding=holding, value=D('12000'), unit_nav=D('120'), as_of_date=today)
        make_account(self.user, 'Bank FD', 'FD', 100000, deposit_principal=D('100000'), deposit_rate=D('7'),
                     deposit_start_date=today - timedelta(days=100), deposit_maturity_date=today + timedelta(days=265))
        RecurringTransaction.objects.create(user=self.user, transaction_type='TRANSFER', amount=D('5000'), currency='₹',
                                            base_amount=D('5000'), description='SIP', frequency='MONTHLY', start_date=today,
                                            from_account=self.bank, to_account=self.funds)

    def open_page(self, phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('holding-list'))
        page.wait_for_selector('#investments-overview', state='attached')
        return page


class DesktopTests(Base):
    def test_the_page_has_its_title_and_four_tiles(self):
        page = self.open_page()
        self.assertEqual(page.inner_text('h1').strip(), 'Investments')
        self.assertEqual(page.locator('#investments-overview [data-iv]').count(), 4)
        self.assertEqual(page.inner_text('[data-iv=sip]').strip(), '₹5,000')
        self.assertFalse(page.is_visible('#investments-overview-phone'))

    def test_the_tiles_match_the_printed_report(self):
        page = self.open_page()
        figures = [page.inner_text(f'[data-iv={key}]').strip() for key in ('invested', 'sip')]
        gain = page.inner_text('[data-iv=gain]').strip()
        page.click('#investments-print-report')
        page.wait_for_selector('#portfolio-summary')
        report = page.inner_text('#portfolio-summary')
        for figure in figures + [gain.lstrip('+')]:
            self.assertIn(figure, report)

    def test_the_allocation_bar_lists_its_groups(self):
        page = self.open_page()
        text = page.inner_text('#investments-allocation')
        self.assertIn('Where it is', text)
        self.assertGreaterEqual(page.locator('#investments-allocation .iv-bar span').count(), 2)

    def test_the_market_holdings_are_still_listed(self):
        page = self.open_page()
        self.assertIn('Winner Fund', page.inner_text('body'))

    def test_the_sidebar_says_investments(self):
        page = self.open_page()
        self.assertGreaterEqual(page.locator('.nav-text', has_text='Investments').count(), 1)
        self.assertEqual(page.locator('.nav-text', has_text='Holdings').count(), 0)


class PhoneTests(Base):
    def test_the_summary_is_four_compact_tiles_two_to_a_row(self):
        page = self.open_page(phone=True)
        self.assertFalse(page.is_visible('#investments-overview'))
        tiles = page.locator('#investments-overview-phone .tmr-stat-card')
        self.assertEqual(tiles.count(), 4)
        boxes = [tiles.nth(i).bounding_box() for i in range(4)]
        self.assertAlmostEqual(boxes[0]['y'], boxes[1]['y'], delta=2)         # two to a row
        self.assertAlmostEqual(boxes[2]['y'], boxes[3]['y'], delta=2)
        self.assertLess(boxes[3]['y'] + boxes[3]['height'] - boxes[0]['y'], 260)      # the whole block stays short

    def test_coming_up_keeps_every_amount_inside_the_card_and_right_aligned(self):
        make_account(self.user, 'A very long deposit account name for the house down payment', 'FD', 412700,
                     deposit_maturity_date=timezone.localdate() + timedelta(days=40))
        page = self.open_page(phone=True)
        card = page.locator('#investments-upcoming').bounding_box()
        amounts = page.locator('#investments-upcoming .iv-up-amt')
        self.assertGreaterEqual(amounts.count(), 2)
        rights = []
        for i in range(amounts.count()):
            box = amounts.nth(i).bounding_box()
            self.assertLessEqual(box['x'] + box['width'], card['x'] + card['width'] + 1)
            rights.append(round(box['x'] + box['width']))
        self.assertEqual(len(set(rights)), 1)         # one right edge for all of them

    def test_the_page_fits_a_phone_without_sideways_scroll(self):
        page = self.open_page(phone=True)
        self.assertEqual(page.inner_text('h1').strip(), 'Investments')
        self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), True)

    def test_the_more_menu_says_investments(self):
        page = self.open_page(phone=True)
        page.click('.mobile-nav-item-more')
        page.wait_for_selector('#moreMenuSheet .mmt-label', state='visible')
        self.assertIn('Investments', page.inner_text('#moreMenuSheet'))
        self.assertNotIn('Holdings', page.inner_text('#moreMenuSheet'))


class TabsTests(Base):
    def setUp(self):
        super().setUp()
        today = timezone.localdate()
        make_account(self.user, 'PPF', 'PPF', 50000)
        make_account(self.user, 'Post Office RD', 'RD', 20000, deposit_rate=D('6.7'), deposit_maturity_date=today + timedelta(days=30))

    def go(self, page, tab):
        page.click(f'#iv-tab-{tab}')
        page.wait_for_selector(f'#iv-pane-{tab}.active.show')

    def test_the_market_tab_is_open_first(self):
        page = self.open_page()
        self.assertTrue(page.is_visible('#iv-pane-market'))
        self.assertFalse(page.is_visible('#iv-pane-deposits'))

    def test_the_deposits_tab_shows_the_deposits(self):
        page = self.open_page()
        self.go(page, 'deposits')
        text = page.inner_text('#iv-pane-deposits')
        self.assertIn('Bank FD', text)
        self.assertIn('Post Office RD', text)
        self.assertIn('7%', text)
        self.assertNotIn('PPF', text)

    def test_the_schemes_tab_shows_the_schemes(self):
        page = self.open_page()
        self.go(page, 'schemes')
        text = page.inner_text('#iv-pane-schemes')
        self.assertIn('PPF', text)
        self.assertNotIn('Bank FD', text)

    def test_the_open_tab_is_kept_in_the_address_and_comes_back_on_reload(self):
        page = self.open_page()
        self.go(page, 'deposits')
        self.assertTrue(page.url.endswith('#deposits'))
        page.reload()
        page.wait_for_selector('#iv-pane-deposits.active.show')

    def test_an_address_with_a_tab_opens_that_tab(self):
        page = self.new_page(self.user)
        self.open(page, reverse('holding-list') + '#schemes')
        page.wait_for_selector('#iv-pane-schemes.active.show')

    def test_a_deposit_row_opens_its_account(self):
        page = self.open_page()
        self.go(page, 'deposits')
        page.click('#iv-deposits-table >> text=Bank FD')
        page.wait_for_url('**/accounts/**')

    def test_coming_up_lists_the_next_maturities(self):
        page = self.open_page()
        text = page.inner_text('#investments-upcoming')
        self.assertIn('Matures', text)
        self.assertLess(text.index('Post Office RD'), text.index('Bank FD'))

    def test_an_empty_tab_invites_adding_one(self):
        from expenses.models import Account
        Account.objects.filter(user=self.user, account_type__in=['FD', 'RD']).delete()
        page = self.open_page()
        self.go(page, 'deposits')
        self.assertTrue(page.is_visible('#iv-deposits-empty'))


class PhoneTabsTests(Base):
    def test_deposits_are_shown_as_cards_on_a_phone(self):
        page = self.open_page(phone=True)
        page.click('#iv-tab-deposits')
        page.wait_for_selector('#iv-pane-deposits.active.show')
        self.assertTrue(page.is_visible('#iv-deposits-cards'))
        self.assertFalse(page.is_visible('#iv-deposits-table'))
        self.assertIn('Bank FD', page.inner_text('#iv-deposits-cards'))
        self.assertEqual(page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), True)
