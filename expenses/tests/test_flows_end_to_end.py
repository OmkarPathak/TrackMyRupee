"""End-to-end behavioural tests for every TMR Flow.

test_flows.py focuses on registry, rendering and limits. This module pins down what each
flow actually *does*: form validation, every object a commit creates (with its field
values and links), historical on/off scheduling, idempotent replay and rollback.
"""

import calendar
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.flows.base import Flow
from expenses.flows.income import first_pay_date
from expenses.flows.insurance import _premium_to_recurring_frequency
from expenses.flows.registry import FlowRegistry
from expenses.models import (
    Account,
    AssetValuation,
    CapitalEvent,
    FinancialFlow,
    FlowCreatedObject,
    Holding,
    Income,
    Loan,
    LoanInterestRate,
    PhysicalAsset,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from expenses.services import LoanService
from expenses.services_recurring import RecurringService


def today():
    return timezone.localdate()


class FlowTestBase(TestCase):
    """PRO-tier user (no plan limits) with a cash wallet and a bank account."""

    currency = '₹'

    def setUp(self):
        self.user = User.objects.create_user(username='e2e-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': self.currency})
        profile.tier = 'PRO'
        profile.currency = self.currency
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.cash = Account.objects.create(
            user=self.user, name='Cash', account_type='CASH_WALLET',
            balance=Decimal('500000.00'), currency=self.currency,
        )
        self.bank = Account.objects.create(
            user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
            balance=Decimal('500000.00'), currency=self.currency,
        )

    # -- helpers ---------------------------------------------------------
    def form(self, key, data, user=None):
        flow = FlowRegistry.get(key)
        return flow.form_class(data=data, user=user or self.user)

    def valid_form(self, key, data):
        form = self.form(key, data)
        self.assertTrue(form.is_valid(), dict(form.errors))
        return form

    def run_flow(self, key, data, idem=None):
        """Validate through the real form, then commit. Returns FlowResult."""
        form = self.valid_form(key, data)
        return FlowRegistry.get(key).commit(self.user, form.cleaned_data, idem or uuid.uuid4())

    def created(self, result, model):
        return [obj for obj in result.created if isinstance(obj, model)]

    def one(self, result, model):
        objs = self.created(result, model)
        self.assertEqual(len(objs), 1, f'expected exactly one {model.__name__}, got {objs}')
        return objs[0]

    def assertErrorOn(self, key, data, field):
        form = self.form(key, data)
        self.assertFalse(form.is_valid(), f'form unexpectedly valid for {field}')
        self.assertIn(field, form.errors, dict(form.errors))

    def post_commit(self, key, data, hx=True, idem=None):
        payload = dict(data)
        if idem is not False:
            payload['idempotency_key'] = idem or self.session_key(key)
        extra = {'HTTP_HX_REQUEST': 'true'} if hx else {}
        return self.client.post(reverse('flow-commit', kwargs={'key': key}), payload, **extra)

    def session_key(self, key):
        html = self.client.get(reverse('flow-detail', kwargs={'key': key})).content.decode()
        return re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

    def assertSchedulePending(self, rt):
        """With historical entries off, nothing may be due in the past."""
        rt.refresh_from_db()
        self.assertIsNotNone(rt.last_processed_date)
        self.assertLessEqual(rt.last_processed_date, today())
        self.assertGreater(rt.next_due_date, today(), 'schedule would back-post an occurrence the user opted out of')


# Minimal valid payload for every flow (string values, as posted by a browser).
def valid_payloads(test):
    cash = str(test.cash.id)
    return {
        'loan': dict(name='Home Loan', principal='1200000', annual_rate='8.5', tenure_months='120',
                     start_date='2026-10-01', create_repayment_schedule='on', payment_account=cash,
                     repayment_type='EMI', loan_type='HOME', repayment_is_active='on'),
        'creditcard': dict(name='HDFC Card', balance='5000', currency='₹', credit_limit='100000', billing_day='5'),
        'salary': dict(amount='80000', account=cash, salary_date='1', start_date='2026-10-01'),
        'rentbill': dict(description='Flat rent', amount='25000', frequency='MONTHLY', account=cash,
                         start_date='2026-10-05', category='Rent'),
        'insurance': dict(name='Term Plan', premium_amount='12000', premium_frequency='ANNUAL',
                          premium_payment_account=cash, start_date='2026-10-01'),
        'sip': dict(instrument_type='SIP', name='Nifty 50', amount='5000', frequency='MONTHLY',
                    deposit_start_date='2026-10-15', from_account=cash),
        'fd': dict(name='SBI FD', principal='100000', annual_rate='7', deposit_start_date='2026-10-01',
                   maturity_date='2027-10-01', deposit_compounding='QUARTERLY', from_account=cash),
        'ppfepfnps': dict(scheme_type='PPF', name='My PPF', annual_amount='150000', deposit_principal='50000',
                          deposit_rate='7.1', deposit_start_date='2026-04-01', deposit_compounding='ANNUAL',
                          from_account=cash),
        'savingsgoal': dict(name='Emergency', target_amount='300000', current_amount='0', target_months='12',
                            color='success'),
        'car': dict(name='Honda City', purchase_price='1200000', acquisition_date='2026-10-01', from_account=cash),
        'gold': dict(route='physical', name='Gold Chain', amount='200000', acquisition_date='2026-10-01',
                     from_account=cash),
    }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class TestFlowEngine(FlowTestBase):
    def test_every_flow_has_a_payload_in_the_matrix(self):
        self.assertEqual(set(valid_payloads(self)), set(FlowRegistry.all()))

    def test_resolve_value_only_resolves_known_step_keys(self):
        created = {'loan': 'LOAN-OBJ'}
        resolve = Flow._resolve_value
        self.assertEqual(resolve('$loan', created), 'LOAN-OBJ')
        self.assertEqual(resolve({'a': ['$loan', 1]}, created), {'a': ['LOAN-OBJ', 1]})
        # User data that merely starts with "$" must survive untouched.
        for literal in ('$', '$500 phone', '$$$', '$unknown'):
            self.assertEqual(resolve(literal, created), literal)

    def test_dollar_currency_user_can_run_every_flow(self):
        """Regression: '$' (a valid currency symbol) used to be read as a step reference -> NULL currency."""
        profile = self.user.profile
        profile.currency = '$'
        profile.save()
        dollar_cash = Account.objects.create(user=self.user, name='USD Cash', account_type='CASH_WALLET',
                                             balance=Decimal('100000'), currency='$')
        for key, payload in valid_payloads(self).items():
            payload = {k: (str(dollar_cash.id) if v == str(self.cash.id) else v) for k, v in payload.items()}
            if 'currency' in payload:
                payload['currency'] = '$'
            if key == 'ppfepfnps':
                payload['name'] = 'USD PPF'
            with self.subTest(flow=key):
                result = self.run_flow(key, payload)
                for obj in result.created:
                    currency = getattr(obj, 'currency', None)
                    if hasattr(obj, 'currency'):
                        self.assertEqual(currency, '$', f'{type(obj).__name__}.currency')

    def test_text_starting_with_dollar_is_stored_verbatim(self):
        payload = valid_payloads(self)['loan']
        payload['name'] = '$500 phone'
        loan = self.one(self.run_flow('loan', payload), Loan)
        self.assertEqual(loan.name, '$500 phone')

        goal = self.one(self.run_flow('savingsgoal', {**valid_payloads(self)['savingsgoal'], 'name': '$ trip'}), SavingsGoal)
        self.assertEqual(goal.name, '$ trip')

        rt = self.one(self.run_flow('rentbill', {**valid_payloads(self)['rentbill'], 'description': '$rent'}), RecurringTransaction)
        self.assertEqual(rt.description, '$rent')

    def test_commit_records_flow_and_every_created_object(self):
        key = uuid.uuid4()
        result = self.run_flow('loan', valid_payloads(self)['loan'], idem=key)
        flow = FinancialFlow.objects.get(user=self.user, idempotency_key=key)
        self.assertEqual(flow.flow_key, 'loan')
        self.assertEqual(result.flow_id, flow.id)
        step_keys = set(flow.created_objects.values_list('step_key', flat=True))
        self.assertEqual(step_keys, {'loan', 'rate', 'emi'})
        self.assertEqual(flow.created_objects.count(), len(result.created))

    def test_spec_snapshot_is_json_safe_and_complete(self):
        key = uuid.uuid4()
        self.run_flow('loan', valid_payloads(self)['loan'], idem=key)
        spec = FinancialFlow.objects.get(idempotency_key=key).spec
        self.assertEqual(Decimal(spec['principal']), Decimal('1200000'))
        self.assertEqual(spec['start_date'], '2026-10-01')
        self.assertEqual(spec['user'], self.user.pk)
        self.assertEqual(spec['payment_account'], self.cash.pk)

    def test_replaying_a_commit_creates_nothing_new_for_every_flow(self):
        for key, payload in valid_payloads(self).items():
            with self.subTest(flow=key):
                idem = uuid.uuid4()
                first = self.run_flow(key, payload, idem=idem)
                counts = (
                    Account.objects.count(), Loan.objects.count(), RecurringTransaction.objects.count(),
                    CapitalEvent.objects.count(), PhysicalAsset.objects.count(), SavingsGoal.objects.count(),
                    FinancialFlow.objects.count(),
                )
                again = self.run_flow(key, payload, idem=idem)
                self.assertEqual(counts, (
                    Account.objects.count(), Loan.objects.count(), RecurringTransaction.objects.count(),
                    CapitalEvent.objects.count(), PhysicalAsset.objects.count(), SavingsGoal.objects.count(),
                    FinancialFlow.objects.count(),
                ))
                self.assertEqual(again.flow_id, first.flow_id)
                self.assertEqual({o.pk for o in again.created}, {o.pk for o in first.created})

    def test_running_a_flow_twice_with_new_keys_creates_two_sets(self):
        payload = valid_payloads(self)['loan']
        self.run_flow('loan', payload)
        self.run_flow('loan', {**payload, 'name': 'Second Loan'})
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 2)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='LOAN').count(), 2)

    def test_reusing_a_key_from_another_flow_is_rejected_not_silently_dropped(self):
        key = uuid.uuid4()
        self.run_flow('savingsgoal', valid_payloads(self)['savingsgoal'], idem=key)
        with self.assertRaises(ValidationError):
            self.run_flow('rentbill', valid_payloads(self)['rentbill'], idem=key)
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user, transaction_type='EXPENSE').exists())

    def test_same_key_for_different_users_is_independent(self):
        key = uuid.uuid4()
        self.run_flow('savingsgoal', valid_payloads(self)['savingsgoal'], idem=key)
        other = User.objects.create_user(username='other-e2e', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=other, defaults={'currency': '₹'})
        form = self.form('savingsgoal', valid_payloads(self)['savingsgoal'], user=other)
        self.assertTrue(form.is_valid())
        FlowRegistry.get('savingsgoal').commit(other, form.cleaned_data, key)
        self.assertEqual(SavingsGoal.objects.filter(user=other).count(), 1)
        self.assertEqual(SavingsGoal.objects.filter(user=self.user).count(), 1)

    def test_commit_is_all_or_nothing_when_a_later_step_fails(self):
        with patch.object(RecurringTransaction, 'save', side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                self.run_flow('loan', valid_payloads(self)['loan'])
        self.assertEqual(Loan.objects.count(), 0)
        self.assertEqual(LoanInterestRate.objects.count(), 0)
        self.assertEqual(FinancialFlow.objects.count(), 0)
        self.assertEqual(FlowCreatedObject.objects.count(), 0)

    def test_model_validation_failure_rolls_back_earlier_steps(self):
        """Car creates asset -> valuation -> vehicle account; a name clash on the account must undo the asset."""
        Account.objects.create(user=self.user, name='Honda City', account_type='VEHICLE', balance=Decimal('1'), currency='₹')
        before = PhysicalAsset.objects.count()
        with self.assertRaises(ValidationError):
            self.run_flow('car', valid_payloads(self)['car'])
        self.assertEqual(PhysicalAsset.objects.count(), before)
        self.assertEqual(AssetValuation.objects.count(), 0)
        self.assertEqual(FinancialFlow.objects.count(), 0)

    def test_deleting_a_flow_removes_everything_it_created(self):
        result = self.run_flow('loan', valid_payloads(self)['loan'])
        FinancialFlow.objects.get(pk=result.flow_id).delete()
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 0)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), 0)
        self.assertEqual(LoanInterestRate.objects.count(), 0)

    def test_count_steps_ignores_skipped_and_existing_object_steps(self):
        from expenses.flows.base import CreateStep
        steps = [
            CreateStep(Account, {'name': 'new'}),
            CreateStep(Account, {'pk': 5, 'name': 'existing'}),
            CreateStep(Account, {'name': 'skipped'}, condition=False),
            CreateStep(Loan, {'name': 'l'}),
        ]
        self.assertEqual(Flow.count_steps_by_model(steps), {Account: 1, Loan: 1})

    def test_format_review_value_handles_choices_bools_dates_decimals(self):
        from django import forms
        fmt = Flow._format_review_value
        choice_field = forms.ChoiceField(choices=[('A', 'Alpha')])
        self.assertEqual(fmt(choice_field, 'A'), 'Alpha')
        self.assertEqual(str(fmt(forms.BooleanField(), True)), 'Yes')
        self.assertEqual(str(fmt(forms.BooleanField(), False)), 'No')
        self.assertEqual(fmt(forms.DateField(), date(2026, 10, 1)), '01 Oct 2026')
        self.assertEqual(fmt(forms.DecimalField(), Decimal('1500')), '₹1,500')


