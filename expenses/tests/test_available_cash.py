"""Available cash: what a person can spend today. The cash and bank accounts they have not set aside, in their own currency;
money moved into deposits and investments is not in it, and net worth is not changed by any of it."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.account_types import available_cash_codes
from expenses.available_cash import available_cash
from expenses.forms import AccountForm
from expenses.models import Account, FXRate, Transfer, UserProfile
from expenses.views.dashboard_networth import net_worth_card

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('spender', password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.bank = self.account('HDFC Savings', 'SAVINGS_ACCOUNT', 50000)

    def account(self, name, kind, balance, **extra):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency=extra.pop('currency', '₹'), **extra)

    def page(self, **query):
        return self.client.get(reverse('account-list'), query)


class CalculationTests(Base):
    def total(self, *accounts):
        return available_cash(accounts, lambda a: a.balance)

    def test_cash_and_bank_accounts_count(self):
        accounts = [self.bank, self.account('Wallet', 'CASH_WALLET', 2000), self.account('Salary', 'SALARY_ACCOUNT', 30000),
                    self.account('Current', 'CURRENT_ACCOUNT', 8000), self.account('Paytm', 'DIGITAL_WALLET', 500)]
        self.assertEqual(self.total(*accounts), D('90500.00'))

    def test_deposits_investments_cards_and_loans_do_not(self):
        accounts = [self.account('FD', 'FD', 100000), self.account('Funds', 'MUTUAL_FUND', 200000), self.account('Card', 'CREDIT_CARD', -5000),
                    self.account('Loan', 'HOME_LOAN', 900000), self.account('PPF', 'PPF', 70000)]
        self.assertEqual(self.total(*accounts), D('0.00'))

    def test_an_account_set_aside_does_not_count(self):
        pot = self.account('Investment pot', 'SAVINGS_ACCOUNT', 80000, include_in_available_cash=False)
        self.assertEqual(self.total(self.bank, pot), D('50000.00'))

    def test_a_closed_account_does_not_count(self):
        closed = self.account('Old account', 'SAVINGS_ACCOUNT', 9000, is_active=False)
        self.assertEqual(self.total(self.bank, closed), D('50000.00'))

    def test_an_overdrawn_account_takes_away(self):
        overdrawn = self.account('Current', 'CURRENT_ACCOUNT', -1500)
        self.assertEqual(self.total(self.bank, overdrawn), D('48500.00'))

    def test_the_converted_balance_is_what_is_added(self):
        dollars = self.account('USD savings', 'SAVINGS_ACCOUNT', 100, currency='$')
        self.assertEqual(available_cash([self.bank, dollars], lambda a: a.balance * 80 if a.currency == '$' else a.balance), D('58000.00'))

    def test_the_types_are_exactly_the_cash_and_bank_group(self):
        self.assertEqual(available_cash_codes(), {'CASH_WALLET', 'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT', 'DIGITAL_WALLET'})

    def test_every_account_counts_unless_set_aside(self):
        self.assertTrue(self.bank.include_in_available_cash)
        self.assertTrue(self.bank.counts_as_available_cash)
        self.assertFalse(self.bank.is_set_aside)
        self.bank.include_in_available_cash = False
        self.assertFalse(self.bank.counts_as_available_cash)
        self.assertTrue(self.bank.is_set_aside)

    def test_an_investment_account_is_never_called_set_aside(self):
        funds = self.account('Funds', 'MUTUAL_FUND', 1000, include_in_available_cash=False)
        self.assertFalse(funds.is_set_aside)
        self.assertFalse(funds.counts_as_available_cash)


class AccountsPageTests(Base):
    def test_the_page_shows_available_cash_beside_net_worth(self):
        self.account('FD', 'FD', 100000)
        self.account('Funds', 'MUTUAL_FUND', 200000)
        context = self.page().context
        self.assertEqual(context['available_cash'], D('50000.00'))
        self.assertEqual(context['overall_total'], D('350000.00'))
        html = self.page().content.decode()
        self.assertIn('id="acx-available-cash"', html)
        self.assertIn('₹50,000', html)

    def test_money_moved_into_an_investment_leaves_available_cash_but_not_net_worth(self):
        funds = self.account('Funds', 'MUTUAL_FUND', 0)
        before = self.page().context
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=funds, amount=D('20000'), date=timezone.localdate())
        after = self.page().context
        self.assertEqual(before['available_cash'], D('50000.00'))
        self.assertEqual(after['available_cash'], D('30000.00'))
        self.assertEqual(after['overall_total'], before['overall_total'])

    def test_an_account_set_aside_is_left_out_and_marked(self):
        pot = self.account('Emergency fund', 'SAVINGS_ACCOUNT', 80000, include_in_available_cash=False)
        context = self.page().context
        self.assertEqual(context['available_cash'], D('50000.00'))
        self.assertEqual(context['overall_total'], D('130000.00'))        # (net worth still has it)
        self.assertContains(self.page(), 'Not in available cash', count=1)
        self.assertIsNotNone(pot.pk)

    def test_the_inactive_tab_shows_none(self):
        self.assertIsNone(self.page(status='inactive').context['available_cash'])

    def test_a_dollar_account_is_converted(self):
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=timezone.localdate(),
                                            defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        self.account('USD savings', 'SAVINGS_ACCOUNT', 100, currency='$')
        self.assertEqual(self.page().context['available_cash'], D('58000.00'))

    def test_an_overdraft_shows_with_its_sign(self):
        Account.objects.filter(pk=self.bank.pk).update(balance=D('-2000'))
        html = self.page().content.decode()
        self.assertRegex(html, r'id="acx-available-cash"[^>]*>\s*Available cash <strong>&minus;₹2,000')


class DashboardTests(Base):
    def test_the_net_worth_card_has_it(self):
        self.account('Funds', 'MUTUAL_FUND', 200000)
        card = net_worth_card(self.user, currency_symbol='₹')
        self.assertEqual(card['available_cash'], D('50000.00'))
        self.assertEqual(card['net_worth'], D('250000.00'))

    def test_a_set_aside_account_is_left_out_of_the_card_too(self):
        self.account('Emergency fund', 'SAVINGS_ACCOUNT', 80000, include_in_available_cash=False)
        card = net_worth_card(self.user, currency_symbol='₹')
        self.assertEqual(card['available_cash'], D('50000.00'))
        self.assertEqual(card['net_worth'], D('130000.00'))

    def test_the_fragment_the_dashboard_fetches_shows_it(self):
        cache.clear()
        response = self.client.get(reverse('dashboard-networth'))
        self.assertContains(response, 'id="nw-available-cash"')
        self.assertContains(response, 'Available cash')

    def test_changing_the_setting_changes_the_figure_straight_away(self):
        cache.clear()
        line = r'id="nw-available-cash".*?<strong class="text-body">(.*?)</strong>'
        import re
        self.assertEqual(re.search(line, self.client.get(reverse('dashboard-networth')).content.decode(), re.S).group(1), '₹50,000')
        self.bank.include_in_available_cash = False
        self.bank.save()
        self.assertEqual(re.search(line, self.client.get(reverse('dashboard-networth')).content.decode(), re.S).group(1), '₹0')


class FormTests(Base):
    def data(self, **extra):
        data = {'name': 'Pot', 'account_type': 'SAVINGS_ACCOUNT', 'balance': '1000', 'currency': '₹'}
        data.update(extra)
        return data

    def test_the_setting_starts_on_for_a_new_account(self):
        self.assertTrue(AccountForm(user=self.user)['include_in_available_cash'].value())
        self.assertContains(self.client.get(reverse('account-create')), 'id="available-cash-switch"')

    def test_ticked_it_is_kept_and_unticked_it_is_turned_off(self):
        on = AccountForm(self.data(include_in_available_cash='on'), user=self.user)
        self.assertTrue(on.is_valid(), on.errors)
        self.assertTrue(on.save(commit=False).include_in_available_cash)
        off = AccountForm(self.data(name='Pot 2'), user=self.user)
        self.assertTrue(off.is_valid(), off.errors)
        self.assertFalse(off.save(commit=False).include_in_available_cash)

    def test_creating_with_it_off_through_the_page_keeps_it_off(self):
        response = self.client.post(reverse('account-create'), self.data())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.get(name='Pot').include_in_available_cash)

    def test_the_switch_is_offered_only_for_cash_and_bank_types(self):
        html = self.client.get(reverse('account-create')).content.decode()
        self.assertIn('cashTypes: ["CASH_WALLET"', html)
        self.assertNotIn('"FD"', html.split('cashTypes:')[1].split(']')[0])

    def test_editing_an_account_turns_it_off_and_on(self):
        edit = reverse('account-edit', kwargs={'pk': self.bank.uuid})
        data = {'name': 'HDFC Savings', 'account_type': 'SAVINGS_ACCOUNT', 'balance': '50000', 'currency': '₹'}
        self.client.post(edit, data)
        self.bank.refresh_from_db()
        self.assertFalse(self.bank.include_in_available_cash)
        self.client.post(edit, dict(data, include_in_available_cash='on'))
        self.bank.refresh_from_db()
        self.assertTrue(self.bank.include_in_available_cash)
