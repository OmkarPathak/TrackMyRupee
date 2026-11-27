"""Cash & bank accounts (cash wallet, savings, salary, current, digital wallet): balances through every kind of entry.

Every one of these holds a plain balance that income, expenses and transfers move by exactly the amount entered.
"""
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import balance_on, net_worth, open_account, save_account_form

D = Decimal
TODAY = timezone.localdate()
KINDS = ('CASH_WALLET', 'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT', 'DIGITAL_WALLET')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('cash-edge')

    def account(self, name='Wallet', kind='SAVINGS_ACCOUNT', balance=10000, **extra):
        return make_account(self.user, name, kind, balance, **extra)

    def spend(self, account, amount, on=None):
        from expenses.models import Expense
        return Expense.objects.create(user=self.user, account=account, amount=D(str(amount)), base_amount=D(str(amount)), currency='₹',
                                      date=on or TODAY, description='Edge spend', category='Food')

    def earn(self, account, amount, on=None):
        from expenses.models import Income
        return Income.objects.create(user=self.user, account=account, amount=D(str(amount)), base_amount=D(str(amount)), currency='₹',
                                     date=on or TODAY, source='Edge pay', source_type='Salary')

    def move(self, src, dst, amount, converted=None):
        from expenses.models import Transfer
        return Transfer.objects.create(user=self.user, from_account=src, to_account=dst, amount=D(str(amount)),
                                       converted_amount=D(str(converted if converted is not None else amount)), date=TODAY)

    def shown(self, page, account):
        open_account(self, page, account)
        return balance_on(page)


class EveryKindTests(Base):
    def test_each_kind_shows_its_balance_and_counts_in_net_worth(self):
        page = self.new_page(self.user)
        accounts = [self.account(f'{kind.title()}', kind, 1000 * (i + 1)) for i, kind in enumerate(KINDS)]
        for account, expected in zip(accounts, (1000, 2000, 3000, 4000, 5000), strict=True):
            with self.subTest(account.account_type):
                self.assertEqual(self.shown(page, account), D(expected))
        self.assertEqual(net_worth(self, page), D('15000.00'))

    def test_income_and_spending_move_every_kind_by_exactly_the_amount(self):
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                account = self.account(f'Moves {kind}', kind, 10000)
                self.earn(account, '2500.50')
                self.spend(account, '1200.25')
                self.assertEqual(self.shown(page, account), D('11300.25'))


class BalanceEdgeCases(Base):
    def test_paise_add_up_exactly(self):
        account = self.account(balance='0.00')
        for _ in range(10):
            self.earn(account, '0.10')
        self.spend(account, '0.30')
        page = self.new_page(self.user)
        self.assertEqual(self.shown(page, account), D('0.70'))

    def test_spending_more_than_the_balance_shows_a_negative_balance_and_lowers_net_worth(self):
        account = self.account(balance=1000)
        self.spend(account, 1800)
        page = self.new_page(self.user)
        self.assertEqual(self.shown(page, account), D('-800.00'))
        self.assertEqual(net_worth(self, page), D('-800.00'))

    def test_a_zero_balance_account_is_shown_as_zero_not_blank(self):
        account = self.account(balance=0)
        page = self.new_page(self.user)
        self.assertEqual(self.shown(page, account), D('0.00'))

    def test_a_very_large_balance_keeps_every_digit_in_indian_grouping(self):
        account = self.account(balance='99999999999.99')
        page = self.new_page(self.user)
        open_account(self, page, account)
        self.assertEqual(balance_on(page), D('99999999999.99'))
        exact = page.locator('div[style*="clamp(2rem"] [data-exact]').first.get_attribute('data-exact')
        self.assertIn('9,99,99,99,999.99', exact)

    def test_deleting_an_income_takes_the_money_back_out(self):
        account = self.account(balance=1000)
        income = self.earn(account, 400)
        page = self.new_page(self.user)
        self.assertEqual(self.shown(page, account), D('1400.00'))
        income.delete()
        self.assertEqual(self.shown(page, account), D('1000.00'))

    def test_editing_an_expense_to_another_account_moves_the_money_between_them(self):
        first, second = self.account('First', balance=5000), self.account('Second', balance=5000)
        expense = self.spend(first, 700)
        expense.account = second
        expense.save()
        page = self.new_page(self.user)
        self.assertEqual(self.shown(page, first), D('5000.00'))
        self.assertEqual(self.shown(page, second), D('4300.00'))

    def test_an_inactive_account_stops_counting_in_net_worth(self):
        keep = self.account('Keep', balance=3000)
        gone = self.account('Gone', balance=7000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('10000.00'))
        type(gone).objects.filter(pk=gone.pk).update(is_active=False)
        self.assertEqual(net_worth(self, page), D('3000.00'))
        self.assertEqual(self.shown(page, keep), D('3000.00'))


