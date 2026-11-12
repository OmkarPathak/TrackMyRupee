"""Forms start with what the app can work out: the only choice, and the account used last time."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.forms import ChoiceField, ModelChoiceField
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.form_defaults import only_choice
from expenses.forms import (
    CapitalEventForm, ExpenseForm, GoalContributionForm, IncomeForm, LoanRepaymentForm, TransferForm,
)
from expenses.models import Account, CapitalEvent, GoalContribution, Income, Loan, SavingsGoal, Transfer, UserProfile

D = Decimal


class OnlyChoiceTests(SimpleTestCase):
    def test_one_real_choice_is_the_only_choice(self):
        self.assertEqual(only_choice(ChoiceField(choices=[('', '---'), ('a', 'A')])), 'a')

    def test_several_or_no_choices_have_no_default(self):
        self.assertIsNone(only_choice(ChoiceField(choices=[('a', 'A'), ('b', 'B')])))
        self.assertIsNone(only_choice(ChoiceField(choices=[('', '---')])))

    def test_an_optional_field_is_left_alone(self):
        self.assertIsNone(only_choice(ChoiceField(required=False, choices=[('a', 'A')])))

    def test_grouped_choices_count_each_option(self):
        self.assertEqual(only_choice(ChoiceField(choices=[('G', [('a', 'A')])])), 'a')
        self.assertIsNone(only_choice(ChoiceField(choices=[('G', [('a', 'A'), ('b', 'B')])])))


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('defaults-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.user.refresh_from_db()
        Account.objects.filter(user=self.user).delete()
        self.client.force_login(self.user)

    def account(self, name, kind='SAVINGS_ACCOUNT'):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D('1000'), currency='₹')

    def loan(self):
        return Loan.objects.create(user=self.user, name='Home', loan_type='HOME', initial_principal=D('100000'),
                                   duration_months=12, start_date=timezone.localdate(), currency='₹')


class OneAccountTests(Base):
    """Someone with a single account is never asked which one."""

    def setUp(self):
        super().setUp()
        self.only = self.account('HDFC Savings')

    def test_money_forms_start_on_the_only_account(self):
        self.assertEqual(IncomeForm(user=self.user)['account'].value(), self.only.pk)
        self.assertEqual(CapitalEventForm(user=self.user)['account'].value(), self.only.pk)
        self.assertEqual(GoalContributionForm(user=self.user)['account'].value(), self.only.pk)
        self.assertEqual(LoanRepaymentForm(user=self.user, loan=self.loan())['from_account'].value(), self.only.pk)
        self.assertEqual(ExpenseForm(user=self.user)['account'].value(), self.only.pk)

    def test_move_money_fills_from_but_never_to(self):
        form = TransferForm(user=self.user)
        self.assertEqual(form['from_account'].value(), self.only.pk)
        self.assertIn(form['to_account'].value(), (None, ''))

    def test_the_page_selects_it(self):
        response = self.client.get(reverse('income-create'))
        self.assertEqual(response.context['form']['account'].value(), self.only.pk)

    def test_two_accounts_means_no_guess(self):
        self.account('ICICI Savings')
        self.assertIn(GoalContributionForm(user=self.user)['account'].value(), (None, ''))
        self.assertIn(CapitalEventForm(user=self.user)['account'].value(), (None, ''))

    def test_an_optional_dropdown_is_not_filled_in(self):
        self.loan()
        self.assertIn(CapitalEventForm(user=self.user)['linked_loan'].value(), (None, ''))

    def test_editing_keeps_what_was_saved(self):
        other = self.account('ICICI Savings')
        event = CapitalEvent.objects.create(user=self.user, date=timezone.localdate(), amount=D('500'), currency='₹',
                                            account=other, subtype='OTHER')
        Account.objects.filter(pk=other.pk).delete()
        form = CapitalEventForm(instance=CapitalEvent.objects.get(pk=event.pk), user=self.user)
        self.assertNotEqual(form['account'].value(), self.only.pk)

    def test_a_submitted_form_is_not_changed(self):
        form = IncomeForm({'date': timezone.localdate(), 'amount': '10', 'currency': '₹', 'source': 'x'}, user=self.user)
        self.assertIn(form['account'].value(), (None, ''))


class LastUsedTests(Base):
    def setUp(self):
        super().setUp()
        self.cash = self.account('Cash', 'CASH_WALLET')
        self.bank = self.account('HDFC Savings')
        self.card = self.account('ICICI Card', 'CREDIT_CARD')

    def test_move_money_starts_from_the_last_move(self):
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.card, amount=D('100'),
                                date=timezone.localdate())
        form = TransferForm(user=self.user)
        self.assertEqual(form['from_account'].value(), self.bank.pk)
        self.assertEqual(form['to_account'].value(), self.card.pk)

    def test_move_money_has_no_guess_before_the_first_move(self):
        form = TransferForm(user=self.user)
        self.assertIn(form['from_account'].value(), (None, ''))
        self.assertIn(form['to_account'].value(), (None, ''))

    def test_income_starts_on_the_account_that_got_the_last_income(self):
        Income.objects.create(user=self.user, account=self.bank, amount=D('50000'), currency='₹',
                              source='Salary', date=timezone.localdate())
        self.assertEqual(IncomeForm(user=self.user)['account'].value(), self.bank.pk)

    def test_income_falls_back_to_cash_with_no_history(self):
        self.assertEqual(IncomeForm(user=self.user)['account'].value(), self.cash.pk)

    def test_one_time_big_purchase_starts_on_the_last_one_used(self):
        CapitalEvent.objects.create(user=self.user, date=timezone.localdate(), amount=D('500'), currency='₹',
                                    account=self.card, subtype='OTHER')
        self.assertEqual(CapitalEventForm(user=self.user)['account'].value(), self.card.pk)

    def test_pay_emi_form_never_starts_on_a_card(self):
        form = LoanRepaymentForm(user=self.user, loan=self.loan())
        self.assertEqual(form['from_account'].value(), self.bank.pk)

    def test_a_goal_contribution_starts_on_the_account_used_last_time(self):
        goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=D('1000'), currency='₹',
                                          target_date=timezone.localdate())
        self.assertIn(GoalContributionForm(user=self.user)['account'].value(), (None, ''))
        GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('100'), date=timezone.localdate())
        self.assertEqual(GoalContributionForm(user=self.user)['account'].value(), self.bank.pk)
