"""Every form that asks for an account starts on the right one: what the person last used for that kind of entry, else the
cash or bank account they use most, else the one called Cash."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone

from expenses import composer
from expenses.form_defaults import default_account, most_used_account
from expenses.forms import (
    CapitalEventForm,
    ExpenseForm,
    GoalContributionForm,
    IncomeForm,
    RecurringTransactionForm,
    TransferForm,
)
from expenses.models import Account, Expense, Income, Transfer
from expenses.tests.test_flows_end_to_end import FlowTestBase


def pk_of(value):
    return getattr(value, 'pk', value)


class Base(FlowTestBase):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.salary = Account.objects.create(user=self.user, name='Salary A/c', account_type='SALARY_ACCOUNT',
                                             balance=Decimal('1000'), currency='₹')

    def fresh_user(self):
        return User.objects.get(pk=self.user.pk)          # (the expense form caches its accounts on the user object)

    def spend(self, account, times, days_ago=5):
        for _ in range(times):
            Expense.objects.create(user=self.user, date=self.today - timedelta(days=days_ago), amount=Decimal('100'),
                                   description='x', category='Food', account=account, base_amount=Decimal('100'))

    def earn(self, account, times, days_ago=5):
        for _ in range(times):
            Income.objects.create(user=self.user, date=self.today - timedelta(days=days_ago), amount=Decimal('100'),
                                  source='Salary', account=account, base_amount=Decimal('100'))

    def initial(self, form, field='account'):
        return pk_of(form.fields[field].initial)


class SharedRuleTests(Base):
    def accounts(self):
        return Account.objects.filter(user=self.user, is_active=True)

    def test_most_used_ignores_cards_and_deposits(self):
        card = Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', balance=0, currency='₹')
        self.spend(card, 9)
        self.assertIsNone(most_used_account(self.accounts()))

    def test_the_newest_row_beats_the_busiest_account(self):
        self.spend(self.bank, 5)
        self.spend(self.salary, 1, days_ago=1)
        rows = Expense.objects.filter(user=self.user).order_by('-date', '-id')
        self.assertEqual(default_account(self.accounts(), rows, 'account_id'), self.salary)

    def test_with_nothing_recorded_it_is_cash(self):
        self.assertEqual(default_account(self.accounts()), self.cash)

    def test_with_nothing_recorded_and_no_cash_it_is_nothing(self):
        self.cash.delete()
        self.assertIsNone(default_account(self.accounts().exclude(name='Bank')))


class FormTests(Base):
    def test_the_expense_form_starts_on_the_account_last_paid_from(self):
        self.spend(self.bank, 4)
        self.spend(self.salary, 1, days_ago=1)
        self.assertEqual(self.initial(ExpenseForm(user=self.fresh_user())), self.salary.pk)

    def test_the_expense_form_with_no_history_is_cash_as_before(self):
        self.assertEqual(self.initial(ExpenseForm(user=self.fresh_user())), self.cash.pk)

    def test_the_expense_form_falls_back_to_the_busiest_when_the_last_account_cannot_be_picked(self):
        self.earn(self.bank, 3)
        self.assertEqual(self.initial(ExpenseForm(user=self.fresh_user())), self.bank.pk)

    def test_the_income_form_starts_on_the_account_last_paid_into(self):
        self.earn(self.salary, 1)
        self.assertEqual(self.initial(IncomeForm(user=self.fresh_user())), self.salary.pk)

    def test_the_income_form_with_no_income_yet_uses_the_busiest(self):
        self.spend(self.bank, 3)
        self.assertEqual(self.initial(IncomeForm(user=self.fresh_user())), self.bank.pk)

    def test_the_goal_contribution_form_uses_the_busiest_when_there_is_no_contribution(self):
        self.spend(self.bank, 3)
        self.assertEqual(self.initial(GoalContributionForm(user=self.fresh_user())), self.bank.pk)

    def test_the_big_purchase_form_uses_the_account_of_the_last_expense(self):
        self.spend(self.salary, 1)
        self.spend(self.bank, 3, days_ago=20)
        self.assertEqual(self.initial(CapitalEventForm(user=self.fresh_user())), self.salary.pk)

    def test_forms_that_never_guessed_still_leave_the_choice_with_no_history(self):
        user = self.fresh_user()                       # (there is a Cash account, and nothing recorded)
        for form in (GoalContributionForm(user=user), CapitalEventForm(user=user), RecurringTransactionForm(user=user)):
            self.assertIsNone(form.fields['account'].initial, type(form).__name__)

    def test_the_recurring_form_starts_on_the_busiest_account(self):
        self.spend(self.bank, 3)
        self.assertEqual(self.initial(RecurringTransactionForm(user=self.fresh_user())), self.bank.pk)

    def test_the_recurring_form_for_an_existing_schedule_is_not_touched(self):
        from expenses.models import RecurringTransaction
        self.spend(self.bank, 3)
        schedule = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=Decimal('10'), currency='₹', account=self.cash,
            description='Gym', frequency='MONTHLY', start_date=self.today)
        form = RecurringTransactionForm(user=self.fresh_user(), instance=schedule)
        self.assertIsNone(form.fields['account'].initial)

    def test_the_transfer_form_without_an_earlier_move_pays_from_the_busiest(self):
        self.spend(self.bank, 3)
        form = TransferForm(user=self.fresh_user())
        self.assertEqual(self.initial(form, 'from_account'), self.bank.pk)
        self.assertIsNone(form.fields['to_account'].initial)

    def test_the_transfer_form_keeps_starting_from_the_last_move(self):
        self.spend(self.bank, 3)
        Transfer.objects.create(user=self.user, date=self.today, amount=Decimal('50'), from_account=self.salary,
                                to_account=self.cash)
        form = TransferForm(user=self.fresh_user())
        self.assertEqual(self.initial(form, 'from_account'), self.salary.pk)
        self.assertEqual(self.initial(form, 'to_account'), self.cash.pk)


class TransferFallbackTests(Base):
    def test_when_the_last_moves_accounts_can_no_longer_be_picked_it_starts_from_the_busiest(self):
        self.spend(self.bank, 3)
        Transfer.objects.create(user=self.user, date=self.today, amount=Decimal('50'), from_account=self.salary, to_account=self.cash)
        Account.objects.filter(pk=self.salary.pk).update(is_active=False)           # (closed since)
        form = TransferForm(user=self.fresh_user())
        self.assertEqual(self.initial(form, 'from_account'), self.bank.pk)
        self.assertIsNone(form.fields['to_account'].initial)

    def test_with_neither_a_usable_last_move_nor_any_history_it_leaves_the_choice(self):
        Transfer.objects.create(user=self.user, date=self.today, amount=Decimal('50'), from_account=self.salary, to_account=self.cash)
        Account.objects.filter(pk=self.salary.pk).update(is_active=False)
        form = TransferForm(user=self.fresh_user())
        self.assertIsNone(form.fields['from_account'].initial)


class ComposerTests(Base):
    def test_quick_add_starts_on_the_last_used_account(self):
        self.spend(self.salary, 1)
        payload = composer.build_payload(self.user)
        self.assertEqual((payload['default_account_id'], payload['default_account_source']), (self.salary.pk, 'last_used'))

    def test_quick_add_with_only_income_history_uses_the_busiest(self):
        self.earn(self.salary, 2)
        payload = composer.build_payload(self.user)
        self.assertEqual((payload['default_account_id'], payload['default_account_source']), (self.salary.pk, 'most_used'))

    def test_quick_add_with_no_history_still_takes_the_first(self):
        payload = composer.build_payload(self.user)
        self.assertEqual(payload['default_account_source'], 'first')