# ---------------------------------------------------------------------------
# Recurrence helper the flows depend on
# ---------------------------------------------------------------------------
class TestLastDueBeforeToday(TestCase):
    def at(self, fake_today, start, frequency):
        with patch('expenses.services_recurring.timezone.localdate', return_value=fake_today):
            return RecurringService.last_due_before_today(start, frequency)

    def test_future_start_has_no_previous_occurrence(self):
        self.assertIsNone(self.at(date(2026, 10, 10), date(2026, 10, 11), 'MONTHLY'))

    def test_start_today_is_the_last_occurrence(self):
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 10, 10), 'MONTHLY'), date(2026, 10, 10))

    def test_monthly_keeps_the_day_of_month(self):
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 1, 1), 'MONTHLY'), date(2026, 10, 1))
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 1, 25), 'MONTHLY'), date(2026, 9, 25))

    def test_monthly_clamps_to_short_months(self):
        self.assertEqual(self.at(date(2026, 3, 15), date(2026, 1, 31), 'MONTHLY'), date(2026, 2, 28))
        self.assertEqual(self.at(date(2026, 3, 31), date(2026, 1, 31), 'MONTHLY'), date(2026, 3, 31))

    def test_quarterly_semiannual_yearly_use_calendar_months(self):
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 1, 1), 'QUARTERLY'), date(2026, 10, 1))
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 1, 20), 'QUARTERLY'), date(2026, 7, 20))
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 1, 1), 'SEMIANNUALLY'), date(2026, 7, 1))
        self.assertEqual(self.at(date(2026, 10, 10), date(2024, 4, 1), 'YEARLY'), date(2026, 4, 1))
        self.assertEqual(self.at(date(2026, 3, 1), date(2024, 4, 1), 'YEARLY'), date(2025, 4, 1))

    def test_day_based_frequencies_step_exact_days(self):
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 10, 1), 'DAILY'), date(2026, 10, 10))
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 9, 1), 'WEEKLY'), date(2026, 10, 6))
        self.assertEqual(self.at(date(2026, 10, 10), date(2026, 9, 1), 'BIWEEKLY'), date(2026, 9, 29))

    def test_next_due_after_the_result_is_always_in_the_future(self):
        """The invariant the flows rely on: historical off must never leave a past due date."""
        starts = [date(2025, 1, 31), date(2025, 2, 28), date(2026, 1, 1), date(2026, 1, 25), date(2024, 2, 29),
                  date(2026, 9, 30), date(2026, 10, 9)]
        todays = [date(2026, 2, 28), date(2026, 3, 1), date(2026, 10, 10), date(2026, 12, 31)]
        for frequency in ('DAILY', 'WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'SEMIANNUALLY', 'YEARLY'):
            for start in starts:
                for fake_today in todays:
                    last = self.at(fake_today, start, frequency)
                    if last is None:
                        continue
                    nxt = RecurringTransaction.get_next_date(last, frequency, start)
                    with self.subTest(frequency=frequency, start=start, today=fake_today):
                        self.assertLessEqual(last, fake_today)
                        self.assertGreaterEqual(last, start)
                        self.assertGreater(nxt, fake_today)


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------
class TestLoanFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['loan'], **overrides}

    # form
    def test_required_fields(self):
        form = self.form('loan', {})
        self.assertFalse(form.is_valid())
        for field in ('repayment_type', 'name', 'principal', 'annual_rate', 'tenure_months', 'start_date'):
            self.assertIn(field, form.errors)

    def test_rejects_non_positive_values(self):
        self.assertErrorOn('loan', self.payload(principal='0'), 'principal')
        self.assertErrorOn('loan', self.payload(annual_rate='-1'), 'annual_rate')
        self.assertErrorOn('loan', self.payload(tenure_months='0'), 'tenure_months')

    def test_schedule_requires_a_payment_account_unless_disabled(self):
        data = self.payload()
        data.pop('payment_account')
        self.assertErrorOn('loan', data, 'payment_account')
        data.pop('create_repayment_schedule')
        self.valid_form('loan', data)

    def test_payment_account_must_belong_to_user_and_be_active(self):
        other = User.objects.create_user(username='thief', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertErrorOn('loan', self.payload(payment_account=str(theirs.id)), 'payment_account')
        self.cash.is_active = False
        self.cash.save()
        self.assertErrorOn('loan', self.payload(), 'payment_account')

    def test_mid_tenure_needs_opening_paid_principal_below_principal(self):
        self.assertErrorOn('loan', self.payload(mid_tenure='on'), 'opening_paid_principal')
        self.assertErrorOn('loan', self.payload(mid_tenure='on', opening_paid_principal='1200000'), 'opening_paid_principal')
        self.assertErrorOn('loan', self.payload(mid_tenure='on', opening_paid_principal='9999999'), 'opening_paid_principal')
        self.valid_form('loan', self.payload(mid_tenure='on', opening_paid_principal='200000'))

    def test_down_payment_needs_an_amount(self):
        self.assertErrorOn('loan', self.payload(include_down_payment='on'), 'down_payment_amount')

    def test_zero_calculated_repayment_needs_an_explicit_amount(self):
        data = self.payload(repayment_type='INTEREST_ONLY', annual_rate='0')
        self.assertErrorOn('loan', data, 'repayment_amount')
        self.valid_form('loan', {**data, 'repayment_amount': '1000'})
        self.valid_form('loan', {k: v for k, v in data.items() if k != 'create_repayment_schedule'})

    # commit
    def test_creates_loan_rate_and_emi_with_correct_values(self):
        result = self.run_flow('loan', self.payload())
        self.assertEqual(len(result.created), 3)

        loan = self.one(result, Loan)
        self.assertEqual((loan.user, loan.name, loan.loan_type, loan.repayment_type),
                         (self.user, 'Home Loan', 'HOME', 'EMI'))
        self.assertEqual(loan.initial_principal, Decimal('1200000.00'))
        self.assertEqual(loan.duration_months, 120)
        self.assertEqual(loan.start_date, date(2026, 10, 1))
        self.assertEqual(loan.opening_paid_principal, Decimal('0.00'))
        self.assertEqual(loan.currency, '₹')
        self.assertTrue(loan.is_active)

        rate = self.one(result, LoanInterestRate)
        self.assertEqual((rate.loan, rate.interest_rate, rate.effective_date),
                         (loan, Decimal('8.50'), date(2026, 10, 1)))

        emi = self.one(result, RecurringTransaction)
        expected = Decimal(str(LoanService.calculate_emi(Decimal('1200000'), Decimal('8.5'), 120))).quantize(Decimal('0.01'))
        self.assertEqual(emi.amount, expected)
        self.assertEqual((emi.transaction_type, emi.loan, emi.account, emi.frequency, emi.start_date),
                         ('LOAN', loan, self.cash, 'MONTHLY', date(2026, 10, 1)))
        self.assertEqual(emi.description, 'Loan EMI: Home Loan')
        self.assertTrue(emi.is_active)
        self.assertFalse(CapitalEvent.objects.filter(user=self.user).exists())

    def test_zero_interest_loan_splits_principal_evenly(self):
        result = self.run_flow('loan', self.payload(principal='12000', annual_rate='0', tenure_months='12'))
        self.assertEqual(self.one(result, RecurringTransaction).amount, Decimal('1000.00'))

    def test_repayment_amount_override_wins_over_calculated_emi(self):
        emi = self.one(self.run_flow('loan', self.payload(repayment_amount='33333.33')), RecurringTransaction)
        self.assertEqual(emi.amount, Decimal('33333.33'))

    def test_interest_only_repayment_is_monthly_interest(self):
        result = self.run_flow('loan', self.payload(repayment_type='INTEREST_ONLY', principal='120000', annual_rate='12'))
        self.assertEqual(self.one(result, RecurringTransaction).amount, Decimal('1200.00'))
        self.assertEqual(self.one(result, Loan).repayment_type, 'INTEREST_ONLY')

    def test_schedule_start_prefers_repayment_start_then_first_emi_then_loan_start(self):
        rt = self.one(self.run_flow('loan', self.payload(repayment_start_date='2026-12-10', first_emi_date='2026-11-05')), RecurringTransaction)
        self.assertEqual(rt.start_date, date(2026, 12, 10))
        rt = self.one(self.run_flow('loan', self.payload(name='L2', first_emi_date='2026-11-05')), RecurringTransaction)
        self.assertEqual(rt.start_date, date(2026, 11, 5))
        rt = self.one(self.run_flow('loan', self.payload(name='L3')), RecurringTransaction)
        self.assertEqual(rt.start_date, date(2026, 10, 1))

    def test_every_repayment_frequency_is_accepted(self):
        for index, (freq, _label) in enumerate(RecurringTransaction.FREQUENCY_CHOICES):
            with self.subTest(frequency=freq):
                emi = self.one(self.run_flow('loan', self.payload(name=f'L{index}', repayment_frequency=freq)), RecurringTransaction)
                self.assertEqual(emi.frequency, freq)

    def test_inactive_schedule_flag_is_respected(self):
        data = self.payload()
        data.pop('repayment_is_active')
        # Unchecked checkbox == absent from POST == inactive.
        emi = self.one(self.run_flow('loan', data), RecurringTransaction)
        self.assertFalse(emi.is_active)
        emi = self.one(self.run_flow('loan', self.payload(name='Active', repayment_is_active='on')), RecurringTransaction)
        self.assertTrue(emi.is_active)

    def test_schedule_can_be_skipped(self):
        data = self.payload()
        data.pop('create_repayment_schedule')
        data.pop('payment_account')
        result = self.run_flow('loan', data)
        self.assertEqual(len(result.created), 2)
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user).exists())
        self.assertEqual(self.one(result, Loan).currency, '₹')

    def test_loan_currency_follows_payment_account(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=1, currency='$')
        result = self.run_flow('loan', self.payload(payment_account=str(usd.id)))
        self.assertEqual(self.one(result, Loan).currency, '$')
        self.assertEqual(self.one(result, RecurringTransaction).currency, '$')

    def test_mid_tenure_records_opening_paid_principal(self):
        loan = self.one(self.run_flow('loan', self.payload(mid_tenure='on', opening_paid_principal='300000')), Loan)
        self.assertEqual(loan.opening_paid_principal, Decimal('300000.00'))

    def test_down_payment_creates_linked_capital_event(self):
        bank = str(self.bank.id)
        result = self.run_flow('loan', self.payload(include_down_payment='on', down_payment_amount='250000', down_payment_account=bank))
        event = self.one(result, CapitalEvent)
        self.assertEqual((event.amount, event.subtype, event.linked_loan, event.account, event.date, event.currency),
                         (Decimal('250000.00'), 'loan_down_payment', self.one(result, Loan), self.bank, date(2026, 10, 1), '₹'))
        self.assertEqual(event.note, 'Loan down payment for Home Loan')

    def test_down_payment_custom_note(self):
        result = self.run_flow('loan', self.payload(include_down_payment='on', down_payment_amount='1000', custom_note='Booking amount'))
        self.assertEqual(self.one(result, CapitalEvent).note, 'Booking amount')

    def test_down_payment_amount_ignored_when_toggle_is_off(self):
        result = self.run_flow('loan', self.payload(down_payment_amount='1000'))
        self.assertEqual(self.created(result, CapitalEvent), [])

    # scheduling
    def test_historical_off_leaves_only_future_dues(self):
        start = today() - relativedelta(months=6, days=3)
        rt = self.one(self.run_flow('loan', self.payload(start_date=start.isoformat())), RecurringTransaction)
        self.assertSchedulePending(rt)

    def test_historical_on_via_view_posts_missed_emis(self):
        start = today() - relativedelta(months=3)
        response = self.post_commit('loan', self.payload(start_date=start.isoformat(), create_historical_entries='on'))
        self.assertEqual(response.status_code, 204)
        rt = RecurringTransaction.objects.get(user=self.user, transaction_type='LOAN')
        self.assertIsNotNone(rt.last_processed_date)
        self.assertGreater(rt.next_due_date, today())
        loan = Loan.objects.get(user=self.user)
        self.assertGreaterEqual(loan.repayments.count(), 3)

    def test_future_start_has_nothing_processed(self):
        start = today() + timedelta(days=20)
        rt = self.one(self.run_flow('loan', self.payload(start_date=start.isoformat())), RecurringTransaction)
        rt.refresh_from_db()
        self.assertIsNone(rt.last_processed_date)
        self.assertEqual(rt.next_due_date, start)


