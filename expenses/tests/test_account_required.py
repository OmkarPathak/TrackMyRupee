"""Every transaction must be traced to an account: forms, composer, Flows and conversions reject a
missing account with a visible, field-level message, while rows saved before the rule keep working."""

import json
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import (
    CapitalEventForm, ExpenseForm, GoalContributionForm, IncomeForm, LoanRepaymentForm,
    RecurringTransactionForm,
)
from expenses.models import Account, CapitalEvent, Expense, Income, Loan, RecurringTransaction, SavingsGoal, UserProfile

D = Decimal
MSG = 'Select an account so your balances stay accurate.'


def today():
    return timezone.localdate()


class AccountRequiredBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='acc-req', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        Account.objects.filter(user=self.user).delete()
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000.00'), currency='₹')
        cache.clear()

    def expense_data(self, **kw):
        data = {'date': today(), 'amount': '100', 'currency': '₹', 'description': 'x',
                'category': 'Food', 'payment_method': 'Cash'}
        data.update(kw)
        return data


class FormsRequireAccountTests(AccountRequiredBase):
    def test_expense_form_requires_account(self):
        form = ExpenseForm(self.expense_data(), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertEqual(form.errors['account'], [MSG])
        form = ExpenseForm(self.expense_data(account=self.cash.pk), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_income_form_requires_account(self):
        data = {'date': today(), 'amount': '100', 'currency': '₹', 'source_type': 'Salary'}
        self.assertEqual(IncomeForm(data, user=self.user).errors['account'], [MSG])
        self.assertTrue(IncomeForm({**data, 'account': self.cash.pk}, user=self.user).is_valid())

    def test_capital_event_form_requires_account(self):
        data = {'date': today(), 'amount': '600000', 'currency': '₹', 'subtype': 'loan_prepayment',
                'exclude_from_averages': 'on', 'exclude_from_budget': 'on', 'include_in_net_worth': 'on'}
        self.assertEqual(CapitalEventForm(data, user=self.user).errors['account'], [MSG])
        self.assertTrue(CapitalEventForm({**data, 'account': self.cash.pk}, user=self.user).is_valid())

    def test_goal_contribution_form_requires_account(self):
        data = {'amount': '100', 'date': today()}
        self.assertEqual(GoalContributionForm(data, user=self.user).errors['account'], [MSG])
        self.assertTrue(GoalContributionForm({**data, 'account': self.cash.pk}, user=self.user).is_valid())

    def test_loan_repayment_form_requires_account(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('1000'),
                                   duration_months=10, start_date=today(), currency='₹')
        data = {'amount': '100', 'principal_portion': '90', 'interest_portion': '10', 'date': today()}
        self.assertEqual(LoanRepaymentForm(data, user=self.user, loan=loan).errors['from_account'], [MSG])
        self.assertTrue(LoanRepaymentForm({**data, 'from_account': self.cash.pk}, user=self.user, loan=loan).is_valid())

    def test_no_html_required_attribute(self):
        """The native select is replaced by a searchable widget; an HTML5 `required` would be
        un-focusable and block the submit silently."""
        for form in (ExpenseForm(user=self.user), IncomeForm(user=self.user), CapitalEventForm(user=self.user)):
            self.assertNotIn('required', form['account'].as_widget())

    def test_recurring_form_requires_account_except_transfers(self):
        base = {'description': 'Rent', 'amount': '100', 'currency': '₹', 'frequency': 'MONTHLY',
                'start_date': today(), 'payment_method': 'Cash', 'is_active': 'on'}
        for kind, extra in (('EXPENSE', {'category': 'Food'}), ('INCOME', {'source': 'Salary'}),
                            ('CAPITAL', {'capital_subtype': 'large_purchase'})):
            form = RecurringTransactionForm({**base, 'transaction_type': kind, **extra}, user=self.user)
            self.assertFalse(form.is_valid(), kind)
            self.assertIn('account', form.errors, kind)
        ok = RecurringTransactionForm({**base, 'transaction_type': 'EXPENSE', 'category': 'Food',
                                       'account': self.cash.pk}, user=self.user)
        self.assertTrue(ok.is_valid(), ok.errors)


class ViewsShowTheErrorTests(AccountRequiredBase):
    def test_capital_event_create_without_account_shows_error_and_changes_nothing(self):
        """The reported case: a 6L loan prepayment with no account must not post."""
        loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=D('600000'),
                                   duration_months=24, start_date=today(), currency='₹')
        response = self.client.post(reverse('capital-event-create'), {
            'date': today(), 'amount': '600000', 'currency': '₹', 'subtype': 'loan_prepayment',
            'linked_loan': loan.pk, 'exclude_from_averages': 'on', 'exclude_from_budget': 'on',
            'include_in_net_worth': 'on'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, MSG)
        self.assertFalse(CapitalEvent.objects.exists())

    def test_expense_edit_without_account_shows_error(self):
        expense = Expense.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                         category='Food', description='x', account=self.cash)
        response = self.client.post(reverse('expense-edit', args=[expense.uuid]), self.expense_data())
        self.assertContains(response, MSG)
        expense.refresh_from_db()
        self.assertEqual(expense.account, self.cash)

    def test_income_create_without_account_shows_error(self):
        response = self.client.post(reverse('income-create'), {'date': today(), 'amount': '5', 'currency': '₹',
                                                               'source_type': 'Salary'})
        self.assertContains(response, MSG)
        self.assertFalse(Income.objects.exists())

    def test_composer_rejects_missing_account_with_field_error(self):
        response = self.client.post(reverse('expense-composer-save'), data=json.dumps({
            'key': 'k-1', 'amount': '100', 'currency': '₹', 'date': str(today()), 'description': 'x',
            'category': 'Food', 'account_id': '', 'payment_method': 'Cash'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('account', response.json()['field_errors'])
        self.assertFalse(Expense.objects.exists())

    def test_composer_template_has_account_message_slot(self):
        from django.template.loader import render_to_string
        html = render_to_string('components/expense_composer_card.html', {})
        self.assertIn('tmr-account-msg', html)
        self.assertIn('data-k="errAccount"', html)


class LegacyRowsKeepWorkingTests(AccountRequiredBase):
    def test_editing_a_legacy_row_without_account_is_still_allowed(self):
        legacy = Expense.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                        category='Food', description='old')
        self.assertIsNone(legacy.account_id)
        form = ExpenseForm(self.expense_data(description='renamed'), instance=legacy, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D('100000.00'))

    def test_choosing_an_account_on_a_legacy_row_charges_it(self):
        legacy = Expense.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                        category='Food', description='old')
        form = ExpenseForm(self.expense_data(amount='50', account=self.cash.pk), instance=legacy, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.cash.refresh_from_db()
        self.assertEqual(self.cash.balance, D('99950.00'))

    def test_account_cannot_be_removed_from_a_row_that_has_one(self):
        expense = Expense.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                         category='Food', description='x', account=self.cash)
        form = ExpenseForm(self.expense_data(), instance=expense, user=self.user)
        self.assertEqual(form.errors['account'], [MSG])

    def test_legacy_recurring_schedule_can_still_be_edited(self):
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', description='Gym', amount=D('10'), currency='₹',
            category='Food', frequency='MONTHLY', start_date=today(), payment_method='Cash')
        form = RecurringTransactionForm({
            'transaction_type': 'EXPENSE', 'description': 'Gym 2', 'amount': '10', 'currency': '₹',
            'category': 'Food', 'frequency': 'MONTHLY', 'start_date': rt.start_date,
            'payment_method': 'Cash', 'is_active': 'on'}, instance=rt, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)


class ConversionsNeedAnAccountTests(AccountRequiredBase):
    def test_expense_without_account_cannot_become_capital_event(self):
        expense = Expense.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                         category='Food', description='old')
        self.client.post(reverse('expense-convert', args=[expense.uuid]))
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())
        self.assertFalse(CapitalEvent.objects.exists())

    def test_event_kept_out_of_cash_flow_cannot_become_expense(self):
        event = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                            subtype='other', account=self.cash, include_in_net_worth=False)
        self.client.post(reverse('capital-event-convert', args=[event.uuid]))
        self.assertTrue(CapitalEvent.objects.filter(pk=event.pk).exists())
        self.assertFalse(Expense.objects.exists())

    def test_event_with_account_converts_and_keeps_account(self):
        event = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('50'), currency='₹',
                                            subtype='other', account=self.cash)
        self.client.post(reverse('capital-event-convert', args=[event.uuid]))
        self.assertEqual(Expense.objects.get().account, self.cash)
