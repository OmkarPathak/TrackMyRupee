"""The time-period button says the chosen period in the person's language however it got there: from the address, from the menu,
or after the page restores its state."""
from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

KEYS = ('today', 'this_month', 'last_month', 'last_3_months', 'this_year', 'all')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        from expenses.models import UserProfile
        self.users = {}
        for language in ('en', 'hi', 'mr'):          # (the app follows the profile's language, and remembers it, so one person each)
            user = make_user(f'labels{language}')
            make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 1000)
            UserProfile.objects.filter(user=user).update(language=language)
            self.users[language] = user

    def page_in(self, language):
        return self.new_page(self.users[language])

    def button(self, page):
        return page.inner_text('.tmr-time-label').strip()

    def menu_label(self, page, key):
        return page.inner_text(f'[data-time-key="{key}"] .tmr-item-label').strip()

    def wait_for_the_list(self, page):
        page.wait_for_timeout(900)


class MenuTests(Base):
    def test_choosing_a_period_writes_it_in_the_menus_own_words(self):
        for language in ('en', 'hi', 'mr'):
            for key in KEYS:
                page = self.page_in(language)
                self.open(page, reverse('all-transactions'))
                page.click('#tmr-time-dropdown-btn')
                words = self.menu_label(page, key)
                page.click(f'[data-time-key="{key}"]')
                self.wait_for_the_list(page)
                self.assertEqual(self.button(page), words, f'{language} {key}')
                page.close()


class AddressTests(Base):
    def test_a_period_in_the_address_is_written_in_the_menus_own_words(self):
        for language in ('en', 'hi', 'mr'):
            for key in KEYS:
                page = self.page_in(language)
                self.open(page, reverse('all-transactions') + f'?time_period={key}')
                page.wait_for_timeout(500)
                page.click('#tmr-time-dropdown-btn')
                words = self.menu_label(page, key)
                self.assertEqual(self.button(page), words, f'{language} {key}')
                page.close()

    def test_the_periods_the_menu_does_not_offer_still_have_a_label(self):
        for key in ('last_6_months', 'calendar_last_month'):
            page = self.page_in('en')
            self.open(page, reverse('all-transactions') + f'?time_period={key}')
            page.wait_for_timeout(500)
            self.assertNotEqual(self.button(page), '', key)
            page.close()