class TransferEdgeCases(Base):
    def test_a_transfer_between_two_accounts_keeps_the_total_unchanged(self):
        a, b = self.account('A', balance=8000), self.account('B', 'DIGITAL_WALLET', 2000)
        self.move(a, b, 3000)
        page = self.new_page(self.user)
        self.assertEqual((self.shown(page, a), self.shown(page, b)), (D('5000.00'), D('5000.00')))
        self.assertEqual(net_worth(self, page), D('10000.00'))

    def test_a_transfer_that_overdraws_the_source_still_balances_both_sides(self):
        a, b = self.account('A', balance=1000), self.account('B', balance=0)
        self.move(a, b, 1500)
        page = self.new_page(self.user)
        self.assertEqual((self.shown(page, a), self.shown(page, b)), (D('-500.00'), D('1500.00')))
        self.assertEqual(net_worth(self, page), D('1000.00'))

    def test_deleting_a_transfer_gives_both_balances_back(self):
        a, b = self.account('A', balance=4000), self.account('B', balance=1000)
        transfer = self.move(a, b, 2500)
        transfer.delete()
        page = self.new_page(self.user)
        self.assertEqual((self.shown(page, a), self.shown(page, b)), (D('4000.00'), D('1000.00')))

    def test_a_transfer_to_yourself_is_refused(self):
        from django.core.exceptions import ValidationError
        a = self.account('A', balance=4000)
        with self.assertRaises((ValidationError, ValueError, Exception)):
            transfer = self.move(a, a, 100)
            transfer.full_clean()
        self.assertEqual(type(a).objects.get(pk=a.pk).balance, D('4000.00'))


class FormTests(Base):
    def test_each_kind_can_be_created_with_an_opening_balance(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                self.open(page, reverse('account-create') + f'?type={kind}')
                page.fill('#id_name', f'New {kind}')
                page.fill('#id_balance', '1234.56')
                save_account_form(page)
                self.wait_until(lambda k=kind: Account.objects.filter(user=self.user, name=f'New {k}').exists(), f'{kind} was not saved', seconds=15)
                self.assertEqual(Account.objects.get(user=self.user, name=f'New {kind}').balance, D('1234.56'))

    def test_the_balance_can_be_edited(self):
        account = self.account(balance=1000)
        page = self.new_page(self.user)
        self.open(page, reverse('account-edit', kwargs={'pk': account.uuid}))
        page.fill('#id_balance', '2500.75')
        save_account_form(page)
        page.wait_for_url(lambda url: '/edit' not in url)
        self.assertEqual(self.shown(page, account), D('2500.75'))

    def test_a_name_is_required_and_the_form_stays_on_its_first_step(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=SAVINGS_ACCOUNT')
        page.fill('#id_balance', '100')
        page.click('#nextBtn')
        page.wait_for_timeout(800)
        self.assertIn('Step 1 of 2', page.inner_text('body'))
        self.assertEqual(Account.objects.filter(user=self.user).count(), 0)
