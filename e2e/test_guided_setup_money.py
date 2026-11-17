"""Amounts typed into a Guided Setup are grouped as the person types, like on the other forms, and still save the
plain number."""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse

from e2e.base import E2ETestCase, make_account, make_user

# What the person sees in the field (the element's own value is the plain number)
SHOWN = "el => Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').get.call(el)"


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('money')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 3000000)

    def shown(self, page, selector):
        return page.eval_on_selector(selector, SHOWN)


class TypingTests(Base):
    def type_into(self, page, selector, text):
        page.click(selector)
        page.keyboard.type(text, delay=20)

    def test_the_principal_is_grouped_the_indian_way_while_typing(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.type_into(page, 'input[name="principal"]', '2500000')
        self.assertEqual(self.shown(page, 'input[name="principal"]'), '25,00,000')
        self.assertEqual(page.eval_on_selector('input[name="principal"]', 'el => el.value'), '2500000')       # the number it posts

    def test_the_rate_is_not_grouped(self):
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.type_into(page, 'input[name="annual_rate"]', '7.25')
        self.assertEqual(self.shown(page, 'input[name="annual_rate"]'), '7.25')

    def test_a_dollar_profile_groups_in_threes(self):
        user = make_user('dollars', currency='$')
        make_account(user, 'Checking', 'SAVINGS_ACCOUNT', 3000000)
        page = self.new_page(user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        self.type_into(page, 'input[name="principal"]', '2500000')
        self.assertEqual(self.shown(page, 'input[name="principal"]'), '2,500,000')

    def test_every_amount_field_of_the_other_setups_is_grouped_too(self):
        for key, field, expected in (('loan', 'principal', '25,00,000'), ('sip', 'amount', '25,00,000'),
                                     ('savingsgoal', 'target_amount', '25,00,000'), ('salary', 'amount', '25,00,000')):
            page = self.new_page(self.user)
            self.open(page, reverse('flow-detail', kwargs={'key': key}))
            self.type_into(page, f'input[name="{field}"]', '2500000')
            self.assertEqual(self.shown(page, f'input[name="{field}"]'), expected, f'{key}.{field}')

    def test_it_works_on_a_phone(self):
        page = self.new_page(self.user, phone=True)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.tap('input[name="principal"]')
        page.keyboard.type('2500000', delay=20)
        self.assertEqual(self.shown(page, 'input[name="principal"]'), '25,00,000')


class SavingTests(Base):
    def test_an_fd_set_up_with_grouped_amounts_saves_the_plain_numbers(self):
        from django.utils import timezone

        from expenses.models import Account, RecurringTransaction
        today = timezone.localdate()
        page = self.new_page(self.user)
        self.open(page, reverse('flow-detail', kwargs={'key': 'fd'}))
        page.fill('input[name="name"]', 'Big FD')
        page.click('input[name="principal"]')
        page.keyboard.type('2500000', delay=20)
        page.fill('input[name="annual_rate"]', '7.1')
        self.set_select(page, 'select[name="from_account"]', str(self.bank.pk))
        form = page.locator('#tmr-flow-form-fd')
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(300)
        page.fill('input[name="deposit_start_date"]', today.isoformat())
        page.fill('input[name="maturity_date"]', (today + timedelta(days=3 * 365)).isoformat())
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(300)
        self.set_select(page, 'select[name="interest_payout"]', 'MONTHLY')
        page.click('input[name="payout_amount"]')
        page.keyboard.type('14500', delay=20)
        self.assertEqual(self.shown(page, 'input[name="payout_amount"]'), '14,500')
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_timeout(300)
        form.locator('button:has-text("Next") >> visible=true').first.click()
        page.wait_for_selector('#tmr-review-fd', state='visible')
        page.locator('[data-confirm-btn] >> visible=true').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Big FD').exists(),
                        'the deposit was not created', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Big FD').deposit_principal, Decimal('2500000.00'))
        self.wait_until(lambda: RecurringTransaction.objects.filter(user=self.user, source='Interest Income').exists(),
                        'no payout schedule', seconds=15)
        self.assertEqual(RecurringTransaction.objects.get(user=self.user, source='Interest Income').amount,
                         Decimal('14500.00'))