# ---------------------------------------------------------------------------
# Credit card
# ---------------------------------------------------------------------------
class TestCreditCardFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['creditcard'], **overrides}

    def test_new_card_is_stored_as_a_liability(self):
        card = self.one(self.run_flow('creditcard', self.payload(is_pinned='on')), Account)
        self.assertEqual((card.name, card.account_type, card.currency), ('HDFC Card', 'CREDIT_CARD', '₹'))
        self.assertEqual(card.balance, Decimal('-5000.00'))
        self.assertEqual(card.credit_limit, Decimal('100000.00'))
        self.assertEqual(card.credit_card_billing_day, 5)
        self.assertTrue(card.is_pinned)
        self.assertTrue(card.is_active)

    def test_validation(self):
        self.assertErrorOn('creditcard', self.payload(name=''), 'name')
        self.assertErrorOn('creditcard', self.payload(billing_day='0'), 'billing_day')
        self.assertErrorOn('creditcard', self.payload(billing_day='32'), 'billing_day')
        self.assertErrorOn('creditcard', self.payload(balance='-1'), 'balance')
        self.assertErrorOn('creditcard', self.payload(credit_limit='-1'), 'credit_limit')
        self.assertErrorOn('creditcard', self.payload(currency='XXX'), 'currency')
        self.assertErrorOn('creditcard', {}, 'balance')

    def test_every_billing_day_boundary_is_accepted(self):
        for day in ('1', '28', '31'):
            self.valid_form('creditcard', self.payload(billing_day=day))

    def test_zero_balance_card(self):
        card = self.one(self.run_flow('creditcard', self.payload(balance='0')), Account)
        self.assertEqual(card.balance, Decimal('0.00'))

    def test_updating_an_existing_card_edits_in_place(self):
        existing = Account.objects.create(user=self.user, name='Old Visa', account_type='CREDIT_CARD',
                                          balance=Decimal('-100'), currency='₹', credit_limit=Decimal('1000'),
                                          credit_card_billing_day=1)
        before = Account.objects.count()
        data = self.payload(existing_account=str(existing.id), name='', balance='2500', credit_limit='50000', billing_day='20')
        result = self.run_flow('creditcard', data)
        self.assertEqual(Account.objects.count(), before)
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Old Visa')
        self.assertEqual((existing.balance, existing.credit_limit, existing.credit_card_billing_day),
                         (Decimal('-2500.00'), Decimal('50000.00'), 20))
        self.assertEqual(result.created[0].pk, existing.pk)

    def test_updating_an_existing_card_can_rename_it(self):
        existing = Account.objects.create(user=self.user, name='Old Visa', account_type='CREDIT_CARD', balance=0, currency='₹')
        self.run_flow('creditcard', self.payload(existing_account=str(existing.id), name='Renamed'))
        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Renamed')

    def test_existing_account_must_be_own_active_credit_card(self):
        self.assertErrorOn('creditcard', self.payload(existing_account=str(self.cash.id)), 'existing_account')
        other = User.objects.create_user(username='cc-other', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CREDIT_CARD', balance=0, currency='₹')
        self.assertErrorOn('creditcard', self.payload(existing_account=str(theirs.id)), 'existing_account')

    def test_card_creation_consumes_account_quota_but_update_does_not(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        flow = FlowRegistry.get('creditcard')
        existing = Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', balance=0, currency='₹')
        form = self.valid_form('creditcard', self.payload(existing_account=str(existing.id)))
        steps = flow.plan(flow.derive({**form.cleaned_data, 'user': self.user}))
        self.assertEqual(flow.check_limits(self.user, steps), [])  # updating never trips the limit


# ---------------------------------------------------------------------------
# Salary
# ---------------------------------------------------------------------------
class TestSalaryFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['salary'], **overrides}

    def test_first_pay_date(self):
        self.assertEqual(first_pay_date(date(2026, 7, 1), 25), date(2026, 7, 25))
        self.assertEqual(first_pay_date(date(2026, 7, 25), 25), date(2026, 7, 25))
        self.assertEqual(first_pay_date(date(2026, 7, 26), 25), date(2026, 8, 25))
        self.assertEqual(first_pay_date(date(2026, 12, 30), 5), date(2027, 1, 5))
        self.assertEqual(first_pay_date(date(2026, 2, 1), 31), date(2026, 3, 31))
        self.assertEqual(first_pay_date(date(2026, 4, 1), 31), date(2026, 5, 31))
        self.assertEqual(first_pay_date(date(2026, 4, 10), None), date(2026, 4, 10))

    def test_validation(self):
        self.assertErrorOn('salary', self.payload(amount='0'), 'amount')
        self.assertErrorOn('salary', self.payload(salary_date='0'), 'salary_date')
        self.assertErrorOn('salary', self.payload(salary_date='32'), 'salary_date')
        self.assertErrorOn('salary', {k: v for k, v in self.payload().items() if k != 'start_date'}, 'start_date')
        self.assertErrorOn('salary', self.payload(currency='nope'), 'currency')

    def test_creates_income_schedule_on_the_pay_day(self):
        rt = self.one(self.run_flow('salary', self.payload(salary_date='25', start_date='2026-10-01')), RecurringTransaction)
        self.assertEqual((rt.transaction_type, rt.source, rt.frequency, rt.account, rt.amount, rt.currency),
                         ('INCOME', 'Salary', 'MONTHLY', self.cash, Decimal('80000.00'), '₹'))
        self.assertEqual(rt.start_date, date(2026, 10, 25), 'first pay date must match the pay day, not the tracking start')
        self.assertEqual(rt.description, 'Salary income')
        self.assertTrue(rt.is_active)

    def test_saves_pay_day_on_the_profile(self):
        self.run_flow('salary', self.payload(salary_date='27'))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.salary_date, 27)

    def test_account_is_optional_and_currency_defaults_to_profile(self):
        data = self.payload()
        data.pop('account')
        rt = self.one(self.run_flow('salary', data), RecurringTransaction)
        self.assertIsNone(rt.account)
        self.assertEqual(rt.currency, '₹')

    def test_currency_defaults_to_account_currency(self):
        eur = Account.objects.create(user=self.user, name='EUR', account_type='SAVINGS_ACCOUNT', balance=1, currency='€')
        rt = self.one(self.run_flow('salary', self.payload(account=str(eur.id))), RecurringTransaction)
        self.assertEqual(rt.currency, '€')

    def test_account_must_belong_to_user(self):
        other = User.objects.create_user(username='sal-other', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertErrorOn('salary', self.payload(account=str(theirs.id)), 'account')

    def test_historical_off_posts_no_income_and_next_pay_is_upcoming(self):
        start = today() - relativedelta(months=3)
        rt = self.one(self.run_flow('salary', self.payload(start_date=start.isoformat(), salary_date=str(start.day))), RecurringTransaction)
        self.assertSchedulePending(rt)
        self.assertFalse(Income.objects.filter(user=self.user, source='Salary').exists())

    def test_historical_on_posts_one_income_per_missed_month(self):
        start = today() - relativedelta(months=3)
        rt = self.one(self.run_flow('salary', self.payload(
            start_date=start.isoformat(), salary_date=str(start.day), create_historical_entries='on')), RecurringTransaction)
        incomes = Income.objects.filter(user=self.user, source='Salary', account=self.cash)
        self.assertEqual(incomes.count(), 4)  # start month + 3 more, inclusive of today's month if due
        self.assertEqual(set(incomes.values_list('amount', flat=True)), {Decimal('80000.00')})
        rt.refresh_from_db()
        self.assertGreater(rt.next_due_date, today())

    def test_historical_catchup_does_not_double_post_when_replayed(self):
        start = today() - relativedelta(months=2)
        data = self.payload(start_date=start.isoformat(), salary_date=str(start.day), create_historical_entries='on')
        idem = uuid.uuid4()
        self.run_flow('salary', data, idem=idem)
        count = Income.objects.filter(user=self.user, source='Salary').count()
        self.run_flow('salary', data, idem=idem)
        self.assertEqual(Income.objects.filter(user=self.user, source='Salary').count(), count)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, source='Salary').count(), 1)

    def test_replay_after_a_profile_edit_does_not_revert_it(self):
        idem = uuid.uuid4()
        self.run_flow('salary', self.payload(salary_date='1'), idem=idem)
        profile = self.user.profile
        profile.salary_date = 7
        profile.save()
        self.run_flow('salary', self.payload(salary_date='1'), idem=idem)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.salary_date, 7)


