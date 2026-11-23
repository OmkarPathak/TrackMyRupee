"""The summary report, on a computer, on a phone and as it prints, and the figures at the top of All Transactions on a phone."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Holding, Valuation
        self.user = make_user('report')
        make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 250000)
        funds = make_account(self.user, 'Mutual Funds', 'MUTUAL_FUND', 0)
        holding = Holding.objects.create(account=funds, instrument_name='Winner Fund', units=Decimal('10'),
                                         avg_cost=Decimal('100'), instrument_type='MF', currency='₹')
        Valuation.objects.create(holding=holding, value=Decimal('1200'), unit_nav=Decimal('120'), as_of_date=timezone.localdate())
        make_account(self.user, 'Visa', 'CREDIT_CARD', -20000)


class DesktopTests(Base):
    def test_the_report_opens_from_the_accounts_page(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        page.click('a[href="%s"]' % reverse('portfolio-summary'))
        page.wait_for_selector('#portfolio-summary')
        self.assertIn('Winner Fund', page.inner_text('#portfolio-summary'))
        self.assertIn('Visa', page.inner_text('#portfolio-summary'))

    def test_the_print_button_opens_the_print_dialog(self):
        page = self.new_page(self.user)
        self.open(page, reverse('portfolio-summary'))
        page.evaluate("(() => { window.__printed = 0; window.print = () => { window.__printed += 1; }; })()")
        page.click('button:has-text("Print / Save as PDF")')
        self.assertEqual(page.evaluate('window.__printed'), 1)

    def test_when_printing_only_the_report_is_left(self):
        page = self.new_page(self.user)
        self.open(page, reverse('portfolio-summary'))
        page.emulate_media(media='print')
        for selector in ('#sidebar', 'nav.navbar', '.psr-actions'):
            self.assertFalse(page.locator(selector).first.is_visible(), selector)
        self.assertTrue(page.locator('#portfolio-summary').is_visible())

    def test_the_two_columns_sit_side_by_side(self):
        page = self.new_page(self.user)
        self.open(page, reverse('portfolio-summary'))
        left = page.locator('[data-psr-block]').first.bounding_box()
        right = page.locator('[data-psr-liabilities]').bounding_box()
        self.assertGreater(right['x'], left['x'] + left['width'] - 5)
        self.assertLess(abs(right['y'] - left['y']), 40)


class PhoneTests(Base):
    def test_the_report_is_one_column_and_does_not_scroll_sideways(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('portfolio-summary'))
        left = page.locator('[data-psr-block]').first.bounding_box()
        right = page.locator('[data-psr-liabilities]').bounding_box()
        self.assertGreater(right['y'], left['y'] + left['height'] - 5)
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), page.viewport_size['width'] + 1)

    def test_all_transactions_shows_its_figures_two_to_a_row(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('all-transactions'))
        cards = page.locator('.tmr-stat-card:visible')
        self.assertEqual(cards.count(), 6)
        boxes = [cards.nth(i).bounding_box() for i in range(6)]
        self.assertEqual(boxes[0]['y'], boxes[1]['y'])
        self.assertLess(boxes[0]['x'], boxes[1]['x'])
        self.assertGreater(boxes[2]['y'], boxes[0]['y'])
        self.assertEqual(boxes[2]['x'], boxes[0]['x'])

    def test_the_figures_take_less_than_40_percent_of_the_screen(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('all-transactions'))
        cards = page.locator('.tmr-stat-card:visible')
        first, last = cards.nth(0).bounding_box(), cards.nth(5).bounding_box()
        height = last['y'] + last['height'] - first['y']
        self.assertLess(height, page.viewport_size['height'] * 0.40, f'{height:.0f}px of {page.viewport_size["height"]}px')

    def test_they_stay_under_40_percent_on_a_small_phone_too(self):
        page = self.new_page(self.user, phone=True)
        page.set_viewport_size({'width': 360, 'height': 640})
        self.open(page, reverse('all-transactions'))
        cards = page.locator('.tmr-stat-card:visible')
        first, last = cards.nth(0).bounding_box(), cards.nth(5).bounding_box()
        self.assertLess(last['y'] + last['height'] - first['y'], 640 * 0.40)


class PrintTests(Base):
    LUMINANCE = """(el) => { const [r, g, b] = getComputedStyle(el).color.match(/\\d+/g).map(Number); return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255; }"""

    def printed_in_dark_mode(self):
        page = self.new_page(self.user)
        self.open(page, reverse('portfolio-summary'))
        page.evaluate("document.documentElement.setAttribute('data-bs-theme', 'dark')")
        page.emulate_media(media='print')
        return page

    def test_the_text_is_dark_on_the_white_page_even_when_the_app_is_in_dark_mode(self):
        page = self.printed_in_dark_mode()
        for selector in ('.psr-legend b', '.psr-block-meta b', '.psr-stat-value', '.psr-name', '.psr-big'):
            self.assertLess(page.eval_on_selector(selector, self.LUMINANCE), 0.45, selector)

    def test_the_gain_and_loss_colours_are_the_dark_print_ones(self):
        page = self.printed_in_dark_mode()
        self.assertLess(page.eval_on_selector('.psr-up', self.LUMINANCE), 0.45)

    def test_the_total_rule_is_dark(self):
        page = self.printed_in_dark_mode()
        self.assertTrue(page.locator('#portfolio-summary').is_visible())
        self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('#portfolio-summary')).backgroundColor"),
                         'rgb(255, 255, 255)')

    def test_a_long_name_wraps_instead_of_running_off_the_page(self):
        from expenses.models import Holding
        funds = self.user.accounts.get(name='Mutual Funds')
        Holding.objects.create(account=funds, instrument_name='Supercalifragilisticexpialidocious' * 3, units=Decimal('1'),
                               avg_cost=Decimal('100'), instrument_type='MF', currency='₹')
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('portfolio-summary'))
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), page.viewport_size['width'] + 1)


class AmountStyleTests(Base):
    """The figures at the top of All Transactions are written like the amounts on the Accounts page."""
    STYLE = """(el) => { const c = getComputedStyle(el); return { family: c.fontFamily, weight: c.fontWeight, numeric: c.fontVariantNumeric,
        spacing: parseFloat(c.letterSpacing) / parseFloat(c.fontSize) }; }"""

    def style_of(self, page, url_name, selector):
        self.open(page, reverse(url_name))
        return page.eval_on_selector(selector, self.STYLE)

    def test_a_figure_is_set_like_an_amount_on_the_accounts_page(self):
        page = self.new_page(self.user, phone=True)
        mine = self.style_of(page, 'all-transactions', '.tmr-stat-value')
        theirs = self.style_of(page, 'account-list', '.acx-tile-amount')
        self.assertEqual((mine['family'], mine['weight'], mine['numeric']), (theirs['family'], theirs['weight'], theirs['numeric']))
        self.assertAlmostEqual(mine['spacing'], theirs['spacing'], places=2)
