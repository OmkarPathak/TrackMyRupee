"""Closing things down and sharing with family: deleting an account, deleting your own account, Family sharing."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class DeleteTests(E2ETestCase):
    def test_deleting_an_account_asks_first_and_then_removes_it(self):
        from expenses.models import Account
        user = make_user('closer')
        make_account(user, 'Main', 'SAVINGS_ACCOUNT', 100)
        spare = make_account(user, 'Old wallet', 'CASH_WALLET', 0)
        page = self.new_page(user)
        self.open(page, reverse('account-delete', kwargs={'pk': spare.uuid}))
        self.assertIn('Old wallet', page.inner_text('body'))          # it names what is about to go
        self.assertTrue(Account.objects.filter(pk=spare.pk, is_active=True).exists())
        page.click('button:has-text("Delete")')
        page.wait_for_url(lambda url: '/delete' not in url)
        self.assertFalse(Account.objects.filter(pk=spare.pk, is_active=True).exists())

    @override_settings(ACCOUNT_DELETION_ASYNC=False)
    def test_deleting_your_own_account_removes_you_and_your_data_and_signs_you_out(self):
        from expenses.models import Account, Expense
        user = make_user('leaving')
        bank = make_account(user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 5000)
        Expense.objects.create(user=user, category='Food', amount=Decimal('40'), base_amount=Decimal('40'), currency='₹',
                               date=timezone.localdate(), account=bank, description='Tea')
        page = self.new_page(user)
        self.open(page, reverse('user-delete'))
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/delete' not in url)
        self.wait_until(lambda: not User.objects.filter(username='leaving').exists(), 'the account was not deleted')
        self.assertFalse(Account.objects.filter(name='HDFC Savings').exists())
        page.goto(self.live_server_url + reverse('expense-list'))
        self.assertIn('/accounts/login', page.url)


@override_settings(FAMILY_ENABLED=True)
class FamilyTests(E2ETestCase):
    def test_creating_a_family_and_moving_between_its_pages_with_the_tabs_under_the_title(self):
        user = make_user('parent')
        page = self.new_page(user)
        self.open(page, reverse('family-home'))
        page.click('button:has-text("Create your family")')
        page.wait_for_selector('.tmr-tabs')
        # the tabs sit below the page title and sub heading
        title = page.locator('.fam-head h1').bounding_box()
        tabs = page.locator('.tmr-tabs').bounding_box()
        self.assertGreater(tabs['y'], title['y'])
        for label, path in (('Net worth', '/family/net-worth/'), ('What I share', '/family/share/'),
                            ('Members', '/family/')):
            page.locator('.tmr-tabs .tmr-tab', has_text=label).first.click()
            page.wait_for_url(f'**{path}')
            self.assertTrue(page.locator('.tmr-tab.is-active', has_text=label).count() >= 1, label)