# ---------------------------------------------------------------------------
# Rent / Bill
# ---------------------------------------------------------------------------
class TestRentBillFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['rentbill'], **overrides}

    def test_creates_expense_schedule(self):
        rt = self.one(self.run_flow('rentbill', self.payload()), RecurringTransaction)
        self.assertEqual((rt.transaction_type, rt.amount, rt.category, rt.description, rt.frequency,
                          rt.account, rt.currency, rt.start_date, rt.is_active),
                         ('EXPENSE', Decimal('25000.00'), 'Rent', 'Flat rent', 'MONTHLY',
                          self.cash, '₹', date(2026, 10, 5), True))

    def test_validation(self):
        self.assertErrorOn('rentbill', self.payload(description=''), 'description')
        self.assertErrorOn('rentbill', self.payload(amount='0'), 'amount')
        self.assertErrorOn('rentbill', self.payload(frequency='FORTNIGHTLY'), 'frequency')
        self.assertErrorOn('rentbill', {k: v for k, v in self.payload().items() if k != 'start_date'}, 'start_date')

    def test_custom_category_and_blank_category_defaults_to_rent(self):
        rt = self.one(self.run_flow('rentbill', self.payload(category='Electricity')), RecurringTransaction)
        self.assertEqual(rt.category, 'Electricity')
        rt = self.one(self.run_flow('rentbill', self.payload(description='Other', category='')), RecurringTransaction)
        self.assertEqual(rt.category, 'Rent')

    def test_every_frequency_commits_with_a_future_next_due(self):
        start = (today() - relativedelta(months=14, days=5)).isoformat()
        for index, (freq, _label) in enumerate(RecurringTransaction.FREQUENCY_CHOICES):
            with self.subTest(frequency=freq):
                rt = self.one(self.run_flow('rentbill', self.payload(description=f'Bill {index}', frequency=freq, start_date=start)),
                              RecurringTransaction)
                self.assertEqual(rt.frequency, freq)
                self.assertSchedulePending(rt)

    def test_account_is_optional(self):
        data = self.payload()
        data.pop('account')
        self.assertIsNone(self.one(self.run_flow('rentbill', data), RecurringTransaction).account)

    def test_currency_comes_from_account_when_not_given(self):
        gbp = Account.objects.create(user=self.user, name='GBP', account_type='SAVINGS_ACCOUNT', balance=1, currency='£')
        rt = self.one(self.run_flow('rentbill', self.payload(account=str(gbp.id))), RecurringTransaction)
        self.assertEqual(rt.currency, '£')

    def test_preview_annualises_by_frequency_in_the_users_currency(self):
        flow = FlowRegistry.get('rentbill')
        expected = {'DAILY': 365, 'WEEKLY': 52, 'BIWEEKLY': 26, 'MONTHLY': 12, 'QUARTERLY': 4, 'SEMIANNUALLY': 2, 'YEARLY': 1}
        for freq, times in expected.items():
            form = self.valid_form('rentbill', self.payload(amount='100', frequency=freq))
            preview = flow.preview(self.user, form.cleaned_data)
            self.assertEqual(preview['headline'], 100.0)
            self.assertIn(f"₹{100 * times:,.2f}", str(preview['bullets'][1]), freq)

    def test_preview_uses_non_rupee_currency_symbol(self):
        gbp = Account.objects.create(user=self.user, name='GBP', account_type='SAVINGS_ACCOUNT', balance=1, currency='£')
        form = self.valid_form('rentbill', self.payload(amount='100', account=str(gbp.id)))
        bullet = str(FlowRegistry.get('rentbill').preview(self.user, form.cleaned_data)['bullets'][1])
        self.assertIn('£1,200.00', bullet)
        self.assertNotIn('₹', bullet)

    def test_historical_on_posts_expenses(self):
        from expenses.models import Expense
        start = today() - relativedelta(months=2)
        self.post_commit('rentbill', self.payload(start_date=start.isoformat(), create_historical_entries='on'))
        rt = RecurringTransaction.objects.get(user=self.user, transaction_type='EXPENSE')
        self.assertGreaterEqual(Expense.objects.filter(user=self.user, amount=Decimal('25000.00')).count(), 2)
        self.assertGreater(rt.next_due_date, today())

    def test_historical_off_posts_nothing(self):
        from expenses.models import Expense
        start = today() - relativedelta(months=2)
        rt = self.one(self.run_flow('rentbill', self.payload(start_date=start.isoformat())), RecurringTransaction)
        self.assertSchedulePending(rt)
        self.assertFalse(Expense.objects.filter(user=self.user, amount=Decimal('25000.00')).exists())


