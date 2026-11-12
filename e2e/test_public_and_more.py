"""What a visitor sees, signing out, adding income, the calendar, and the first-visit tour."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user

# (url name, text that must be on the page)
PUBLIC_PAGES = [
    ('landing', 'TrackMyRupee'),
    ('features', 'Features'),
    ('pricing', 'Pro'),
    ('about', 'About'),
    ('contact', 'Contact'),
    ('privacy-policy', 'Privacy'),
    ('terms-of-service', 'Terms'),
    ('alternatives', 'TrackMyRupee'),
    ('loan-emi-calculator', 'EMI'),
    ('account_login', 'sign in'),
    ('account_signup', 'account'),
]


class PublicPagesTests(E2ETestCase):
    def crawl(self, phone):
        page = self.new_page(phone=phone)                         # (not signed in)
        for name, text in PUBLIC_PAGES:
            with self.subTest(page=name, phone=phone):
                self.open(page, reverse(name))
                self.assertIn(text.lower(), page.inner_text('body').lower(), name)
                self.assertNotIn('Server Error', page.inner_text('body'))

    def test_the_public_pages_open_on_a_computer(self):
        self.crawl(phone=False)

    def test_the_public_pages_open_on_a_phone(self):
        self.crawl(phone=True)

    def test_the_landing_page_leads_to_sign_up_and_sign_in(self):
        page = self.new_page()
        self.open(page, reverse('landing'))
        page.locator('a[href*="/accounts/signup/"] >> visible=true').first.click()
        page.wait_for_url('**/accounts/signup/**')
        self.open(page, reverse('landing'))
        page.locator('a[href*="/accounts/login/"] >> visible=true').first.click()
        page.wait_for_url('**/accounts/login/**')


class SignOutTests(E2ETestCase):
    def test_signing_out_ends_the_session(self):
        user = make_user('leaver')
        page = self.new_page(user)
        self.open(page, reverse('expense-list'))
        self.assertIn('Expenses', page.inner_text('body'))
        self.open(page, reverse('account_logout'))
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/accounts/logout' not in url)
        page.goto(self.live_server_url + reverse('expense-list'))
        self.assertIn('/accounts/login', page.url)                # a signed-out person is asked to sign in again


class IncomeTests(E2ETestCase):
    def test_income_is_added_from_the_form_and_the_balance_goes_up(self):
        from expenses.models import Account, Income
        user = make_user('earner')
        bank = make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 1000)
        page = self.new_page(user)
        self.open(page, reverse('income-create'))
        page.fill('input[name="amount"]', '85000')
        self.set_select(page, 'select[name="source_type"]', 'Salary')
        self.set_select(page, 'select[name="account"]', str(bank.pk))
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/income/add' not in url)
        income = Income.objects.get(user=user)
        self.assertEqual((income.amount, income.source_type), (Decimal('85000.00'), 'Salary'))
        self.assertEqual(Account.objects.get(pk=bank.pk).balance, Decimal('86000.00'))


class CalendarTests(E2ETestCase):
    def test_tapping_a_day_shows_what_happened_on_it(self):
        from expenses.models import Expense
        user = make_user('calendar-fan')
        bank = make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 5000)
        today = timezone.localdate()
        Expense.objects.create(user=user, category='Food', amount=Decimal('275'), base_amount=Decimal('275'), currency='₹',
                               date=today, account=bank, description='Filter coffee')
        page = self.new_page(user)
        self.open(page, reverse('calendar'))
        page.click(f'[data-date="{today.isoformat()}"]')
        page.wait_for_selector('text=Filter coffee')


class TourTests(E2ETestCase):
    def test_the_first_visit_tour_starts_and_can_be_finished(self):
        from expenses.models import UserProfile
        user = make_user('tourist', tour_pending=True)
        make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 5000)
        page = self.new_page(user)
        self.open(page, reverse('home'))
        # the tour library comes from a CDN: without a network there is nothing to test
        if not page.evaluate("() => new Promise(done => { let n = 0; const t = setInterval(() => { "
                             "if (window.driver || ++n > 40) { clearInterval(t); done(!!window.driver); } }, 100); })"):
            self.skipTest('driver.js could not be loaded (no network): the tour cannot run here')
        page.wait_for_selector('.driver-popover')
        for _step in range(15):                                   # walk to the end
            done = page.locator('.driver-popover-next-btn:has-text("Done")')
            if done.count():
                done.click()
                break
            page.click('.driver-popover-next-btn')
            page.wait_for_timeout(300)
        page.wait_for_selector('.driver-popover', state='detached')
        self.wait_until(lambda: not UserProfile.objects.get(user=user).app_tour_pending, 'the tour was not marked as seen')
        self.assertTrue(User.objects.filter(pk=user.pk).exists())
