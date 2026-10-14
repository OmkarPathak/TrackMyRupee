"""Audit of transfers and of undoing foreign-currency transactions: whatever the exchange rate did in
between, editing or deleting a transaction hands back exactly what it moved."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    CapitalEvent,
    Expense,
    FXRate,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    SavingsGoal,
    Transfer,
)

D = Decimal
TODAY = date.today()


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='tr', password='pw')
        self.user.profile.tier = 'PRO'
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        self.client.force_login(self.user)
        self.inr = Account.objects.create(user=self.user, name='INR', account_type='BANK', balance=D('10000'), currency='₹')
        self.usd = Account.objects.create(user=self.user, name='USD', account_type='BANK', balance=D('1000'), currency='$')
        self.rate(80)

    def rate(self, value):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=timezone.localdate(),
                                        defaults={'rate': D(str(value)), 'source': 't'})
        FXRate.objects.update_or_create(from_currency='INR', to_currency='USD', as_of_date=timezone.localdate(),
                                        defaults={'rate': D('1') / D(str(value)), 'source': 't'})
        cache.clear()

    def bal(self, account):
        account.refresh_from_db()
        return account.balance


class UndoAtADifferentRateTests(Base):
    """A $100 item on a rupee account is worth ₹8,000 when entered; the rate then moves to 85."""

    def test_deleting_a_dollar_expense_returns_what_it_took(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food', date=TODAY)
        self.assertEqual(self.bal(self.inr), D('2000.00'))
        self.rate(85)
        e.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_editing_a_dollar_expense_charges_the_new_amount_at_the_new_rate_and_returns_the_old_one_exactly(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food', date=TODAY)
        self.rate(85)
        e.amount = D('10')
        e.save()
        self.assertEqual(self.bal(self.inr), D('10000.00') - D('850.00'))

    def test_deleting_dollar_income_takes_back_what_it_added(self):
        i = Income.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', source='Freelance', date=TODAY)
        self.assertEqual(self.bal(self.inr), D('18000.00'))
        self.rate(75)
        i.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_deleting_a_cross_currency_transfer_restores_both_accounts(self):
        t = Transfer.objects.create(user=self.user, from_account=self.usd, to_account=self.inr, amount=D('100'), date=TODAY)
        self.assertEqual((self.bal(self.usd), self.bal(self.inr)), (D('900.00'), D('18000.00')))
        self.rate(85)
        t.delete()
        self.assertEqual((self.bal(self.usd), self.bal(self.inr)), (D('1000.00'), D('10000.00')))

    def test_editing_a_cross_currency_transfer_moves_only_the_difference(self):
        t = Transfer.objects.create(user=self.user, from_account=self.usd, to_account=self.inr, amount=D('100'), date=TODAY)
        self.rate(85)
        t.amount = D('50')
        t.save()
        # 50 dollars now leave the dollar account; the rupee side holds the 50 at the new rate
        self.assertEqual(self.bal(self.usd), D('950.00'))
        self.assertEqual(self.bal(self.inr), D('10000.00') + D('50') * D('85'))

    def test_deleting_a_goal_contribution_in_dollars_returns_it(self):
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('5000'), currency='$')
        c = GoalContribution.objects.create(goal=goal, account=self.inr, amount=D('100'), date=TODAY)
        self.assertEqual(self.bal(self.inr), D('2000.00'))
        self.rate(85)
        c.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_deleting_a_loan_repayment_in_dollars_returns_it(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'), currency='$',
                                   duration_months=12, start_date=TODAY, is_active=True)
        r = LoanRepayment.objects.create(loan=loan, from_account=self.inr, date=TODAY, amount=D('100'),
                                         principal_portion=D('90'), interest_portion=D('10'))
        self.assertEqual(self.bal(self.inr), D('2000.00'))
        self.rate(85)
        r.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_deleting_a_dollar_capital_event_returns_it(self):
        ce = CapitalEvent.objects.create(user=self.user, account=self.inr, date=TODAY, amount=D('100'), currency='$',
                                         include_in_net_worth=True)
        self.assertEqual(self.bal(self.inr), D('2000.00'))
        self.rate(85)
        ce.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_the_same_currency_case_is_unchanged(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('123.45'), currency='₹', category='Food', date=TODAY)
        e.delete()
        self.assertEqual(self.bal(self.inr), D('10000.00'))

    def test_an_item_with_no_ledger_record_falls_back_to_todays_rate(self):
        from expenses.models import JournalEntry
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food', date=TODAY)
        JournalEntry.objects.filter(source_type='EXPENSE', source_id=e.pk).delete()
        e.delete()
        self.assertEqual(self.bal(self.inr), D('2000.00') + D('8000.00'))


class TransferRulesTests(Base):
    def setUp(self):
        super().setUp()
        self.inr2 = Account.objects.create(user=self.user, name='INR2', account_type='BANK', balance=D('500'), currency='₹')

    def make(self, amount='100', **kw):
        values = dict(user=self.user, from_account=self.inr, to_account=self.inr2, amount=D(amount), date=TODAY)
        values.update(kw)
        return Transfer.objects.create(**values)

    def test_a_transfer_is_not_spending_and_not_income(self):
        self.make('250')
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(Income.objects.count(), 0)
        self.assertEqual(self.bal(self.inr) + self.bal(self.inr2), D('10500.00'))

    def test_money_is_neither_created_nor_lost_by_a_same_currency_transfer(self):
        before = self.bal(self.inr) + self.bal(self.inr2)
        t = self.make('333.33')
        t.amount = D('10.10')
        t.save()
        self.assertEqual(self.bal(self.inr) + self.bal(self.inr2), before)
        t.delete()
        self.assertEqual((self.bal(self.inr), self.bal(self.inr2)), (D('10000.00'), D('500.00')))

    def test_moving_a_transfer_to_other_accounts_returns_the_old_ones(self):
        t = self.make('100')
        t.from_account = self.inr2
        t.to_account = self.inr
        t.save()
        self.assertEqual((self.bal(self.inr), self.bal(self.inr2)), (D('10100.00'), D('400.00')))

    def test_a_transfer_to_the_same_account_is_refused(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            self.make(to_account=self.inr)

    def test_another_users_account_cannot_be_used(self):
        from django.core.exceptions import ValidationError
        stranger = User.objects.create_user(username='x', password='pw')
        theirs = Account.objects.create(user=stranger, name='T', balance=D('1'), currency='₹')
        with self.assertRaises(ValidationError):
            self.make(to_account=theirs)

    def test_the_base_amount_is_in_the_users_currency(self):
        t = Transfer.objects.create(user=self.user, from_account=self.usd, to_account=self.inr, amount=D('10'), date=TODAY)
        self.assertEqual((t.exchange_rate, t.converted_amount), (D('80.000000'), D('800.00')))

    def test_a_transfer_between_two_dollar_accounts_needs_no_rate(self):
        usd2 = Account.objects.create(user=self.user, name='USD2', account_type='BANK', balance=D('0'), currency='$')
        FXRate.objects.all().delete()
        cache.clear()
        Transfer.objects.create(user=self.user, from_account=self.usd, to_account=usd2, amount=D('5'), date=TODAY)
        self.assertEqual((self.bal(self.usd), self.bal(usd2)), (D('995.00'), D('5.00')))

    def test_the_transfer_screen_moves_money_and_delete_gives_it_back(self):
        r = self.client.post(reverse('transfer-create'), {
            'from_account': self.inr.pk, 'to_account': self.inr2.pk, 'amount': '75', 'date': TODAY.isoformat()})
        self.assertEqual(r.status_code, 302)
        t = Transfer.objects.get()
        self.assertEqual((self.bal(self.inr), self.bal(self.inr2)), (D('9925.00'), D('575.00')))
        self.client.post(reverse('transfer-delete', args=[t.uuid]))
        self.assertEqual((self.bal(self.inr), self.bal(self.inr2)), (D('10000.00'), D('500.00')))


class EditingOtherFieldsDoesNotRevalueTests(Base):
    """Changing a description or category must not change what the account was charged, even if the
    exchange rate has moved since the transaction was entered."""

    def ledger_net(self, account):
        from expenses.models import JournalLine
        return sum((l.amount if l.direction == 'DEBIT' else -l.amount)
                   for l in JournalLine.objects.filter(account_ref=account, journal_entry__status='POSTED'))

    def test_expense(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food',
                                   date=TODAY, description='a')
        self.rate(85)
        e.description = 'b'
        e.date = TODAY
        e.payment_method = 'UPI'
        e.save()
        self.assertEqual(self.bal(self.inr), D('2000.00'))
        self.assertEqual(self.ledger_net(self.inr), D('-8000.00'))

    def test_moving_an_expense_to_another_category_refiles_it_and_the_account_and_ledger_still_agree(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food',
                                   date=TODAY, description='a')
        self.rate(85)
        e.category = 'Fun'
        e.save()
        self.assertEqual(self.bal(self.inr), D('10000.00') + self.ledger_net(self.inr))

    def test_income(self):
        i = Income.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', source='Freelance', date=TODAY)
        self.rate(70)
        i.description = 'renamed'
        i.save()
        self.assertEqual(self.bal(self.inr), D('18000.00'))
        self.assertEqual(self.ledger_net(self.inr), D('8000.00'))

    def test_transfer(self):
        t = Transfer.objects.create(user=self.user, from_account=self.usd, to_account=self.inr, amount=D('100'), date=TODAY)
        self.rate(85)
        t.description = 'rent share'
        t.save()
        self.assertEqual((self.bal(self.usd), self.bal(self.inr)), (D('900.00'), D('18000.00')))

    def test_goal_contribution(self):
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('5000'), currency='$')
        c = GoalContribution.objects.create(goal=goal, account=self.inr, amount=D('100'), date=TODAY)
        self.rate(85)
        c.date = TODAY
        c.save()
        self.assertEqual(self.bal(self.inr), D('2000.00'))

    def test_loan_repayment(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'), currency='$',
                                   duration_months=12, start_date=TODAY, is_active=True)
        r = LoanRepayment.objects.create(loan=loan, from_account=self.inr, date=TODAY, amount=D('100'),
                                         principal_portion=D('90'), interest_portion=D('10'))
        self.rate(85)
        r.save()
        self.assertEqual(self.bal(self.inr), D('2000.00'))

    def test_capital_event(self):
        ce = CapitalEvent.objects.create(user=self.user, account=self.inr, date=TODAY, amount=D('100'), currency='$',
                                         include_in_net_worth=True)
        self.rate(85)
        ce.note = 'laptop'
        ce.save()
        self.assertEqual(self.bal(self.inr), D('2000.00'))

    def test_changing_the_amount_does_charge_the_new_amount_at_the_new_rate(self):
        e = Expense.objects.create(user=self.user, account=self.inr, amount=D('100'), currency='$', category='Food', date=TODAY)
        self.rate(85)
        e.amount = D('200')
        e.save()
        self.assertEqual(self.bal(self.inr), D('10000.00') - D('17000.00'))