# ---------------------------------------------------------------------------
# Insurance
# ---------------------------------------------------------------------------
class TestInsuranceFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['insurance'], **overrides}

    def test_frequency_mapping(self):
        self.assertEqual(_premium_to_recurring_frequency('ANNUAL'), 'YEARLY')
        self.assertEqual(_premium_to_recurring_frequency('SEMI_ANNUAL'), 'SEMIANNUALLY')
        self.assertEqual(_premium_to_recurring_frequency('QUARTERLY'), 'QUARTERLY')
        self.assertEqual(_premium_to_recurring_frequency('MONTHLY'), 'MONTHLY')
        self.assertEqual(_premium_to_recurring_frequency(None), 'YEARLY')

    def test_validation(self):
        self.assertErrorOn('insurance', self.payload(name=''), 'name')
        self.assertErrorOn('insurance', self.payload(premium_amount='0'), 'premium_amount')
        self.assertErrorOn('insurance', self.payload(premium_frequency='WEEKLY'), 'premium_frequency')
        self.assertErrorOn('insurance', self.payload(sum_assured='-5'), 'sum_assured')

    def test_creates_policy_account_and_premium_schedule_all_linked(self):
        result = self.run_flow('insurance', self.payload(policy_number='POL-123', sum_assured='10000000'))
        self.assertEqual(len(result.created), 3)
        asset = self.one(result, PhysicalAsset)
        self.assertEqual((asset.asset_class, asset.name, asset.policy_number, asset.premium_amount,
                          asset.premium_frequency, asset.policy_start_date, asset.sum_assured, asset.currency),
                         ('INSURANCE', 'Term Plan', 'POL-123', Decimal('12000.00'), 'ANNUAL',
                          date(2026, 10, 1), Decimal('10000000.00'), '₹'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.name, account.linked_physical_asset, account.balance),
                         ('LIFE_INSURANCE', 'Term Plan', asset, Decimal('0.00')))
        rt = self.one(result, RecurringTransaction)
        self.assertEqual((rt.transaction_type, rt.physical_asset, rt.account, rt.amount, rt.frequency, rt.start_date),
                         ('INSURANCE_PREMIUM', asset, self.cash, Decimal('12000.00'), 'YEARLY', date(2026, 10, 1)))
        self.assertEqual(rt.description, 'Term Plan premium')

    def test_each_premium_frequency_maps_to_the_matching_schedule(self):
        mapping = {'ANNUAL': 'YEARLY', 'SEMI_ANNUAL': 'SEMIANNUALLY', 'QUARTERLY': 'QUARTERLY', 'MONTHLY': 'MONTHLY'}
        for index, (premium, recurring) in enumerate(mapping.items()):
            with self.subTest(premium_frequency=premium):
                result = self.run_flow('insurance', self.payload(name=f'Policy {index}', premium_frequency=premium))
                self.assertEqual(self.one(result, RecurringTransaction).frequency, recurring)
                self.assertEqual(self.one(result, PhysicalAsset).premium_frequency, premium)

    def test_preview_headline_is_the_annual_cost(self):
        flow = FlowRegistry.get('insurance')
        for premium, factor in {'ANNUAL': 1, 'SEMI_ANNUAL': 2, 'QUARTERLY': 4, 'MONTHLY': 12}.items():
            form = self.valid_form('insurance', self.payload(premium_frequency=premium, premium_amount='1000'))
            self.assertEqual(flow.preview(self.user, form.cleaned_data)['headline'], 1000.0 * factor)

    def test_payment_account_optional_and_drives_currency(self):
        data = self.payload()
        data.pop('premium_payment_account')
        result = self.run_flow('insurance', data)
        self.assertIsNone(self.one(result, RecurringTransaction).account)
        self.assertEqual(self.one(result, PhysicalAsset).currency, '₹')

    def test_insurance_account_name_clash_rolls_back_the_policy(self):
        Account.objects.create(user=self.user, name='Term Plan', account_type='LIFE_INSURANCE', balance=0, currency='₹')
        with self.assertRaises(ValidationError):
            self.run_flow('insurance', self.payload())
        self.assertFalse(PhysicalAsset.objects.filter(user=self.user, asset_class='INSURANCE').exists())
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user).exists())

    def test_historical_off_schedule_is_in_the_future(self):
        start = (today() - relativedelta(months=20)).isoformat()
        rt = self.one(self.run_flow('insurance', self.payload(start_date=start)), RecurringTransaction)
        self.assertSchedulePending(rt)


# ---------------------------------------------------------------------------
# SIP / RD
# ---------------------------------------------------------------------------
class TestSipRdFlowEndToEnd(FlowTestBase):
    def sip(self, **overrides):
        return {**valid_payloads(self)['sip'], **overrides}

    def rd(self, **overrides):
        data = dict(instrument_type='RD', name='Post Office RD', amount='3000', frequency='MONTHLY',
                    deposit_start_date='2026-10-10', deposit_principal='3000', deposit_rate='6.7',
                    deposit_compounding='QUARTERLY', deposit_maturity_date='2031-10-10', rd_installment_day='10',
                    from_account=str(self.cash.id))
        data.update(overrides)
        return data

    def test_sip_creates_investment_account_and_transfer(self):
        result = self.run_flow('sip', self.sip(is_pinned='on', end_date='2030-10-15'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.name, account.balance, account.currency, account.is_pinned),
                         ('MUTUAL_FUND', 'Nifty 50', Decimal('0.00'), '₹', True))
        rt = self.one(result, RecurringTransaction)
        self.assertEqual((rt.transaction_type, rt.from_account, rt.to_account, rt.amount, rt.frequency,
                          rt.start_date, rt.end_date),
                         ('TRANSFER', self.cash, account, Decimal('5000.00'), 'MONTHLY', date(2026, 10, 15), date(2030, 10, 15)))
        self.assertEqual(rt.description, 'Investment contribution: Nifty 50')

    def test_sip_validation(self):
        self.assertErrorOn('sip', self.sip(instrument_type='STOCK'), 'instrument_type')
        self.assertErrorOn('sip', self.sip(amount='0'), 'amount')
        self.assertErrorOn('sip', self.sip(name=''), 'name')
        self.assertErrorOn('sip', self.sip(frequency='HOURLY'), 'frequency')
        self.assertErrorOn('sip', self.sip(end_date='2026-01-01'), 'end_date')

    def test_sip_start_date_defaults_to_today(self):
        data = self.sip()
        data.pop('deposit_start_date')
        rt = self.one(self.run_flow('sip', data), RecurringTransaction)
        self.assertEqual(rt.start_date, today())

    def test_sip_without_funding_account_uses_profile_currency(self):
        profile = self.user.profile
        profile.currency = '€'
        profile.save()
        data = self.sip()
        data.pop('from_account')
        result = self.run_flow('sip', data)
        self.assertEqual({o.currency for o in result.created}, {'€'})

    def test_funding_account_must_belong_to_user(self):
        other = User.objects.create_user(username='sip-other', password='x')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='CASH_WALLET', balance=0, currency='₹')
        self.assertErrorOn('sip', self.sip(from_account=str(theirs.id)), 'from_account')

    def test_sip_frequencies(self):
        for index, (freq, _label) in enumerate(RecurringTransaction.FREQUENCY_CHOICES):
            with self.subTest(frequency=freq):
                result = self.run_flow('sip', self.sip(name=f'Fund {index}', frequency=freq))
                self.assertEqual(self.one(result, RecurringTransaction).frequency, freq)

    def test_rd_creates_deposit_account_with_installment_details(self):
        result = self.run_flow('sip', self.rd(show_accrued_balance='on', record_maturity_income='on'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.deposit_principal, account.deposit_rate,
                          account.deposit_start_date, account.deposit_maturity_date, account.deposit_compounding,
                          account.rd_installment_amount, account.rd_installment_day),
                         ('RD', Decimal('3000.00'), Decimal('6.70'), date(2026, 10, 10), date(2031, 10, 10),
                          'QUARTERLY', Decimal('3000.00'), 10))
        self.assertTrue(account.show_accrued_balance)
        self.assertTrue(account.record_maturity_income)
        rt = self.one(result, RecurringTransaction)
        self.assertEqual((rt.from_account, rt.to_account, rt.start_date), (self.cash, account, date(2026, 10, 10)))

    def test_rd_flags_default_off_when_unchecked(self):
        account = self.one(self.run_flow('sip', self.rd()), Account)
        self.assertFalse(account.show_accrued_balance)
        self.assertFalse(account.record_maturity_income)

    def test_rd_requires_its_extra_fields(self):
        for field in ('deposit_principal', 'deposit_rate', 'deposit_start_date', 'rd_installment_day'):
            with self.subTest(missing=field):
                data = self.rd()
                data.pop(field)
                self.assertErrorOn('sip', data, field)

    def test_sip_does_not_require_rd_fields(self):
        self.valid_form('sip', self.sip())

    def test_rd_installment_day_bounds(self):
        self.assertErrorOn('sip', self.rd(rd_installment_day='29'), 'rd_installment_day')
        self.assertErrorOn('sip', self.rd(rd_installment_day='0'), 'rd_installment_day')

    def test_rd_date_ordering(self):
        self.assertErrorOn('sip', self.rd(deposit_maturity_date='2026-10-10'), 'deposit_maturity_date')
        self.assertErrorOn('sip', self.rd(deposit_maturity_date='2026-01-01'), 'deposit_maturity_date')
        self.assertErrorOn('sip', self.rd(deposit_closed_date='2026-01-01'), 'deposit_closed_date')
        self.assertErrorOn('sip', self.rd(end_date='2026-01-01'), 'end_date')

    def test_sip_historical_off_vs_on(self):
        start = (today() - relativedelta(months=5, days=2)).isoformat()
        rt = self.one(self.run_flow('sip', self.sip(deposit_start_date=start)), RecurringTransaction)
        self.assertSchedulePending(rt)

        self.post_commit('sip', self.sip(name='Backfilled', deposit_start_date=start, create_historical_entries='on'))
        rt = RecurringTransaction.objects.get(user=self.user, description='Investment contribution: Backfilled')
        self.assertGreater(rt.next_due_date, today())
        self.assertIsNotNone(rt.last_processed_date)

    def test_commit_with_unlimited_plan_creates_exactly_two_objects(self):
        self.assertEqual(len(self.run_flow('sip', self.sip()).created), 2)


