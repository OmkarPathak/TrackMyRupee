"""A setup offers the cash or bank account the person uses most as the account to pay from or into, not just the first."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from expenses.flows.base import default_funding_account
from expenses.flows.investment import FdFlowForm
from expenses.models import Account, Expense, Income
from expenses.tests.test_flows_end_to_end import FlowTestBase


class DefaultFundingAccountTests(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.accounts = Account.objects.filter(user=self.user, is_active=True).order_by('name')

    def spend(self, account, times, days_ago=5, **extra):
        for _ in range(times):
            Expense.objects.create(user=self.user, date=self.today - timedelta(days=days_ago), amount=Decimal('100'),
                                   description='x', category='Food', account=account, base_amount=Decimal('100'), **extra)

    def earn(self, account, times, days_ago=5):
        for _ in range(times):
            Income.objects.create(user=self.user, date=self.today - timedelta(days=days_ago), amount=Decimal('100'),
                                  source='Salary', account=account, base_amount=Decimal('100'))

    def test_with_no_history_it_is_the_cash_account_as_before(self):
        self.assertEqual(default_funding_account(self.accounts), self.cash)

    def test_the_account_with_most_entries_wins(self):
        self.spend(self.bank, 3)
        self.spend(self.cash, 1)
        self.assertEqual(default_funding_account(self.accounts), self.bank)

    def test_expenses_and_income_both_count(self):
        self.spend(self.bank, 2)
        self.earn(self.bank, 2)
        self.spend(self.cash, 3)
        self.assertEqual(default_funding_account(self.accounts), self.bank)

    def test_old_entries_do_not_count(self):
        self.spend(self.bank, 5, days_ago=400)
        self.spend(self.cash, 1)
        self.assertEqual(default_funding_account(self.accounts), self.cash)

    def test_deleted_entries_do_not_count(self):
        self.spend(self.bank, 5, is_deleted=True)
        self.spend(self.cash, 1)
        self.assertEqual(default_funding_account(self.accounts), self.cash)

    def test_a_credit_card_or_deposit_is_never_offered_however_busy(self):
        card = Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', balance=Decimal('0'), currency='₹')
        self.spend(card, 9)
        self.spend(self.bank, 1)
        self.assertEqual(default_funding_account(Account.objects.filter(user=self.user, is_active=True)), self.bank)

    def test_a_tie_goes_to_the_name_that_comes_first(self):
        self.spend(self.cash, 2)
        self.spend(self.bank, 2)
        self.assertEqual(default_funding_account(self.accounts), self.bank)           # "Bank" before "Cash"

    def test_without_any_cash_or_bank_account_it_is_the_first_account(self):
        Account.objects.filter(pk__in=[self.cash.pk, self.bank.pk]).delete()
        card = Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', balance=Decimal('0'), currency='₹')
        self.assertEqual(default_funding_account(Account.objects.filter(user=self.user, is_active=True)), card)

    def test_another_persons_activity_is_not_counted(self):
        from django.contrib.auth.models import User
        other = User.objects.create_user('other', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='SAVINGS_ACCOUNT', balance=Decimal('0'), currency='₹')
        Expense.objects.create(user=other, date=self.today, amount=1, description='x', category='Food', account=theirs, base_amount=1)
        self.assertEqual(default_funding_account(self.accounts), self.cash)

    def test_the_setups_offer_it(self):
        self.spend(self.bank, 3)
        form = FdFlowForm(user=self.user)
        self.assertEqual(form.fields['from_account'].initial, self.bank)
