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
        page.wait_for_selector('#investments-overview')
        return page


class DesktopTests(Base):
    def test_the_page_has_its_title_and_four_tiles(self):
        page = self.open_page()
        self.assertEqual(page.inner_text('h1').strip(), 'Investments')
        self.assertEqual(page.locator('#investments-overview [data-iv]').count(), 4)
        self.assertEqual(page.inner_text('[data-iv=sip]').strip(), '₹5,000')

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