# ---------------------------------------------------------------------------
# Fixed deposit
# ---------------------------------------------------------------------------
class TestFdFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['fd'], **overrides}

    def test_creates_fd_account_and_funding_event(self):
        result = self.run_flow('fd', self.payload(show_accrued_balance='on', is_pinned='on', custom_note='Diwali FD'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.name, account.balance, account.deposit_principal,
                          account.deposit_rate, account.deposit_start_date, account.deposit_maturity_date,
                          account.deposit_compounding, account.currency),
                         ('FD', 'SBI FD', Decimal('0.00'), Decimal('100000.00'), Decimal('7.00'),
                          date(2026, 10, 1), date(2027, 10, 1), 'QUARTERLY', '₹'))
        self.assertTrue(account.show_accrued_balance)
        self.assertTrue(account.is_pinned)
        self.assertFalse(account.record_maturity_income)
        event = self.one(result, CapitalEvent)
        self.assertEqual((event.amount, event.date, event.subtype, event.account, event.note, event.currency),
                         (Decimal('100000.00'), date(2026, 10, 1), 'investment_lump_sum', self.cash, 'Diwali FD', '₹'))
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user).exists())

    def test_default_note(self):
        self.assertEqual(self.one(self.run_flow('fd', self.payload()), CapitalEvent).note, 'FD investment')

    def test_funding_account_optional(self):
        data = self.payload()
        data.pop('from_account')
        event = self.one(self.run_flow('fd', data), CapitalEvent)
        self.assertIsNone(event.account)

    def test_validation(self):
        self.assertErrorOn('fd', self.payload(principal='0'), 'principal')
        self.assertErrorOn('fd', self.payload(annual_rate='-1'), 'annual_rate')
        self.assertErrorOn('fd', self.payload(deposit_compounding='HOURLY'), 'deposit_compounding')
        self.assertErrorOn('fd', {k: v for k, v in self.payload().items() if k != 'maturity_date'}, 'maturity_date')

    def test_maturity_must_be_after_start_and_close_not_before_it(self):
        self.assertErrorOn('fd', self.payload(maturity_date='2026-10-01'), 'maturity_date')
        self.assertErrorOn('fd', self.payload(maturity_date='2026-01-01'), 'maturity_date')
        self.assertErrorOn('fd', self.payload(deposit_closed_date='2026-09-30'), 'deposit_closed_date')
        self.valid_form('fd', self.payload(deposit_closed_date='2027-01-01'))

    def test_closed_date_is_stored(self):
        account = self.one(self.run_flow('fd', self.payload(deposit_closed_date='2027-03-01')), Account)
        self.assertEqual(account.deposit_closed_date, date(2027, 3, 1))

    def test_preview_headline_is_principal_plus_simple_interest(self):
        flow = FlowRegistry.get('fd')
        form = self.valid_form('fd', self.payload())
        interest = RecurringService.calculate_interest_for_days(Decimal('100000'), Decimal('7'), 365)
        self.assertEqual(flow.preview(self.user, form.cleaned_data)['headline'], float(Decimal('100000') + interest))

    def test_zero_rate_fd_is_allowed(self):
        account = self.one(self.run_flow('fd', self.payload(annual_rate='0')), Account)
        self.assertEqual(account.deposit_rate, Decimal('0.00'))


# ---------------------------------------------------------------------------
# PPF / EPF / NPS
# ---------------------------------------------------------------------------
class TestPpfEpfNpsFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['ppfepfnps'], **overrides}

    def test_each_scheme_type_creates_matching_account(self):
        for scheme in ('PPF', 'EPF', 'NPS'):
            with self.subTest(scheme=scheme):
                result = self.run_flow('ppfepfnps', self.payload(scheme_type=scheme, name=f'My {scheme}'))
                self.assertEqual(self.one(result, Account).account_type, scheme)

    def test_creates_account_and_yearly_transfer(self):
        result = self.run_flow('ppfepfnps', self.payload(show_accrued_balance='on', is_pinned='on', end_date='2040-04-01',
                                                         deposit_maturity_date='2041-04-01'))
        account = self.one(result, Account)
        self.assertEqual((account.name, account.balance, account.deposit_principal, account.deposit_rate,
                          account.deposit_start_date, account.deposit_maturity_date, account.deposit_compounding),
                         ('My PPF', Decimal('0.00'), Decimal('50000.00'), Decimal('7.10'),
                          date(2026, 4, 1), date(2041, 4, 1), 'ANNUAL'))
        self.assertTrue(account.is_pinned)
        rt = self.one(result, RecurringTransaction)
        self.assertEqual((rt.transaction_type, rt.frequency, rt.amount, rt.from_account, rt.to_account,
                          rt.start_date, rt.end_date),
                         ('TRANSFER', 'YEARLY', Decimal('150000.00'), self.cash, account, date(2026, 4, 1), date(2040, 4, 1)))
        self.assertEqual(rt.description, 'Annual investment contribution: My PPF')

    def test_zero_opening_principal_and_zero_rate_are_respected(self):
        """Regression: 0 used to be replaced by the annual amount / the 7.1% default."""
        account = self.one(self.run_flow('ppfepfnps', self.payload(deposit_principal='0', deposit_rate='0')), Account)
        self.assertEqual(account.deposit_principal, Decimal('0.00'))
        self.assertEqual(account.deposit_rate, Decimal('0.00'))

    def test_defaults_when_values_are_absent(self):
        flow = FlowRegistry.get('ppfepfnps')
        data = flow.derive({'user': self.user, 'annual_amount': Decimal('1000'), 'deposit_start_date': date(2026, 4, 1),
                            'from_account': self.cash})
        self.assertEqual(data['deposit_principal'], Decimal('1000'))
        self.assertEqual(data['deposit_rate'], Decimal('7.1'))
        self.assertEqual(data['deposit_maturity_date'], date(2026, 4, 1) + timedelta(days=5475))
        self.assertEqual(data['scheme_type'], 'PPF')

    def test_validation(self):
        self.assertErrorOn('ppfepfnps', self.payload(scheme_type='ULIP'), 'scheme_type')
        self.assertErrorOn('ppfepfnps', self.payload(annual_amount='0'), 'annual_amount')
        self.assertErrorOn('ppfepfnps', self.payload(deposit_principal='-1'), 'deposit_principal')
        self.assertErrorOn('ppfepfnps', self.payload(deposit_rate='-0.1'), 'deposit_rate')
        self.assertErrorOn('ppfepfnps', {k: v for k, v in self.payload().items() if k != 'deposit_start_date'}, 'deposit_start_date')

    def test_date_ordering(self):
        self.assertErrorOn('ppfepfnps', self.payload(deposit_maturity_date='2026-04-01'), 'deposit_maturity_date')
        self.assertErrorOn('ppfepfnps', self.payload(deposit_closed_date='2026-03-31'), 'deposit_closed_date')
        self.assertErrorOn('ppfepfnps', self.payload(end_date='2026-03-31'), 'end_date')

    def test_historical_off_schedule_is_in_the_future(self):
        start = (today() - relativedelta(years=3, months=2)).isoformat()
        rt = self.one(self.run_flow('ppfepfnps', self.payload(deposit_start_date=start)), RecurringTransaction)
        self.assertSchedulePending(rt)

    def test_historical_on_via_view_backfills_each_missed_year(self):
        start = (today() - relativedelta(years=2, months=1)).isoformat()
        self.post_commit('ppfepfnps', self.payload(deposit_start_date=start, create_historical_entries='on'))
        rt = RecurringTransaction.objects.get(user=self.user, transaction_type='TRANSFER')
        self.assertGreater(rt.next_due_date, today())
        self.assertIsNotNone(rt.last_processed_date)


