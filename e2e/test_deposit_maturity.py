"""An FD that matures: the card on its page, adding the interest, and moving the money out."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user


class DepositMaturityTests(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('depositor')
        self.bank = make_account(self.user, 'ICICI Savings', 'SAVINGS_ACCOUNT', 20000)
        self.fd = make_account(self.user, 'ICICI FD', 'FD', 100000,
                               deposit_maturity_date=timezone.localdate() + timedelta(days=2))

    def test_the_card_is_open_and_add_interest_opens_income_for_that_account(self):
        from expenses.models import Income
        page = self.new_page(self.user)
        self.open(page, reverse('account-detail', kwargs={'pk': self.fd.uuid}))
        card = page.locator('#maturity-card')
        self.assertTrue(card.get_attribute('open') is not None)
        self.assertIn('Matures in 2 days', card.inner_text())
        card.locator('text=Add interest').click()
        page.wait_for_url('**/income/add/**')
        page.fill('input[name="amount"]', '10000')
        page.click('button[type="submit"]:visible')
        page.wait_for_url(lambda url: '/income/add' not in url)
        income = Income.objects.get(user=self.user)
        self.assertEqual((income.account_id, income.source_type, income.amount),
                         (self.fd.pk, 'Investment Returns', Decimal('10000.00')))

    def test_move_the_money_out_opens_move_money_from_the_deposit(self):
        page = self.new_page(self.user)
        self.open(page, reverse('account-detail', kwargs={'pk': self.fd.uuid}))
        page.locator('#maturity-card').locator('text=Move Money').click()
        page.wait_for_url('**/transfers/add/**')
        self.assertEqual(page.input_value('select[name="from_account"]'), str(self.fd.pk))

    def test_far_from_maturity_the_card_is_folded_away(self):
        far = make_account(self.user, 'HDFC FD', 'FD', 50000, deposit_maturity_date=timezone.localdate() + timedelta(days=90))
        page = self.new_page(self.user)
        self.open(page, reverse('account-detail', kwargs={'pk': far.uuid}))
        card = page.locator('#maturity-card')
        self.assertIsNone(card.get_attribute('open'))
        card.locator('summary').click()                          # one tap opens it
        self.assertIsNotNone(card.get_attribute('open'))
        self.assertTrue(card.locator('text=Move the money out').is_visible())
