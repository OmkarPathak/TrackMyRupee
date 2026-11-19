"""A tap or click on a rounded amount shows its exact value in a tooltip."""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

TIP = '.tooltip.tmr-amt-tip.show'


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import Expense
        self.user = make_user('exact')
        self.bank = make_account(self.user, 'Big Bank', 'SAVINGS_ACCOUNT', 900000)
        today = timezone.localdate()
        for amount, description in (('231233', 'Big spend'), ('1250000', 'Huge spend'), ('450', 'Tea')):
            Expense.objects.create(user=self.user, category='Food', amount=Decimal(amount), base_amount=Decimal(amount),
                                   currency='₹', date=today, account=self.bank, description=description)

    def open_list(self, page):
        self.open(page, reverse('all-transactions'))
        page.wait_for_selector('.tmr-amt', state='attached')          # (the first one may be in the hidden table)

    def amount(self, page, text, phone=False):
        """The rounded amount shown as `text` on the list (the row on a computer, the card on a phone)."""
        scope = page.locator('.expense-card' if phone else 'tbody tr')
        return scope.locator('.tmr-amt', has_text=text).first


class ComputerTests(Base):
    def test_a_click_shows_the_exact_amount(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').click()
        page.wait_for_selector(TIP)
        self.assertEqual(page.inner_text(TIP).strip(), '₹2,31,233')

    def test_another_amount_has_its_own_value_and_only_one_tooltip_is_open(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').click()
        page.wait_for_selector(TIP)
        self.amount(page, '12.5L').click()
        page.wait_for_timeout(500)
        self.assertEqual(page.locator(TIP).count(), 1)
        self.assertEqual(page.inner_text(TIP).strip(), '₹12,50,000')

    def test_a_second_click_on_the_same_amount_closes_it(self):
        page = self.new_page(self.user)
        self.open_list(page)
        amount = self.amount(page, '2.3L')
        amount.click()
        page.wait_for_selector(TIP)
        page.wait_for_timeout(400)
        amount.click()
        page.wait_for_selector(TIP, state='detached')

    def test_a_click_elsewhere_closes_it(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').click()
        page.wait_for_selector(TIP)
        page.wait_for_timeout(400)
        page.mouse.click(700, 5)
        page.wait_for_selector(TIP, state='detached')

    def test_escape_closes_it(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').click()
        page.wait_for_selector(TIP)
        page.keyboard.press('Escape')
        page.wait_for_selector(TIP, state='detached')

    def test_it_closes_by_itself_after_a_few_seconds(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').click()
        page.wait_for_selector(TIP)
        page.wait_for_selector(TIP, state='detached', timeout=8000)

    def test_it_works_from_the_keyboard(self):
        page = self.new_page(self.user)
        self.open_list(page)
        self.amount(page, '2.3L').focus()
        page.keyboard.press('Enter')
        page.wait_for_selector(TIP)
        self.assertEqual(page.inner_text(TIP).strip(), '₹2,31,233')

    def test_an_amount_shown_in_full_is_not_clickable(self):
        page = self.new_page(self.user)
        self.open_list(page)
        row = page.locator('tbody tr', has_text='Tea').first
        self.assertEqual(row.locator('.tmr-amt').count(), 0)

    def test_an_amount_inside_a_link_shows_the_value_and_does_not_follow_the_link(self):
        page = self.new_page(self.user)
        self.open_list(page)
        page.evaluate("""() => {
            const a = document.createElement('a');
            a.id = 'probe-link'; a.href = '/definitely-not-a-page/';
            a.innerHTML = '<span class="tmr-amt" data-exact="₹9,99,999" id="probe-amt">10L</span>';
            a.style.cssText = 'position:fixed;top:120px;left:300px;z-index:9999;background:#fff;padding:8px;';
            document.body.appendChild(a);
        }""")
        before = page.url
        page.click('#probe-amt')
        page.wait_for_selector(TIP)
        self.assertEqual(page.url, before)
        self.assertEqual(page.inner_text(TIP).strip(), '₹9,99,999')


class PhoneTests(Base):
    def test_a_tap_shows_the_exact_amount_inside_the_screen(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        self.amount(page, '2.3L', phone=True).tap()
        page.wait_for_selector(TIP)
        self.assertEqual(page.inner_text(TIP).strip(), '₹2,31,233')
        page.wait_for_timeout(300)
        box = page.locator(TIP).bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], page.viewport_size['width'])

    def test_a_tap_elsewhere_closes_it(self):
        page = self.new_page(self.user, phone=True)
        self.open_list(page)
        self.amount(page, '2.3L', phone=True).tap()
        page.wait_for_selector(TIP)
        page.wait_for_timeout(400)
        page.mouse.click(10, 300)
        page.wait_for_selector(TIP, state='detached')


class WholeRupeeTests(E2ETestCase):
    """A whole-rupee amount whose paise are left out shows them in the tooltip too."""

    def setUp(self):
        super().setUp()
        self.user = make_user('paise')
        make_account(self.user, 'Paise Bank', 'SAVINGS_ACCOUNT', Decimal('14791.67'))

    def first_with(self, page, text):
        return page.locator('.tmr-amt:visible', has_text=text).first

    def test_a_balance_with_paise_shows_them_on_click(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-list'))
        page.wait_for_selector('.tmr-amt', state='attached')
        self.first_with(page, '14,792').click()
        page.wait_for_selector(TIP)
        self.assertEqual(page.inner_text(TIP).strip(), '₹14,791.67')

    def test_a_balance_with_paise_shows_them_on_a_tap_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('account-list'))
        page.wait_for_selector('.tmr-amt', state='attached')
        self.first_with(page, '14,792').tap()
        page.wait_for_selector(TIP)
        self.assertEqual(page.inner_text(TIP).strip(), '₹14,791.67')