# ---------------------------------------------------------------------------
# Savings goal
# ---------------------------------------------------------------------------
class TestSavingsGoalFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['savingsgoal'], **overrides}

    def test_creates_goal(self):
        goal = self.one(self.run_flow('savingsgoal', self.payload(current_amount='50000', icon='🏖️')), SavingsGoal)
        self.assertEqual((goal.name, goal.target_amount, goal.current_amount, goal.icon, goal.color, goal.currency),
                         ('Emergency', Decimal('300000.00'), Decimal('50000.00'), '🏖️', 'success', '₹'))

    def test_target_date_is_calendar_months_ahead(self):
        for months in (1, 6, 12, 24):
            with self.subTest(months=months):
                goal = self.one(self.run_flow('savingsgoal', self.payload(name=f'G{months}', target_months=str(months))), SavingsGoal)
                self.assertEqual(goal.target_date, today() + relativedelta(months=months))

    def test_twelve_months_is_one_full_year_not_360_days(self):
        goal = self.one(self.run_flow('savingsgoal', self.payload()), SavingsGoal)
        self.assertEqual(goal.target_date, today() + relativedelta(years=1))

    def test_defaults_for_optional_fields(self):
        data = self.payload()
        data.pop('current_amount')
        goal = self.one(self.run_flow('savingsgoal', data), SavingsGoal)
        self.assertEqual(goal.current_amount, Decimal('0.00'))
        self.assertEqual(goal.icon, '🎯')

    def test_validation(self):
        self.assertErrorOn('savingsgoal', self.payload(target_amount='0'), 'target_amount')
        self.assertErrorOn('savingsgoal', self.payload(target_months='0'), 'target_months')
        self.assertErrorOn('savingsgoal', self.payload(current_amount='-1'), 'current_amount')
        self.assertErrorOn('savingsgoal', self.payload(color='purple'), 'color')
        self.assertErrorOn('savingsgoal', self.payload(current_amount='300001'), 'current_amount')
        self.valid_form('savingsgoal', self.payload(current_amount='300000'))

    def test_preview_monthly_suggestion(self):
        flow = FlowRegistry.get('savingsgoal')
        form = self.valid_form('savingsgoal', self.payload(current_amount='60000', target_months='12'))
        preview = flow.preview(self.user, form.cleaned_data)
        self.assertEqual(preview['headline'], 300000.0)
        self.assertIn('20,000.00', str(preview['bullets'][1]))

    def test_suggestion_is_zero_once_goal_is_already_met(self):
        flow = FlowRegistry.get('savingsgoal')
        data = flow.derive({'user': self.user, 'target_amount': Decimal('1000'), 'current_amount': Decimal('1000'), 'target_months': 5})
        self.assertEqual(data['monthly_suggestion'], Decimal('0.00'))

    def test_goals_count_against_the_plan_limit(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        from finance_tracker.plans import get_limit
        limit = get_limit('FREE', 'savings_goals')
        for i in range(limit):
            SavingsGoal.objects.create(user=self.user, name=f'g{i}', target_amount=100, target_date=today(), currency='₹')
        response = self.post_commit('savingsgoal', self.payload(), hx=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SavingsGoal.objects.filter(user=self.user).count(), limit)


# ---------------------------------------------------------------------------
# Car
# ---------------------------------------------------------------------------
class TestCarFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['car'], **overrides}

    def financed(self, **overrides):
        return self.payload(financed='on', annual_rate='9', tenure_months='60', **overrides)

    def test_cash_purchase_creates_asset_valuation_account_and_capital_event(self):
        result = self.run_flow('car', self.payload(custom_note='Paid in cash', is_pinned='on'))
        self.assertEqual(len(result.created), 4)
        asset = self.one(result, PhysicalAsset)
        self.assertEqual((asset.asset_class, asset.name, asset.acquisition_cost, asset.acquisition_date, asset.currency),
                         ('VEHICLE', 'Honda City', Decimal('1200000.00'), date(2026, 10, 1), '₹'))
        valuation = self.one(result, AssetValuation)
        self.assertEqual((valuation.asset, valuation.value, valuation.as_of_date, valuation.source),
                         (asset, Decimal('1200000.00'), date(2026, 10, 1), 'Purchase'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.name, account.balance, account.linked_physical_asset, account.is_pinned),
                         ('VEHICLE', 'Honda City', Decimal('1200000.00'), asset, True))
        event = self.one(result, CapitalEvent)
        self.assertEqual((event.amount, event.subtype, event.account, event.date, event.note),
                         (Decimal('1200000.00'), 'large_purchase', self.cash, date(2026, 10, 1), 'Paid in cash'))
        self.assertFalse(Loan.objects.filter(user=self.user).exists())

    def test_cash_purchase_default_note_and_optional_account(self):
        data = self.payload()
        data.pop('from_account')
        event = self.one(self.run_flow('car', data), CapitalEvent)
        self.assertEqual(event.note, 'Car purchase')
        self.assertIsNone(event.account)

    def test_financed_purchase_creates_loan_emi_and_loan_account_but_no_capital_event(self):
        result = self.run_flow('car', self.financed())
        loan = self.one(result, Loan)
        self.assertEqual((loan.loan_type, loan.name, loan.initial_principal, loan.duration_months, loan.start_date),
                         ('CAR', 'Honda City Loan', Decimal('1200000.00'), 60, date(2026, 10, 1)))
        rate = self.one(result, LoanInterestRate)
        self.assertEqual((rate.loan, rate.interest_rate), (loan, Decimal('9.00')))
        emi = self.one(result, RecurringTransaction)
        expected = Decimal(str(LoanService.calculate_emi(Decimal('1200000'), Decimal('9'), 60))).quantize(Decimal('0.01'))
        self.assertEqual((emi.transaction_type, emi.loan, emi.account, emi.amount, emi.frequency),
                         ('LOAN', loan, self.cash, expected, 'MONTHLY'))
        accounts = {a.account_type: a for a in self.created(result, Account)}
        self.assertEqual(set(accounts), {'VEHICLE', 'VEHICLE_LOAN'})
        self.assertEqual(accounts['VEHICLE_LOAN'].linked_loan, loan)
        self.assertEqual(accounts['VEHICLE'].balance, Decimal('1200000.00'))
        self.assertEqual(self.created(result, CapitalEvent), [])
        self.assertEqual(self.one(result, PhysicalAsset).asset_class, 'VEHICLE')

    def test_loan_name_and_start_date_can_be_customised(self):
        result = self.run_flow('car', self.financed(loan_name='City EMI', loan_start_date='2026-11-15'))
        loan = self.one(result, Loan)
        self.assertEqual((loan.name, loan.start_date), ('City EMI', date(2026, 11, 15)))
        self.assertEqual(self.one(result, RecurringTransaction).start_date, date(2026, 11, 15))
        self.assertTrue(Account.objects.filter(user=self.user, name='City EMI', account_type='VEHICLE_LOAN').exists())

    def test_financed_requires_rate_and_tenure(self):
        self.assertErrorOn('car', self.payload(financed='on', tenure_months='60'), 'annual_rate')
        self.assertErrorOn('car', self.payload(financed='on', annual_rate='9'), 'tenure_months')
        self.valid_form('car', self.payload(tenure_months='60'))  # loan fields ignored when not financed

    def test_validation(self):
        self.assertErrorOn('car', self.payload(purchase_price='0'), 'purchase_price')
        self.assertErrorOn('car', self.payload(name=''), 'name')
        self.assertErrorOn('car', {k: v for k, v in self.payload().items() if k != 'acquisition_date'}, 'acquisition_date')

    def test_currency_follows_the_funding_account(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=1, currency='$')
        result = self.run_flow('car', self.financed(from_account=str(usd.id)))
        for obj in result.created:
            if hasattr(obj, 'currency'):
                self.assertEqual(obj.currency, '$', type(obj).__name__)

    def test_financed_purchase_counts_both_accounts_and_the_loan_for_limits(self):
        flow = FlowRegistry.get('car')
        form = self.valid_form('car', self.financed())
        steps = flow.plan(flow.derive({**form.cleaned_data, 'user': self.user}))
        counts = flow.count_steps_by_model(steps)
        self.assertEqual((counts[Account], counts[Loan], counts[RecurringTransaction]), (2, 1, 1))

    def test_historical_entries_for_financed_car(self):
        start = (today() - relativedelta(months=3)).isoformat()
        self.post_commit('car', self.financed(acquisition_date=start, create_historical_entries='on'))
        loan = Loan.objects.get(user=self.user)
        self.assertGreaterEqual(loan.repayments.count(), 3)
        rt = RecurringTransaction.objects.get(user=self.user, loan=loan)
        self.assertGreater(rt.next_due_date, today())

    def test_financed_historical_off_leaves_only_future_dues(self):
        start = (today() - relativedelta(months=3, days=4)).isoformat()
        rt = self.one(self.run_flow('car', self.financed(acquisition_date=start)), RecurringTransaction)
        self.assertSchedulePending(rt)


# ---------------------------------------------------------------------------
# Gold
# ---------------------------------------------------------------------------
class TestGoldFlowEndToEnd(FlowTestBase):
    def payload(self, **overrides):
        return {**valid_payloads(self)['gold'], **overrides}

    def test_physical_gold_creates_asset_valuation_account_holding_and_purchase(self):
        result = self.run_flow('gold', self.payload(is_pinned='on'))
        self.assertEqual(len(result.created), 5)
        asset = self.one(result, PhysicalAsset)
        self.assertEqual((asset.asset_class, asset.acquisition_cost, asset.acquisition_date),
                         ('GOLD', Decimal('200000.00'), date(2026, 10, 1)))
        valuation = self.one(result, AssetValuation)
        self.assertEqual((valuation.asset, valuation.value, valuation.source), (asset, Decimal('200000.00'), 'Purchase'))
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.balance, account.linked_physical_asset, account.is_pinned),
                         ('GOLD', Decimal('200000.00'), asset, True))
        holding = self.one(result, Holding)
        self.assertEqual((holding.account, holding.instrument_name, holding.units, holding.avg_cost),
                         (account, 'Gold Chain', Decimal('1.000000'), Decimal('200000.00')))
        event = self.one(result, CapitalEvent)
        self.assertEqual((event.amount, event.subtype, event.account, event.note),
                         (Decimal('200000.00'), 'investment_lump_sum', self.cash, 'Gold purchase: Gold Chain'))

    def test_digital_gold_creates_sgb_account_and_holding_without_asset(self):
        result = self.run_flow('gold', self.payload(route='digital', name='SGB 2031'))
        self.assertEqual(len(result.created), 3)
        account = self.one(result, Account)
        self.assertEqual((account.account_type, account.balance), ('SGB', Decimal('0.00')))
        holding = self.one(result, Holding)
        self.assertEqual((holding.account, holding.avg_cost), (account, Decimal('200000.00')))
        self.assertEqual(self.created(result, PhysicalAsset), [])
        self.assertEqual(self.created(result, AssetValuation), [])
        self.assertEqual(self.one(result, CapitalEvent).amount, Decimal('200000.00'))

    def test_no_funding_account_means_no_purchase_event(self):
        for route in ('physical', 'digital'):
            data = self.payload(route=route, name=f'Gold {route}')
            data.pop('from_account')
            result = self.run_flow('gold', data)
            self.assertEqual(self.created(result, CapitalEvent), [], route)
            self.assertEqual(self.one(result, Account).currency, '₹')

    def test_validation(self):
        self.assertErrorOn('gold', self.payload(route='silver'), 'route')
        self.assertErrorOn('gold', self.payload(amount='0'), 'amount')
        self.assertErrorOn('gold', self.payload(name=''), 'name')
        self.assertErrorOn('gold', {k: v for k, v in self.payload().items() if k != 'acquisition_date'}, 'acquisition_date')
        self.assertErrorOn('gold', {k: v for k, v in self.payload().items() if k != 'route'}, 'route')

    def test_currency_follows_funding_account(self):
        usd = Account.objects.create(user=self.user, name='USD', account_type='SAVINGS_ACCOUNT', balance=1, currency='$')
        result = self.run_flow('gold', self.payload(from_account=str(usd.id)))
        for obj in result.created:
            if hasattr(obj, 'currency'):
                self.assertEqual(obj.currency, '$', type(obj).__name__)


