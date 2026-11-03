"""Amount inputs are currency-aware in the browser (static/js/money_input.js): the person types 100000 and sees
1,00,000 (rupee) or 100,000 (other currencies). The form posts the plain number, and the server also drops
commas, so an amount typed with them is never refused."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.forms import (
    CapitalEventForm,
    ExpenseForm,
    IncomeForm,
    MoneyInput,
    PrimaryMoneyInput,
    SavingsGoalForm,
    TransferForm,
)
from expenses.models import Account, UserProfile


class TestMoneyInputWidget(TestCase):
    def test_marks_the_input_for_the_script(self):
        html = MoneyInput(attrs={'class': 'form-control'}).render('amount', '')
        self.assertIn('data-money', html)
        self.assertNotIn('data-money-primary', html)
        self.assertIn('data-money-primary', PrimaryMoneyInput().render('amount', ''))

    def test_commas_are_dropped_from_what_is_posted(self):
        widget = MoneyInput()
        for typed, plain in (('1,00,000', '100000'), ('100,000.50', '100000.50'), (' 2,500 ', '2500'), ('300', '300')):
            self.assertEqual(widget.value_from_datadict({'amount': typed}, {}, 'amount'), plain)
        self.assertIsNone(widget.value_from_datadict({}, {}, 'amount'))

    def test_the_main_amount_of_each_money_form_is_marked_primary(self):
        for form_class in (ExpenseForm, IncomeForm, CapitalEventForm, SavingsGoalForm):
            form = form_class(user=None) if form_class is not SavingsGoalForm else form_class()
            names = [n for n, f in form.fields.items() if isinstance(f.widget, PrimaryMoneyInput)]
            self.assertEqual(len(names), 1, (form_class.__name__, names))

    def test_fields_that_are_not_money_stay_plain_numbers(self):
        from expenses.forms import LoanForm
        form = LoanForm()
        self.assertNotIn('data-money', form.fields['duration_months'].widget.render('d', ''))
        self.assertNotIn('data-money', form.fields['interest_rate'].widget.render('r', ''))


class TestFormsAcceptGroupedAmounts(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('mi', password='x')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save()
        self.user.refresh_from_db()
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=0, currency='₹')

    def test_income_form_takes_a_grouped_amount(self):
        form = IncomeForm({'date': date.today().isoformat(), 'amount': '1,00,000', 'currency': '₹',
                           'account': self.account.pk, 'source_type': 'Salary'}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], Decimal('100000'))

    def test_capital_event_form_takes_a_grouped_amount(self):
        form = CapitalEventForm({'date': date.today().isoformat(), 'amount': '100,000.25', 'currency': '$',
                                 'account': self.account.pk, 'subtype': 'other'}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], Decimal('100000.25'))

    def test_a_plain_amount_still_works(self):
        form = IncomeForm({'date': date.today().isoformat(), 'amount': '2500.50', 'currency': '₹',
                           'account': self.account.pk, 'source_type': 'Salary'}, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['amount'], Decimal('2500.50'))

    def test_pages_load_the_script_and_the_profile_currency(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('income-create'))
        self.assertContains(page, 'js/money_input.js')
        self.assertContains(page, 'window.TMR_CURRENCY = "₹"')
        self.assertContains(page, 'data-money-primary')
