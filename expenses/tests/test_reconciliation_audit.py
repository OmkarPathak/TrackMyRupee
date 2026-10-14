"""Audit of reconciliation: the balance on a statement date is rebuilt correctly from every kind of
transaction, credit cards read with the sign the app shows, and each choice does only what it says."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses import reconciliation
from expenses.models import (
    Account,
    AccountReconciliation,
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
from django.utils import timezone

D = Decimal
TODAY = date.today()
STATEMENT = TODAY - timedelta(days=10)
BEFORE = STATEMENT - timedelta(days=5)
AFTER = STATEMENT + timedelta(days=3)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='rec', password='pw')
        self.user.profile.tier = 'PLUS'
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('1000'), currency='₹')
        self.other = Account.objects.create(user=self.user, name='Other', account_type='BANK', balance=D('500'), currency='₹')

    def expense(self, amount, when, account=None, **kw):
        return Expense.objects.create(user=self.user, account=account or self.bank, amount=D(str(amount)),
                                      date=when, category='Food', description=kw.pop('description', 'x'),
                                      currency=kw.pop('currency', '₹'), **kw)

    def income(self, amount, when, account=None):
        return Income.objects.create(user=self.user, account=account or self.bank, amount=D(str(amount)),
                                     date=when, source='Salary', currency='₹')

    def on_statement_date(self, account=None):
        return reconciliation.balance_as_of(account or self.bank, STATEMENT)


class EveryKindOfTransactionTests(Base):
    """A transaction dated after the statement must not change the balance on the statement date,
    and one dated before must; whatever its type."""

    def check(self, make, signed):
        make(AFTER)
        self.assertEqual(self.on_statement_date(), D('1000.00'), 'later one leaked into the past')
        self.assertEqual(reconciliation.current_balance(self.bank), D('1000.00') + signed / 2)
        make(BEFORE)
        self.assertEqual(reconciliation.current_balance(self.bank), D('1000.00') + signed)
        self.assertEqual(self.on_statement_date(), D('1000.00') + signed / 2)

    def test_expense(self):
        self.check(lambda d: self.expense(40, d), D('-80.00'))

    def test_income(self):
        self.check(lambda d: self.income(60, d), D('120.00'))

    def test_transfer_out(self):
        self.check(lambda d: Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other,
                                                     amount=D('30'), date=d), D('-60.00'))

    def test_transfer_in(self):
        self.check(lambda d: Transfer.objects.create(user=self.user, from_account=self.other, to_account=self.bank,
                                                     amount=D('30'), date=d), D('60.00'))

    def test_goal_contribution(self):
        goal = SavingsGoal.objects.create(user=self.user, name='G', target_amount=D('9999'), currency='₹')
        self.check(lambda d: GoalContribution.objects.create(goal=goal, account=self.bank, amount=D('25'), date=d),
                   D('-50.00'))

    def test_loan_repayment(self):
        loan = Loan.objects.create(user=self.user, name='L', loan_type='HOME', initial_principal=D('5000'),
                                   duration_months=12, start_date=BEFORE - timedelta(days=60), is_active=True)
        self.check(lambda d: LoanRepayment.objects.create(
            loan=loan, from_account=self.bank, date=d, amount=D('20'), principal_portion=D('15'),
            interest_portion=D('5')), D('-40.00'))

    def test_capital_event_that_moves_money(self):
        self.check(lambda d: CapitalEvent.objects.create(user=self.user, account=self.bank, date=d, amount=D('70'),
                                                         include_in_net_worth=True), D('-140.00'))

    def test_capital_event_kept_out_of_net_worth_does_not_touch_the_account(self):
        CapitalEvent.objects.create(user=self.user, account=self.bank, date=BEFORE, amount=D('70'),
                                    include_in_net_worth=False)
        self.assertEqual(reconciliation.current_balance(self.bank), D('1000.00'))
        self.assertEqual(self.on_statement_date(), D('1000.00'))

    def test_a_transaction_on_the_statement_date_itself_counts(self):
        self.expense(100, STATEMENT)
        self.assertEqual(self.on_statement_date(), D('900.00'))

    def test_the_balance_today_is_always_what_the_app_shows(self):
        self.expense(10, BEFORE)
        self.income(300, AFTER)
        Transfer.objects.create(user=self.user, from_account=self.bank, to_account=self.other, amount=D('5'), date=AFTER)
        self.assertEqual(reconciliation.balance_as_of(self.bank, TODAY), reconciliation.current_balance(self.bank))

    def test_another_users_transactions_never_count(self):
        stranger = User.objects.create_user(username='stranger', password='pw')
        acc = Account.objects.create(user=stranger, name='Bank', balance=D('0'), currency='₹')
        Expense.objects.create(user=stranger, account=acc, amount=D('9'), date=BEFORE, category='Food', currency='₹')
        self.assertEqual(self.on_statement_date(), D('1000.00'))

    @override_settings(SOFT_DELETE_ENABLED=True)
    def test_deleted_transactions_do_not_count_either(self):
        later = self.expense(200, AFTER)
        earlier = self.expense(50, BEFORE)
        later.delete()
        earlier.delete()
        self.assertEqual(reconciliation.current_balance(self.bank), D('1000.00'))
        self.assertEqual(self.on_statement_date(), D('1000.00'))
        result = reconciliation.compare(self.bank, STATEMENT, D('1000'))
        self.assertTrue(result.matched)
        self.assertEqual(result.later_count, 0)


class ForeignCurrencyTests(Base):
    def test_a_dollar_expense_on_a_rupee_account_counts_in_rupees(self):
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=D('80'), source='t',
                              as_of_date=timezone.localdate())
        self.expense(10, AFTER, currency='$')
        self.assertEqual(reconciliation.current_balance(self.bank), D('200.00'))
        self.assertEqual(self.on_statement_date(), D('1000.00'))

    def test_a_transfer_from_a_dollar_account_arrives_converted(self):
        FXRate.objects.create(from_currency='USD', to_currency='INR', rate=D('80'), source='t',
                              as_of_date=timezone.localdate())
        usd = Account.objects.create(user=self.user, name='USD', account_type='BANK', balance=D('100'), currency='$')
        Transfer.objects.create(user=self.user, from_account=usd, to_account=self.bank, amount=D('2'), date=BEFORE)
        self.assertEqual(reconciliation.current_balance(self.bank), D('1160.00'))
        self.assertEqual(self.on_statement_date(), D('1160.00'))


class CreditCardTests(Base):
    def setUp(self):
        super().setUp()
        self.card = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD', balance=D('0'),
                                           currency='₹', credit_limit=D('50000'))

    def test_money_owed_is_negative_and_a_matching_statement_matches(self):
        self.expense(1200, BEFORE, account=self.card)
        self.expense(300, AFTER, account=self.card)
        self.assertEqual(reconciliation.current_balance(self.card), D('-1500.00'))
        result = reconciliation.compare(self.card, STATEMENT, D('-1200'))
        self.assertTrue(result.matched)

    def test_entering_the_amount_due_as_a_positive_number_shows_a_large_difference(self):
        self.expense(1200, BEFORE, account=self.card)
        result = reconciliation.compare(self.card, STATEMENT, D('1200'))
        self.assertFalse(result.matched)
        self.assertEqual(result.difference, D('2400.00'))

    def test_adjusting_a_card_moves_the_amount_owed(self):
        self.expense(1200, BEFORE, account=self.card)
        reconciliation.record(self.card, STATEMENT, D('-1250'), action='adjust')
        self.assertEqual(reconciliation.current_balance(self.card), D('-1250.00'))

    def test_a_card_can_be_reconciled_through_the_screen(self):
        self.expense(1200, BEFORE, account=self.card)
        r = self.client.post(reverse('account-reconcile-confirm', args=[self.card.uuid]), {
            'statement_date': STATEMENT.isoformat(), 'statement_balance': '-1200', 'action': 'adjust'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AccountReconciliation.objects.get().status, 'MATCHED')


class ChoicesTests(Base):
    def test_an_adjustment_corrects_today_by_the_difference_found_on_the_statement_date(self):
        self.expense(100, BEFORE)
        self.income(40, AFTER)          # after the statement: must survive the adjustment
        reconciliation.record(self.bank, STATEMENT, D('850'), action='adjust')   # app said 900 then: -50
        self.assertEqual(reconciliation.current_balance(self.bank), D('890.00'))
        self.assertEqual(self.on_statement_date(), D('850.00'))

    def test_a_second_reconciliation_after_an_adjustment_starts_from_the_corrected_balance(self):
        self.expense(100, BEFORE)
        reconciliation.record(self.bank, STATEMENT, D('850'), action='adjust')
        again = reconciliation.record(self.bank, TODAY, D('850'), action='adjust')
        self.assertEqual(again.status, 'MATCHED')

    def test_a_difference_within_a_paisa_is_a_match(self):
        self.assertTrue(reconciliation.compare(self.bank, STATEMENT, D('1000.01')).matched)
        self.assertFalse(reconciliation.compare(self.bank, STATEMENT, D('1000.02')).matched)

    def test_every_attempt_is_kept_in_the_history_newest_first(self):
        reconciliation.record(self.bank, STATEMENT, D('900'), action='open')
        reconciliation.record(self.bank, STATEMENT, D('1000'), action='open')
        r = self.client.get(reverse('account-reconciliation-history', args=[self.bank.uuid]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(AccountReconciliation.objects.filter(account=self.bank).count(), 2)

    def test_the_note_is_kept_and_cut_to_fit(self):
        rec = reconciliation.record(self.bank, STATEMENT, D('900'), action='accept', note='n' * 400)
        self.assertEqual(len(rec.note), 255)

    def test_an_inactive_account_cannot_be_reconciled(self):
        Account.objects.filter(pk=self.bank.pk).update(is_active=False)
        with self.assertRaises(reconciliation.ReconciliationError):
            reconciliation.record(self.bank, STATEMENT, D('1000'), action='adjust')

    def test_a_failed_ledger_posting_leaves_no_half_finished_record(self):
        with patch('expenses.ledger_service.LedgerPostingService.post_account_balance_adjustment',
                   return_value=(None, False)):
            with self.assertRaises(reconciliation.ReconciliationError):
                reconciliation.record(self.bank, STATEMENT, D('900'), action='adjust')
        self.assertEqual(AccountReconciliation.objects.count(), 0)
        self.assertEqual(reconciliation.current_balance(self.bank), D('1000.00'))


class ScreenTests(Base):
    def post(self, name, **data):
        return self.client.post(reverse(name, args=[self.bank.uuid]), data)

    def test_a_statement_date_before_the_account_existed_is_allowed_and_explained_by_the_opening_balance(self):
        result = reconciliation.compare(self.bank, TODAY - timedelta(days=400), D('1000'))
        self.assertTrue(result.matched)

    def test_a_bad_amount_is_refused_without_saving(self):
        r = self.post('account-reconcile-confirm', statement_date=STATEMENT.isoformat(), statement_balance='abc',
                      action='adjust')
        self.assertRedirects(r, reverse('account-reconcile', args=[self.bank.uuid]), fetch_redirect_response=False)
        self.assertEqual(AccountReconciliation.objects.count(), 0)

    def test_a_future_date_is_refused_on_confirm_too(self):
        r = self.post('account-reconcile-confirm', statement_date=(TODAY + timedelta(days=1)).isoformat(),
                      statement_balance='1000', action='adjust')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(AccountReconciliation.objects.count(), 0)

    def test_the_compare_step_never_writes_anything(self):
        before = reconciliation.current_balance(self.bank)
        self.post('account-reconcile', statement_date=STATEMENT.isoformat(), statement_balance='1')
        self.assertEqual(AccountReconciliation.objects.count(), 0)
        self.assertEqual(reconciliation.current_balance(self.bank), before)

    def test_one_users_history_is_not_open_to_another(self):
        stranger = User.objects.create_user(username='s2', password='pw')
        self.client.force_login(stranger)
        r = self.client.get(reverse('account-reconciliation-history', args=[self.bank.uuid]))
        self.assertEqual(r.status_code, 404)
