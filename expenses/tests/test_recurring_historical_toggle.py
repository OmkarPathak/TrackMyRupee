from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from expenses.models import Account, RecurringTransaction, Expense, CapitalEvent
from expenses.forms import RecurringTransactionForm
from expenses.flows.recurring import RentBillFlow
from expenses.flows.loan import NewLoanFlow
from expenses.flows.insurance import InsuranceFlow
from expenses.flows.investment import SipRdFlow, PpfEpfNpsFlow

User = get_user_model()


class RecurringHistoricalToggleTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='toggleuser', password='password123')
        self.user.profile.currency = '₹'
        self.user.profile.save()
        self.account = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH',
            balance=Decimal('100000.00'),
            currency='₹',
        )
        self.past_start = date.today() - timedelta(days=90) # ~3 months ago

    def test_recurring_transaction_form_historical_true(self):
        form_data = {
            'transaction_type': 'EXPENSE',
            'amount': '1000.00',
            'currency': '₹',
            'account': self.account.id,
            'category': 'Rent',
            'description': 'Past Rent Form',
            'frequency': 'MONTHLY',
            'start_date': self.past_start.strftime('%Y-%m-%d'),
            'create_historical_entries': True,
            'is_active': True,
        }
        form = RecurringTransactionForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.user = self.user
        rt = form.save()
        
        # Historical entries should be posted
        posted = Expense.objects.filter(user=self.user, description__icontains='Past Rent Form')
        self.assertGreater(posted.count(), 0)

    def test_recurring_transaction_form_historical_false(self):
        form_data = {
            'transaction_type': 'EXPENSE',
            'amount': '1000.00',
            'currency': '₹',
            'account': self.account.id,
            'category': 'Rent',
            'description': 'Future Rent Form',
            'frequency': 'MONTHLY',
            'start_date': self.past_start.strftime('%Y-%m-%d'),
            'create_historical_entries': False,
            'is_active': True,
        }
        form = RecurringTransactionForm(data=form_data, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        form.instance.user = self.user
        rt = form.save()

        posted = Expense.objects.filter(user=self.user, description='Future Rent Form')
        self.assertEqual(posted.count(), 0)
        self.assertIsNotNone(rt.last_processed_date)

    def test_rent_bill_flow_historical_toggle(self):
        flow = RentBillFlow()
        data_true = flow.derive({
            'user': self.user,
            'description': 'Rent Historical True',
            'amount': Decimal('5000.00'),
            'category': 'Rent',
            'currency': '₹',
            'frequency': 'MONTHLY',
            'account': self.account,
            'start_date': self.past_start,
            'create_historical_entries': True,
        })
        steps_true = flow.plan(data_true)
        rt_step = [s for s in steps_true if s.model == RecurringTransaction][0]
        self.assertIsNone(rt_step.fields['last_processed_date'])

        data_false = flow.derive({
            'user': self.user,
            'description': 'Rent Historical False',
            'amount': Decimal('5000.00'),
            'category': 'Rent',
            'currency': '₹',
            'frequency': 'MONTHLY',
            'account': self.account,
            'start_date': self.past_start,
            'create_historical_entries': False,
        })
        steps_false = flow.plan(data_false)
        rt_step_false = [s for s in steps_false if s.model == RecurringTransaction][0]
        self.assertIsNotNone(rt_step_false.fields['last_processed_date'])

    def test_loan_flow_historical_toggle(self):
        flow = NewLoanFlow()
        raw_true = {
            'user': self.user,
            'name': 'Loan Hist True',
            'loan_type': 'PERSONAL',
            'repayment_type': 'EMI',
            'principal': Decimal('50000.00'),
            'annual_rate': Decimal('10.00'),
            'tenure_months': 12,
            'start_date': self.past_start,
            'create_repayment_schedule': True,
            'payment_account': self.account,
            'repayment_frequency': 'MONTHLY',
            'repayment_start_date': self.past_start,
            'create_historical_entries': True,
            'repayment_is_active': True,
        }
        data_true = flow.derive(raw_true)
        steps_true = flow.plan(data_true)
        rt_step = [s for s in steps_true if s.model == RecurringTransaction][0]
        self.assertIsNone(rt_step.fields['last_processed_date'])

        data_false = flow.derive({**raw_true, 'create_historical_entries': False})
        steps_false = flow.plan(data_false)
        rt_step_false = [s for s in steps_false if s.model == RecurringTransaction][0]
        self.assertIsNotNone(rt_step_false.fields['last_processed_date'])

    def test_insurance_flow_historical_toggle(self):
        flow = InsuranceFlow()
        raw_true = {
            'user': self.user,
            'name': 'Health Ins True',
            'premium_amount': Decimal('12000.00'),
            'premium_frequency': 'ANNUAL',
            'premium_payment_account': self.account,
            'start_date': self.past_start,
            'create_historical_entries': True,
        }
        data_true = flow.derive(raw_true)
        steps_true = flow.plan(data_true)
        rt_step = [s for s in steps_true if s.model == RecurringTransaction][0]
        self.assertIsNone(rt_step.fields['last_processed_date'])

        data_false = flow.derive({**raw_true, 'create_historical_entries': False})
        steps_false = flow.plan(data_false)
        rt_step_false = [s for s in steps_false if s.model == RecurringTransaction][0]
        self.assertIsNotNone(rt_step_false.fields['last_processed_date'])

    def test_sip_rd_flow_historical_toggle(self):
        flow = SipRdFlow()
        raw_true = {
            'user': self.user,
            'instrument_type': 'SIP',
            'name': 'SIP True',
            'amount': Decimal('2000.00'),
            'frequency': 'MONTHLY',
            'deposit_start_date': self.past_start,
            'from_account': self.account,
            'currency': '₹',
            'create_historical_entries': True,
        }
        data_true = flow.derive(raw_true)
        steps_true = flow.plan(data_true)
        rt_step = [s for s in steps_true if s.model == RecurringTransaction][0]
        self.assertIsNone(rt_step.fields['last_processed_date'])

        data_false = flow.derive({**raw_true, 'create_historical_entries': False})
        steps_false = flow.plan(data_false)
        rt_step_false = [s for s in steps_false if s.model == RecurringTransaction][0]
        self.assertIsNotNone(rt_step_false.fields['last_processed_date'])

    def test_ppf_epf_nps_flow_historical_toggle(self):
        flow = PpfEpfNpsFlow()
        raw_true = {
            'user': self.user,
            'scheme_type': 'PPF',
            'name': 'PPF True',
            'annual_amount': Decimal('50000.00'),
            'deposit_principal': Decimal('50000.00'),
            'deposit_start_date': self.past_start,
            'deposit_compounding': 'QUARTERLY',
            'show_accrued_balance': True,
            'record_maturity_income': False,
            'from_account': self.account,
            'currency': '₹',
            'create_historical_entries': True,
        }
        data_true = flow.derive(raw_true)
        steps_true = flow.plan(data_true)
        rt_step = [s for s in steps_true if s.model == RecurringTransaction][0]
        self.assertIsNone(rt_step.fields['last_processed_date'])

        data_false = flow.derive({**raw_true, 'create_historical_entries': False})
        steps_false = flow.plan(data_false)
        rt_step_false = [s for s in steps_false if s.model == RecurringTransaction][0]
        self.assertIsNotNone(rt_step_false.fields['last_processed_date'])
