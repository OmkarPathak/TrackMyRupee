"""Recurring entries that fall due the same day for the same amount are all posted when they are different things (two
fixed deposits paying the same interest), and a schedule that is processed again does not post twice."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from expenses.models import Account, CapitalEvent, Income, RecurringTransaction
from expenses.views.mixins import process_user_recurring_transactions

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('recurrer', password='pw')
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT', balance=D('100000'), currency='₹')
        self.other = Account.objects.create(user=self.user, name='SBI Savings', account_type='SAVINGS_ACCOUNT', balance=D('100000'), currency='₹')

    def income(self, description, account=None, amount='8333.33', source='Interest Income'):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', amount=D(amount), currency='₹', base_amount=D(amount),
            description=description, source=source, frequency='MONTHLY', start_date=self.today, account=account or self.bank)

    def capital(self, description, account=None, amount='50000'):
        return RecurringTransaction.objects.create(
            user=self.user, transaction_type='CAPITAL', amount=D(amount), currency='₹', base_amount=D(amount),
            description=description, capital_subtype='other', frequency='MONTHLY', start_date=self.today, account=account or self.bank)


class IncomeTests(Base):
    def posted(self):
        return Income.objects.filter(user=self.user, date=self.today)

    def test_two_deposits_paying_the_same_interest_into_the_same_account_are_both_posted(self):
        self.income('Interest: FD one')
        self.income('Interest: FD two')
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.posted().count(), 2)

    def test_the_same_interest_into_two_accounts_is_posted_to_each(self):
        self.income('Interest into HDFC', self.bank)
        self.income('Interest into SBI', self.other)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(set(self.posted().values_list('account__name', flat=True)), {'HDFC Savings', 'SBI Savings'})

    def test_processing_again_does_not_post_a_schedule_twice(self):
        self.income('Interest: FD one')
        self.income('Interest: FD two')
        process_user_recurring_transactions(self.user, force=True)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.posted().count(), 2)

    def test_an_entry_the_person_typed_in_by_hand_for_the_same_payment_is_not_posted_again(self):
        schedule = self.income('Interest: FD one')
        Income.objects.create(user=self.user, date=self.today, amount=schedule.amount, currency='₹', source='Interest Income',
                              source_type='Other', description=schedule.description, account=self.bank, base_amount=schedule.amount)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.posted().count(), 1)


class CapitalTests(Base):
    def posted(self):
        return CapitalEvent.objects.filter(user=self.user, date=self.today)

    def test_two_big_payments_of_the_same_size_from_different_accounts_are_both_posted(self):
        self.capital('Plot A instalment', self.bank)
        self.capital('Plot B instalment', self.other)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.posted().count(), 2)

    def test_processing_again_does_not_post_one_twice(self):
        self.capital('Plot A instalment', self.bank)
        self.capital('Plot B instalment', self.other)
        process_user_recurring_transactions(self.user, force=True)
        process_user_recurring_transactions(self.user, force=True)
        self.assertEqual(self.posted().count(), 2)
