"""The plain Add loan form can put the loan amount into an account (it used to be possible only from the loan page and
Guided Setup): on for a new loan, never shown when editing, and never counted as income."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import LoanForm
from expenses.models import Account, Income, Loan, LoanDisbursement, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = self.make_user('adder')
        self.client.force_login(self.user)
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('10000'), currency='₹')

    def make_user(self, name):
        user = User.objects.create_user(name, password='pw')
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.save()
        user.refresh_from_db()
        return user

    def data(self, **extra):
        data = {'name': 'Car Loan', 'loan_type': 'CAR', 'currency': '₹', 'initial_principal': '260000', 'interest_rate': '9',
                'duration_months': '60', 'start_date': self.today.isoformat(), 'record_received': 'on', 'received_account': self.bank.pk}
        data.update(extra)
        return data

    def post(self, **extra):
        return self.client.post(reverse('loan-create'), self.data(**extra))

    def balance(self):
        self.bank.refresh_from_db()
        return self.bank.balance


class FormTests(Base):
    def test_a_new_loan_form_offers_it_and_starts_on(self):
        form = LoanForm(user=self.user)
        self.assertTrue(form['record_received'].value())
        self.assertIn('received_account', form.fields)

    def test_editing_a_loan_never_offers_it(self):
        loan = Loan.objects.create(user=self.user, name='Old', loan_type='CAR', initial_principal=D('1000'), duration_months=12,
                                   start_date=self.today, currency='₹')
        form = LoanForm(instance=loan, user=self.user)
        self.assertNotIn('record_received', form.fields)
        self.assertNotIn('received_account', form.fields)

    def test_switched_on_it_needs_an_account(self):
        form = LoanForm(self.data(received_account=''), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('received_account', form.errors)

    def test_switched_off_it_needs_none(self):
        data = self.data(received_account='')
        data.pop('record_received')
        self.assertTrue(LoanForm(data, user=self.user).is_valid())

    def test_only_the_persons_own_money_accounts_can_be_chosen(self):
        other = self.make_user('someone-else')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='SAVINGS_ACCOUNT', balance=D('0'), currency='₹')
        owes = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('0'), currency='₹')
        for account in (theirs, owes):
            self.assertIn('received_account', LoanForm(self.data(received_account=account.pk), user=self.user).errors, account.name)

    def test_the_page_shows_the_switch_and_the_account(self):
        html = self.client.get(reverse('loan-create')).content.decode()
        self.assertIn('id="received-block"', html)
        self.assertIn('Add the loan amount to an account', html)


class CreateTests(Base):
    def test_the_loan_amount_is_added_to_the_account_on_the_start_date(self):
        response = self.post()
        self.assertEqual(response.status_code, 302)
        loan = Loan.objects.get(name='Car Loan')
        received = loan.disbursements.get()
        self.assertEqual((received.account_id, received.amount, received.date), (self.bank.pk, D('260000.00'), self.today))
        self.assertEqual(self.balance(), D('270000.00'))

    def test_it_is_not_income_and_the_page_says_so(self):
        response = self.post()
        self.assertFalse(Income.objects.filter(user=self.user).exists())
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('added to HDFC Savings' in m and 'not counted as income' in m for m in messages), messages)

    def test_switched_off_nothing_is_added(self):
        data = self.data()
        data.pop('record_received')
        self.client.post(reverse('loan-create'), data)
        self.assertEqual(Loan.objects.filter(name='Car Loan').count(), 1)
        self.assertEqual(LoanDisbursement.objects.count(), 0)
        self.assertEqual(self.balance(), D('10000.00'))

    def test_a_form_posted_without_the_new_fields_still_works_as_before(self):
        data = self.data()
        data.pop('record_received')
        data.pop('received_account')
        self.assertEqual(self.client.post(reverse('loan-create'), data).status_code, 302)
        self.assertEqual(self.balance(), D('10000.00'))

    def test_an_old_start_date_is_respected_by_the_server(self):
        """(the page switches it off for an old date; if the person switches it back on, the money is dated then)"""
        old = self.today - timedelta(days=200)
        self.post(start_date=old.isoformat())
        self.assertEqual(LoanDisbursement.objects.get().date, old)

    def test_a_bad_form_creates_neither_the_loan_nor_the_money(self):
        self.post(initial_principal='0')
        self.assertFalse(Loan.objects.filter(name='Car Loan').exists())
        self.assertEqual(self.balance(), D('10000.00'))

    def test_the_loans_balance_and_the_accounts_activity_agree(self):
        self.post()
        loan = Loan.objects.get(name='Car Loan')
        self.assertEqual(loan.remaining_principal, D('260000.00'))
        html = self.client.get(reverse('account-detail', kwargs={'pk': self.bank.uuid})).content.decode()
        self.assertIn('Loan amount received', html)


class StartingChoiceTests(Base):
    def test_with_a_cash_or_bank_account_one_is_chosen_to_start(self):
        Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=D('0'), currency='₹')
        form = LoanForm(user=self.user)
        self.assertEqual(form['received_account'].value(), self.bank.pk)

    def test_with_only_an_investment_account_that_is_chosen_rather_than_nothing(self):
        Account.objects.filter(pk=self.bank.pk).delete()
        funds = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND', balance=D('0'), currency='₹')
        self.assertEqual(LoanForm(user=self.user)['received_account'].value(), funds.pk)

    def test_with_no_account_at_all_the_switch_starts_off_so_the_loan_can_still_be_added(self):
        Account.objects.all().delete()
        form = LoanForm(user=self.user)
        self.assertFalse(form['record_received'].value())
        data = self.data(received_account='')
        data.pop('record_received')
        self.assertTrue(LoanForm(data, user=self.user).is_valid())