# ---------------------------------------------------------------------------
# HTTP layer shared by all flows
# ---------------------------------------------------------------------------
class TestFlowHttpLayer(FlowTestBase):
    def test_anonymous_users_are_sent_to_login_for_every_endpoint(self):
        self.client.logout()
        for key in FlowRegistry.all():
            for name, method in (('flow-detail', 'get'), ('flow-preview', 'post'), ('flow-commit', 'post')):
                response = getattr(self.client, method)(reverse(name, kwargs={'key': key}))
                with self.subTest(flow=key, endpoint=name):
                    self.assertEqual(response.status_code, 302)
                    self.assertIn('login', response.url)
        self.assertEqual(self.client.get(reverse('tmr-flows')).status_code, 302)
        self.assertEqual(FinancialFlow.objects.count(), 0)

    def test_unknown_flow_key_is_404(self):
        for name, method in (('flow-detail', 'get'), ('flow-preview', 'post'), ('flow-commit', 'post')):
            with self.subTest(endpoint=name):
                self.assertEqual(getattr(self.client, method)(reverse(name, kwargs={'key': 'nope'})).status_code, 404)

    def test_detail_page_renders_for_every_flow_with_an_idempotency_key(self):
        for key in FlowRegistry.all():
            with self.subTest(flow=key):
                response = self.client.get(reverse('flow-detail', kwargs={'key': key}))
                self.assertEqual(response.status_code, 200)
                self.assertRegex(response.content.decode(), r'name="idempotency_key" value="[0-9a-f-]{36}"')

    def test_idempotency_key_is_stable_per_flow_until_a_commit(self):
        self.assertEqual(self.session_key('loan'), self.session_key('loan'))
        self.assertNotEqual(self.session_key('loan'), self.session_key('salary'))

    def test_detail_post_renders_review_even_for_an_empty_form(self):
        for key in FlowRegistry.all():
            with self.subTest(flow=key):
                self.assertEqual(self.client.post(reverse('flow-detail', kwargs={'key': key}), {}).status_code, 200)

    def test_preview_never_creates_anything_and_survives_garbage_input(self):
        for key, payload in valid_payloads(self).items():
            with self.subTest(flow=key, case='valid'):
                self.assertEqual(self.client.post(reverse('flow-preview', kwargs={'key': key}), payload).status_code, 200)
            with self.subTest(flow=key, case='garbage'):
                junk = {name: 'zzz' for name in payload}
                self.assertEqual(self.client.post(reverse('flow-preview', kwargs={'key': key}), junk).status_code, 200)
        self.assertEqual(FinancialFlow.objects.count(), 0)

    def test_commit_with_invalid_form_shows_errors_and_creates_nothing(self):
        for key in FlowRegistry.all():
            with self.subTest(flow=key):
                response = self.post_commit(key, {}, hx=False)
                self.assertEqual(response.status_code, 200)
        self.assertEqual(FinancialFlow.objects.count(), 0)

    def test_invalid_commit_does_not_rotate_the_key(self):
        before = self.session_key('loan')
        self.post_commit('loan', {}, idem=before)
        self.assertEqual(self.session_key('loan'), before)

    def test_successful_htmx_commit_returns_204_with_redirect_header_and_message(self):
        response = self.post_commit('savingsgoal', valid_payloads(self)['savingsgoal'])
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response['HX-Redirect'], reverse('home'))
        messages = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any('Savings Goal created successfully' in m for m in messages), messages)

    def test_successful_plain_commit_redirects_home(self):
        response = self.post_commit('savingsgoal', valid_payloads(self)['savingsgoal'], hx=False)
        self.assertRedirects(response, reverse('home'), fetch_redirect_response=False)

    def test_every_flow_commits_through_the_view(self):
        for key, payload in valid_payloads(self).items():
            with self.subTest(flow=key):
                response = self.post_commit(key, payload)
                self.assertEqual(response.status_code, 204, getattr(response, 'content', b'')[:300])
                self.assertEqual(FinancialFlow.objects.filter(user=self.user, flow_key=key).count(), 1)

    def test_key_rotates_after_success_so_a_second_run_creates_a_second_set(self):
        payload = valid_payloads(self)['savingsgoal']
        first = self.session_key('savingsgoal')
        self.post_commit('savingsgoal', payload, idem=first)
        second = self.session_key('savingsgoal')
        self.assertNotEqual(first, second)
        self.post_commit('savingsgoal', {**payload, 'name': 'Another'}, idem=second)
        self.assertEqual(SavingsGoal.objects.filter(user=self.user).count(), 2)

    def test_double_submit_with_same_key_creates_one_set(self):
        payload = valid_payloads(self)['loan']
        key = self.session_key('loan')
        self.assertEqual(self.post_commit('loan', payload, idem=key).status_code, 204)
        self.assertEqual(self.post_commit('loan', payload, idem=key).status_code, 204)
        self.assertEqual(Loan.objects.filter(user=self.user).count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='LOAN').count(), 1)

    def test_missing_or_malformed_key_falls_back_to_the_session_key(self):
        payload = valid_payloads(self)['savingsgoal']
        session_key = self.session_key('savingsgoal')
        self.post_commit('savingsgoal', payload, idem='not-a-uuid')
        flow = FinancialFlow.objects.get(user=self.user)
        self.assertEqual(str(flow.idempotency_key), session_key)

        # A missing key is treated the same way: the session's key is used and recorded.
        loan_session_key = self.session_key('loan')
        self.post_commit('loan', valid_payloads(self)['loan'], idem=False)
        self.assertEqual(str(FinancialFlow.objects.get(user=self.user, flow_key='loan').idempotency_key), loan_session_key)

    def test_a_key_from_another_flow_is_rejected_without_a_crash(self):
        goal_key = self.session_key('savingsgoal')
        self.post_commit('savingsgoal', valid_payloads(self)['savingsgoal'], idem=goal_key)
        response = self.post_commit('rentbill', valid_payloads(self)['rentbill'], idem=goal_key, hx=False)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RecurringTransaction.objects.filter(user=self.user).exists())

    def test_exceeding_the_plan_limit_blocks_commit_and_explains_why(self):
        profile = self.user.profile
        profile.tier = 'FREE'
        profile.save()
        from finance_tracker.plans import get_limit
        limit = get_limit('FREE', 'recurring_transactions')
        for i in range(limit):
            RecurringTransaction.objects.create(
                user=self.user, transaction_type='EXPENSE', amount=1, currency='₹', account=self.cash,
                category='Rent', description=f'r{i}', frequency='MONTHLY', start_date=today(), is_active=True)
        response = self.post_commit('rentbill', valid_payloads(self)['rentbill'], hx=False)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'plan limit')
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), limit)
        self.assertFalse(FinancialFlow.objects.exists())

    def test_model_level_validation_error_is_shown_not_a_500(self):
        Account.objects.create(user=self.user, name='Honda City', account_type='VEHICLE', balance=1, currency='₹')
        response = self.post_commit('car', valid_payloads(self)['car'], hx=False)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PhysicalAsset.objects.filter(user=self.user, name='Honda City').exists())

    def test_users_cannot_commit_with_other_users_accounts(self):
        other = User.objects.create_user(username='victim', password='x')
        theirs = Account.objects.create(user=other, name='Victim', account_type='CASH_WALLET', balance=0, currency='₹')
        for key in ('loan', 'salary', 'rentbill', 'insurance', 'sip', 'fd', 'ppfepfnps', 'car', 'gold'):
            payload = dict(valid_payloads(self)[key])
            for field, value in payload.items():
                if value == str(self.cash.id):
                    payload[field] = str(theirs.id)
            with self.subTest(flow=key):
                response = self.post_commit(key, payload, hx=False)
                self.assertEqual(response.status_code, 200)
        self.assertFalse(FinancialFlow.objects.exists())

    def test_landing_lists_every_flow_and_reflects_setup_progress(self):
        response = self.client.get(reverse('tmr-flows'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_count'], 11)
        self.assertEqual(response.context['configured_count'], 0)
        for key, payload in valid_payloads(self).items():
            self.post_commit(key, payload)
        response = self.client.get(reverse('tmr-flows'))
        configured = {f['key'] for f in response.context['configured_flows']}
        self.assertEqual(configured, set(FlowRegistry.all()))
        self.assertEqual(response.context['pct_configured'], 100)
        for item in response.context['flows']:
            self.assertTrue(item['edit_url'], item['key'])
            self.assertTrue(item['setup_url'], item['key'])


# ---------------------------------------------------------------------------
# New-user and base-class edges
# ---------------------------------------------------------------------------
class TestFlowEdgeCases(FlowTestBase):
    def test_account_pickers_explain_what_to_do_when_the_user_has_no_accounts(self):
        Account.objects.filter(user=self.user).delete()
        for key, field in (('loan', 'payment_account'), ('salary', 'account'), ('rentbill', 'account'),
                           ('insurance', 'premium_payment_account'), ('sip', 'from_account'),
                           ('fd', 'from_account'), ('ppfepfnps', 'from_account'), ('car', 'from_account')):
            with self.subTest(flow=key):
                form = self.form(key, {})
                self.assertIn('No active accounts found', str(form.fields[field].help_text))
                self.assertEqual(form.fields[field].queryset.count(), 0)

    def test_new_user_with_no_accounts_can_still_run_account_optional_flows(self):
        Account.objects.filter(user=self.user).delete()
        goal = self.run_flow('savingsgoal', valid_payloads(self)['savingsgoal'])
        self.assertEqual(len(goal.created), 1)
        data = {k: v for k, v in valid_payloads(self)['salary'].items() if k != 'account'}
        self.assertEqual(len(self.run_flow('salary', data).created), 1)

    def test_picker_defaults_to_cash_then_first_account(self):
        form = self.form('salary', {})
        self.assertEqual(form.fields['account'].initial, self.cash)
        self.cash.delete()
        self.assertEqual(self.form('salary', {}).fields['account'].initial, self.bank)

    def test_unknown_registry_key_raises(self):
        with self.assertRaises(KeyError):
            FlowRegistry.get('does-not-exist')

    def test_base_flow_requires_plan_and_has_safe_defaults(self):
        base = Flow()
        with self.assertRaises(NotImplementedError):
            base.plan({})
        self.assertEqual(base.check_limits(self.user, []), [])
        self.assertFalse(base.is_configured(self.user))
        self.assertEqual(base.get_edit_url(self.user), '')
        self.assertEqual(base.preview(self.user, {}), {})
        self.assertEqual(base.get_wizard_steps(), [])

    def test_wizard_steps_cover_every_form_field_exactly_once(self):
        """A field missing from the wizard never reaches the review screen; a duplicate shows twice."""
        for key, flow in FlowRegistry.all().items():
            with self.subTest(flow=key):
                form_fields = set(flow.form_class(user=self.user).fields)
                listed = [name for step in flow.get_wizard_steps() for name in step.fields]
                self.assertEqual(len(listed), len(set(listed)), 'duplicate field across steps')
                self.assertEqual(set(listed), form_fields)

    def test_every_flow_declares_the_metadata_the_landing_page_needs(self):
        for key, flow in FlowRegistry.all().items():
            with self.subTest(flow=key):
                for attr in ('label', 'title', 'description', 'estimated_time', 'icon'):
                    self.assertTrue(str(getattr(flow, attr)), attr)
                self.assertTrue(flow.tags)
                self.assertTrue(flow.creates)
                self.assertIn(flow.category, ('debt', 'income', 'bills', 'savings', 'assets'))

    def test_review_screen_summarises_each_flow_with_cards_and_headline(self):
        for key, payload in valid_payloads(self).items():
            with self.subTest(flow=key):
                form = self.valid_form(key, payload)
                flow = FlowRegistry.get(key)
                review = flow.review_context(self.user, form, form.cleaned_data)
                self.assertTrue(review['what_we_create'], 'no "what we will create" cards')
                self.assertTrue(review['sections'], 'no answers listed')
                self.assertTrue(review['headline_suffix'])
                self.assertTrue(review['summary'])
                preview = flow.preview(self.user, form.cleaned_data)
                self.assertIn('headline', preview)
                self.assertEqual(preview.get('warnings', []), [])
