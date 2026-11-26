"""Available cash: what can be spent today. Shown on the Accounts page and the Dashboard, lowered when money moves into an
investment (net worth is not), and kept out for an account the person sets aside."""
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

D = Decimal


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('spender-e2e')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 300000)
        self.cash = make_account(self.user, 'Cash', 'CASH_WALLET', 1000)
        self.fd = make_account(self.user, 'HDFC FD', 'FD', 100000)

    def accounts(self, phone=False):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('account-list'))
        page.wait_for_selector('#acx-available-cash')
        return page

    def figures(self, page):
        available = D(page.inner_text('#acx-available-cash strong').replace('₹', '').replace(',', '').replace('−', '-'))
        worth = D(page.inner_text('.acx-nw-amount').replace('₹', '').replace(',', '').replace('−', '-'))
        return available, worth


class ShownTests(Base):
    def test_the_accounts_page_shows_only_cash_and_bank_money(self):
        page = self.accounts()
        available, worth = self.figures(page)
        self.assertEqual(available, D('301000'))                    # 3,00,000 + 1,000 (the FD is not spendable)
        self.assertEqual(worth, D('401000'))
        self.assertIn('Available cash', page.inner_text('#acx-available-cash'))

    def test_the_dashboard_shows_it_under_net_worth(self):
        page = self.new_page(self.user)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        text = page.inner_text('#nw-available-cash')
        self.assertIn('Available cash', text)
        self.assertIn('₹3,01,000', text)

    def test_it_is_visible_on_a_phone(self):
        page = self.accounts(phone=True)
        self.assertTrue(page.is_visible('#acx-available-cash'))
        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - window.innerWidth'), 2)

    def test_the_page_in_hindi_says_it_in_hindi(self):
        user = make_user('spender-hi', language='hi')
        make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 5000)
        page = self.new_page(user)
        self.open(page, reverse('account-list'))
        self.assertIn('उपलब्ध नकद', page.inner_text('#acx-available-cash'))


class CardSizeTests(Base):
    HEIGHTS = "() => Array.from(document.querySelectorAll('.bento-metric-card')).map(c => Math.round(c.getBoundingClientRect().height))"

    def check(self, phone):
        page = self.new_page(self.user, phone=phone)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        page.wait_for_timeout(1000)
        with_line = page.evaluate(self.HEIGHTS)
        # the page as it was before the line: no line, the chart at its old height
        page.evaluate("document.getElementById('nw-available-cash').style.display = 'none';"
                      "document.querySelector('#bento-card-networth .bento-sparkline-wrap').style.height = '48px'")
        page.wait_for_timeout(200)
        before = page.evaluate(self.HEIGHTS)
        self.assertEqual(len(set(with_line)), 1, with_line)                  # the four cards are the same height
        self.assertLessEqual(max(with_line) - max(before), 1, (with_line, before))
        self.assertTrue(page.evaluate("() => { const l = document.getElementById('nw-available-cash'); return l.scrollWidth <= l.clientWidth + 1; }"))

    def test_the_cards_stay_as_short_as_they_were_on_a_computer(self):
        self.check(phone=False)

    def test_the_cards_stay_as_short_as_they_were_on_a_phone(self):
        self.check(phone=True)

    def test_the_line_is_one_line_with_the_short_label_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        self.assertRegex(page.inner_text('#nw-available-cash').strip(), r'^Cash\s+₹3,01,000$')


class MovingMoneyTests(Base):
    def test_money_moved_into_an_investment_leaves_available_cash_and_not_net_worth(self):
        page = self.new_page(self.user)
        self.open(page, reverse('transfer-create'))
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        self.set_select(page, 'select[name="to_account"]', str(self.fd.pk))
        page.fill('input[name="amount"]', '50000')
        page.click('#moveMoneySubmit')
        page.wait_for_url(lambda url: '/transfers/add' not in url)
        available, worth = self.figures(self.accounts())
        self.assertEqual(available, D('251000'))
        self.assertEqual(worth, D('401000'))

    def test_money_moved_between_cash_accounts_changes_nothing(self):
        page = self.new_page(self.user)
        self.open(page, reverse('transfer-create'))
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        self.set_select(page, 'select[name="to_account"]', str(self.cash.pk))
        page.fill('input[name="amount"]', '5000')
        page.click('#moveMoneySubmit')
        page.wait_for_url(lambda url: '/transfers/add' not in url)
        self.assertEqual(self.figures(self.accounts())[0], D('301000'))


class SetAsideTests(Base):
    def edit(self, page, account, *, on):
        self.open(page, reverse('account-edit', kwargs={'pk': account.uuid}))
        page.wait_for_selector('#available-cash-switch', state='visible')
        box = page.locator('#id_include_in_available_cash')
        if box.is_checked() != on:
            box.click(force=True)
        page.click('#nextBtn')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/edit' not in url)

    def test_turning_it_off_for_an_account_takes_it_out_of_available_cash_but_not_net_worth(self):
        page = self.new_page(self.user)
        self.edit(page, self.bank, on=False)
        self.bank.refresh_from_db()
        self.assertFalse(self.bank.include_in_available_cash)
        accounts = self.accounts()
        available, worth = self.figures(accounts)
        self.assertEqual(available, D('1000'))
        self.assertEqual(worth, D('401000'))
        self.assertIn('Not in available cash', accounts.inner_text('body'))

    def test_turning_it_back_on_puts_it_back(self):
        page = self.new_page(self.user)
        self.edit(page, self.bank, on=False)
        self.edit(page, self.bank, on=True)
        accounts = self.accounts()
        self.assertEqual(self.figures(accounts)[0], D('301000'))
        self.assertNotIn('Not in available cash', accounts.inner_text('body'))

    def test_the_dashboard_follows(self):
        page = self.new_page(self.user)
        self.edit(page, self.bank, on=False)
        self.open(page, reverse('home'))
        page.wait_for_selector('#nw-available-cash')
        self.assertNotIn('3,01,000', page.inner_text('#nw-available-cash'))

    def test_the_switch_is_offered_for_a_savings_account_and_not_for_a_deposit(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=SAVINGS_ACCOUNT')
        page.wait_for_selector('#accountBasicsFields', state='visible')
        self.assertTrue(page.locator('#available-cash-switch').is_visible())
        self.open(page, reverse('account-create') + '?type=FD')
        page.wait_for_selector('#accountBasicsFields', state='visible')
        self.assertFalse(page.locator('#available-cash-switch').is_visible())

    def test_a_new_account_can_be_made_with_it_off(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open(page, reverse('account-create'))
        page.click('[data-category="bank"]')
        page.click('#typeChoicesList button:has-text("Savings Account")')
        page.fill('#id_name', 'Emergency fund')
        page.fill('#id_balance', '80000')
        page.wait_for_selector('#available-cash-switch', state='visible')
        page.locator('#id_include_in_available_cash').click(force=True)
        page.click('#nextBtn')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/accounts/add' not in url)
        self.assertFalse(Account.objects.get(name='Emergency fund').include_in_available_cash)
        self.assertEqual(self.figures(self.accounts())[0], D('301000'))
