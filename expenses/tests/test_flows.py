from decimal import Decimal
from datetime import date, timedelta
import re
import uuid
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from expenses.flows.asset import CarFlow, GoldFlow
from expenses.flows.credit_card import CreditCardFlow
from expenses.flows.income import SalaryFlow
from expenses.flows.insurance import InsuranceFlow
from expenses.flows.investment import FdFlow, PpfEpfNpsFlow, SipRdFlow
from expenses.flows.loan import NewLoanFlow
from expenses.flows.recurring import RentBillFlow
from expenses.flows.registry import FlowRegistry
from expenses.flows.savings_goal import SavingsGoalFlow
from expenses.models import (
    Account,
    CapitalEvent,
    FinancialFlow,
    Income,
    Loan,
    LoanInterestRate,
    PhysicalAsset,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from expenses.services_recurring import RecurringService


class TestFlowRegistry(TestCase):
    EXPECTED_KEYS = {
        'loan',
        'creditcard',
        'salary',
        'rentbill',
        'insurance',
        'sip',
        'fd',
        'ppfepfnps',
        'savingsgoal',
        'car',
        'gold',
    }

    def test_registry_exposes_core_flows(self):
        self.assertEqual(FlowRegistry.get('loan').key, 'loan')
        self.assertEqual(FlowRegistry.get('salary').key, 'salary')
        self.assertEqual(FlowRegistry.get('rentbill').key, 'rentbill')

    def test_by_category_has_expected_groups(self):
        grouped = FlowRegistry.by_category()
        self.assertIn('debt', grouped)
        self.assertIn('income', grouped)
        self.assertIn('bills', grouped)
        self.assertIn('savings', grouped)
        self.assertIn('assets', grouped)

    def test_registry_contains_exactly_11_flows(self):
        flows = FlowRegistry.all()
        self.assertEqual(len(flows), 11)
        self.assertEqual(set(flows.keys()), self.EXPECTED_KEYS)

    def test_flow_classes_defined_exactly_once_per_file(self):
        import ast
        from pathlib import Path

        flows_dir = Path(__file__).resolve().parent.parent / 'flows'
        class_definitions = {}
        for py_file in flows_dir.glob('*.py'):
            if py_file.name in ('__init__.py', 'base.py', 'registry.py'):
                continue
            tree = ast.parse(py_file.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    is_flow = any(
                        (isinstance(base, ast.Name) and base.id == 'Flow')
                        or (isinstance(base, ast.Attribute) and base.attr == 'Flow')
                        for base in node.bases
                    )
                    if is_flow:
                        class_definitions.setdefault(node.name, []).append(py_file.name)

        self.assertEqual(len(class_definitions), 11)
        for class_name, files in class_definitions.items():
            self.assertEqual(len(files), 1, f"Flow class {class_name} defined {len(files)} times: {files}")

    def test_register_flow_safeguard_raises_on_duplicate(self):
        from django.core.exceptions import ImproperlyConfigured
        from expenses.flows.base import Flow
        from expenses.flows.registry import register_flow

        class DummyDuplicateFlow(Flow):
            key = 'salary'

        with self.assertRaises(ImproperlyConfigured) as ctx:
            register_flow(DummyDuplicateFlow)
        self.assertIn("Flow with key 'salary' is already registered", str(ctx.exception))


class TestFlowViews(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='flow-view-user', password='pass')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client.force_login(self.user)

    def test_credit_card_flow_detail_renders(self):
        response = self.client.get(reverse('flow-detail', kwargs={'key': 'creditcard'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Card Basics')

    def test_idempotency_key_rotates_after_successful_commit(self):
        account = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH_WALLET',
            balance=Decimal('1000.00'),
            currency='₹',
        )

        detail_response = self.client.get(reverse('flow-detail', kwargs={'key': 'salary'}))
        html = detail_response.content.decode('utf-8')
        first_key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

        commit_response = self.client.post(
            reverse('flow-commit', kwargs={'key': 'salary'}),
            {
                'amount': '30000',
                'currency': '₹',
                'account': str(account.id),
                'salary_date': '1',
                'start_date': '2026-10-01',
                'idempotency_key': first_key,
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(commit_response.status_code, 204)

        detail_response_after = self.client.get(reverse('flow-detail', kwargs={'key': 'salary'}))
        html_after = detail_response_after.content.decode('utf-8')
        second_key = re.search(r'name="idempotency_key" value="([^"]+)"', html_after).group(1)

        self.assertNotEqual(first_key, second_key)

    def test_salary_flow_commit_creates_recurring_schedule(self):
        account = Account.objects.create(
            user=self.user,
            name='Salary Account',
            account_type='SAVINGS',
            balance=Decimal('1000.00'),
            currency='₹',
        )

        detail_response = self.client.get(reverse('flow-detail', kwargs={'key': 'salary'}))
        html = detail_response.content.decode('utf-8')
        idem_key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

        response = self.client.post(
            reverse('flow-commit', kwargs={'key': 'salary'}),
            {
                'amount': '30000',
                'currency': '₹',
                'account': str(account.id),
                'salary_date': '1',
                'start_date': '2026-10-01',
                'idempotency_key': idem_key,
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            RecurringTransaction.objects.filter(
                user=self.user,
                transaction_type='INCOME',
                source='Salary',
                account=account,
                is_active=True,
            ).exists()
        )

    def test_salary_flow_commit_with_historical_option_posts_income_entries(self):
        account = Account.objects.create(
            user=self.user,
            name='Salary Catchup Account',
            account_type='SAVINGS',
            balance=Decimal('1000.00'),
            currency='₹',
        )

        detail_response = self.client.get(reverse('flow-detail', kwargs={'key': 'salary'}))
        html = detail_response.content.decode('utf-8')
        idem_key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

        start_date = (date.today() - timedelta(days=70)).isoformat()
        response = self.client.post(
            reverse('flow-commit', kwargs={'key': 'salary'}),
            {
                'amount': '30000',
                'currency': '₹',
                'account': str(account.id),
                'salary_date': '1',
                'start_date': start_date,
                'create_historical_entries': 'on',
                'idempotency_key': idem_key,
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 204)
        self.assertTrue(
            RecurringTransaction.objects.filter(
                user=self.user,
                transaction_type='INCOME',
                source='Salary',
                account=account,
                is_active=True,
            ).exists()
        )
        self.assertTrue(
            Income.objects.filter(
                user=self.user,
                source='Salary',
                account=account,
                description__contains='Recurring',
            ).exists()
        )

    def test_insurance_flow_commit_creates_policy_and_premium_schedule(self):
        account = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH_WALLET',
            balance=Decimal('100000.00'),
            currency='₹',
        )

        detail_response = self.client.get(reverse('flow-detail', kwargs={'key': 'insurance'}))
        html = detail_response.content.decode('utf-8')
        idem_key = re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

        response = self.client.post(
            reverse('flow-commit', kwargs={'key': 'insurance'}),
            {
                'name': 'Medical Insurance',
                'policy_number': '123',
                'sum_assured': '2500000',
                'premium_amount': '34000',
                'premium_frequency': 'ANNUAL',
                'premium_payment_account': str(account.id),
                'start_date': '2026-10-01',
                'idempotency_key': idem_key,
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 204)

        policy = PhysicalAsset.objects.get(user=self.user, asset_class='INSURANCE', name='Medical Insurance')
        self.assertEqual(policy.policy_number, '123')
        self.assertEqual(policy.premium_amount, Decimal('34000.00'))
        self.assertEqual(policy.premium_frequency, 'ANNUAL')

        insurance_account = Account.objects.get(user=self.user, linked_physical_asset=policy)
        self.assertEqual(insurance_account.name, 'Medical Insurance')
        self.assertEqual(insurance_account.account_type, 'LIFE_INSURANCE')
        self.assertEqual(insurance_account.currency, '₹')
        self.assertEqual(insurance_account.balance, Decimal('0.00'))

        premium_schedule = RecurringTransaction.objects.get(
            user=self.user,
            transaction_type='INSURANCE_PREMIUM',
            physical_asset=policy,
        )
        self.assertEqual(premium_schedule.amount, Decimal('34000.00'))
        self.assertEqual(premium_schedule.frequency, 'YEARLY')
        self.assertEqual(premium_schedule.account, account)
        self.assertTrue(premium_schedule.is_active)


class TestFlowEndpointCoverage(TestCase):
    COVERED_FLOW_KEYS = {
        'car',
        'gold',
        'creditcard',
        'salary',
        'insurance',
        'sip',
        'fd',
        'ppfepfnps',
        'loan',
        'rentbill',
        'savingsgoal',
    }

    def setUp(self):
        self.user = User.objects.create_user(username='flow-audit-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.save(update_fields=['tier'])
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.cash = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH_WALLET',
            balance=Decimal('250000.00'),
            currency='₹',
        )
        self.bank = Account.objects.create(
            user=self.user,
            name='Bank',
            account_type='SAVINGS_ACCOUNT',
            balance=Decimal('250000.00'),
            currency='₹',
        )

    def _flow_idempotency_key(self, key):
        response = self.client.get(reverse('flow-detail', kwargs={'key': key}))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode('utf-8')
        return re.search(r'name="idempotency_key" value="([^"]+)"', html).group(1)

    def _commit_flow(self, key, payload):
        payload = dict(payload)
        payload['idempotency_key'] = self._flow_idempotency_key(key)
        return self.client.post(
            reverse('flow-commit', kwargs={'key': key}),
            payload,
            HTTP_HX_REQUEST='true',
        )

    def test_registry_keys_match_flow_endpoint_coverage(self):
        registry_keys = set(FlowRegistry.all().keys())
        self.assertEqual(registry_keys, self.COVERED_FLOW_KEYS)

    def test_each_registered_flow_commits_successfully(self):
        flow_cases = [
            (
                'car',
                {
                    'name': 'Audit Car',
                    'purchase_price': '850000',
                    'acquisition_date': '2026-10-01',
                    'from_account': str(self.cash.id),
                },
                lambda: (
                    self.assertTrue(
                        PhysicalAsset.objects.filter(user=self.user, name='Audit Car', asset_class='VEHICLE').exists()
                    ),
                    self.assertTrue(
                        Account.objects.filter(user=self.user, name='Audit Car', account_type='VEHICLE').exists()
                    ),
                ),
            ),
            (
                'gold',
                {
                    'route': 'physical',
                    'name': 'Audit Gold',
                    'amount': '120000',
                    'acquisition_date': '2026-10-01',
                },
                lambda: (
                    self.assertTrue(
                        PhysicalAsset.objects.filter(user=self.user, name='Audit Gold', asset_class='GOLD').exists()
                    ),
                    self.assertTrue(
                        Account.objects.filter(user=self.user, name='Audit Gold', account_type='GOLD').exists()
                    ),
                ),
            ),
            (
                'creditcard',
                {
                    'name': 'Audit Credit Card',
                    'balance': '15000',
                    'currency': '₹',
                    'credit_limit': '200000',
                    'billing_day': '5',
                },
                lambda: self.assertTrue(
                    Account.objects.filter(user=self.user, name='Audit Credit Card', account_type='CREDIT_CARD').exists()
                ),
            ),
            (
                'salary',
                {
                    'amount': '50000',
                    'currency': '₹',
                    'account': str(self.bank.id),
                    'salary_date': '1',
                    'start_date': '2026-10-01',
                },
                lambda: self.assertTrue(
                    RecurringTransaction.objects.filter(user=self.user, transaction_type='INCOME', source='Salary', account=self.bank).exists()
                ),
            ),
            (
                'insurance',
                {
                    'name': 'Audit Insurance',
                    'policy_number': 'AUD-123',
                    'sum_assured': '1000000',
                    'premium_amount': '30000',
                    'premium_frequency': 'ANNUAL',
                    'premium_payment_account': str(self.cash.id),
                    'start_date': '2026-10-01',
                },
                lambda: self.assertTrue(
                    Account.objects.filter(user=self.user, name='Audit Insurance', account_type='LIFE_INSURANCE').exists()
                ),
            ),
            (
                'sip',
                {
                    'instrument_type': 'SIP',
                    'name': 'Audit SIP',
                    'amount': '5000',
                    'frequency': 'MONTHLY',
                    'from_account': str(self.cash.id),
                },
                lambda: self.assertTrue(
                    Account.objects.filter(user=self.user, name='Audit SIP', account_type='MUTUAL_FUND').exists()
                ),
            ),
            (
                'fd',
                {
                    'name': 'Audit FD',
                    'principal': '100000',
                    'annual_rate': '7.5',
                    'deposit_start_date': '2026-10-01',
                    'maturity_date': '2027-10-01',
                    'deposit_compounding': 'QUARTERLY',
                    'show_accrued_balance': 'on',
                    'from_account': str(self.cash.id),
                },
                lambda: self.assertTrue(
                    Account.objects.filter(user=self.user, name='Audit FD', account_type='FD').exists()
                ),
            ),
            (
                'ppfepfnps',
                {
                    'scheme_type': 'PPF',
                    'name': 'Audit PPF',
                    'annual_amount': '150000',
                    'deposit_principal': '150000',
                    'deposit_rate': '7.1',
                    'deposit_start_date': '2026-10-01',
                    'deposit_compounding': 'QUARTERLY',
                    'show_accrued_balance': 'on',
                    'from_account': str(self.cash.id),
                },
                lambda: self.assertTrue(
                    Account.objects.filter(user=self.user, name='Audit PPF', account_type='PPF').exists()
                ),
            ),
            (
                'loan',
                {
                    'loan_type': 'PERSONAL',
                    'name': 'Audit Loan',
                    'principal': '100000',
                    'annual_rate': '10.5',
                    'tenure_months': '24',
                    'start_date': '2026-10-01',
                    'create_repayment_schedule': 'on',
                    'payment_account': str(self.cash.id),
                },
                lambda: self.assertTrue(
                    Loan.objects.filter(user=self.user, name='Audit Loan').exists()
                ),
            ),
            (
                'rentbill',
                {
                    'description': 'Audit Rent',
                    'amount': '25000',
                    'currency': '₹',
                    'frequency': 'MONTHLY',
                    'account': str(self.cash.id),
                    'start_date': '2026-10-01',
                },
                lambda: self.assertTrue(
                    RecurringTransaction.objects.filter(user=self.user, transaction_type='EXPENSE', description='Audit Rent').exists()
                ),
            ),
            (
                'savingsgoal',
                {
                    'name': 'Audit Goal',
                    'target_amount': '600000',
                    'target_months': '24',
                    'icon': 'GOAL',
                    'color': 'success',
                },
                lambda: self.assertTrue(
                    SavingsGoal.objects.filter(user=self.user, name='Audit Goal').exists()
                ),
            ),
        ]

        for key, payload, assertion in flow_cases:
            with self.subTest(flow=key):
                response = self._commit_flow(key, payload)
                self.assertEqual(response.status_code, 204)
                assertion()

        self.assertTrue(
            LoanInterestRate.objects.filter(loan__user=self.user, loan__name='Audit Loan').exists()
        )


class TestLoanFlowPlan(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='flow-user', password='pass')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.account = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('100000.00'), currency='₹')

    def test_loan_flow_plan_includes_loan_rate_and_emi(self):
        flow = NewLoanFlow()
        data = flow.derive({
            'user': self.user,
            'name': 'Bike Loan',
            'loan_type': 'PERSONAL',
            'principal': Decimal('50000.00'),
            'annual_rate': Decimal('12.00'),
            'tenure_months': 12,
            'start_date': date(2026, 10, 1),
            'payment_account': self.account,
            'mid_tenure': False,
            'include_down_payment': False,
            'currency': '₹',
        })

        steps = flow.plan(data)
        self.assertEqual([step.key for step in steps], ['loan', 'rate', 'emi'])
        self.assertGreater(flow.preview(self.user, {
            'principal': Decimal('50000.00'),
            'annual_rate': Decimal('12.00'),
            'tenure_months': 12,
        })['headline'], 0)

    def test_loan_flow_supports_configurable_repayment_schedule(self):
        flow = NewLoanFlow()
        data = flow.derive({
            'user': self.user,
            'name': 'Car Loan',
            'loan_type': 'CAR',
            'principal': Decimal('900000.00'),
            'annual_rate': Decimal('9.50'),
            'tenure_months': 60,
            'start_date': date(2026, 10, 1),
            'create_repayment_schedule': True,
            'payment_account': self.account,
            'repayment_amount': Decimal('22000.00'),
            'repayment_frequency': 'WEEKLY',
            'repayment_start_date': date(2026, 10, 7),
            'repayment_is_active': False,
            'mid_tenure': False,
            'include_down_payment': False,
            'currency': '₹',
        })

        steps = flow.plan(data)
        emi_step = next(step for step in steps if step.key == 'emi')
        self.assertEqual(emi_step.fields['amount'], Decimal('22000.00'))
        self.assertEqual(emi_step.fields['frequency'], 'WEEKLY')
        self.assertEqual(emi_step.fields['start_date'], date(2026, 10, 7))
        self.assertFalse(emi_step.fields['is_active'])
        self.assertTrue(emi_step.condition)

    def test_loan_flow_can_skip_repayment_schedule_creation(self):
        flow = NewLoanFlow()
        data = flow.derive({
            'user': self.user,
            'name': 'Personal Loan',
            'loan_type': 'PERSONAL',
            'principal': Decimal('100000.00'),
            'annual_rate': Decimal('11.00'),
            'tenure_months': 24,
            'start_date': date(2026, 10, 1),
            'create_repayment_schedule': False,
            'payment_account': None,
            'mid_tenure': False,
            'include_down_payment': False,
            'currency': '₹',
        })

        steps = flow.plan(data)
        emi_step = next(step for step in steps if step.key == 'emi')
        self.assertFalse(emi_step.condition)

    def test_loan_form_requires_payment_account_when_schedule_enabled(self):
        form = NewLoanFlow().form_class(
            data={
                'loan_type': 'PERSONAL',
                'name': 'Form Loan',
                'principal': '200000',
                'annual_rate': '10.5',
                'tenure_months': '36',
                'start_date': '2026-10-01',
                'create_repayment_schedule': 'on',
            },
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('payment_account', form.errors)


class TestInvestmentFlows(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='invest-user', password='pass')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.cash = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH_WALLET',
            balance=Decimal('250000.00'),
            currency='₹',
        )

    def test_fd_flow_form_includes_deposit_tracking_fields(self):
        form = FdFlow().form_class(user=self.user)
        expected_fields = {
            'name',
            'principal',
            'annual_rate',
            'deposit_start_date',
            'maturity_date',
            'deposit_compounding',
            'show_accrued_balance',
            'record_maturity_income',
            'deposit_closed_date',
            'from_account',
        }

        self.assertTrue(expected_fields.issubset(set(form.fields.keys())))

    def test_fd_commit_creates_account_and_capital_event(self):
        flow = FdFlow()
        result = flow.commit(
            self.user,
            {
                'name': 'Test Fixed Deposit',
                'principal': Decimal('100000.00'),
                'annual_rate': Decimal('12.00'),
                'deposit_start_date': date(2026, 10, 1),
                'maturity_date': date(2026, 10, 2),
                'deposit_compounding': 'QUARTERLY',
                'show_accrued_balance': True,
                'record_maturity_income': True,
                'deposit_closed_date': None,
                'from_account': self.cash,
            },
            idempotency_key=uuid.UUID('11111111-1111-4111-8111-111111111111'),
        )

        self.assertEqual(len(result.created), 2)
        self.assertEqual(Account.objects.filter(user=self.user, account_type='FD').count(), 1)
        self.assertEqual(CapitalEvent.objects.filter(user=self.user, subtype='investment_lump_sum').count(), 1)
        fd_account = Account.objects.get(user=self.user, account_type='FD')
        self.assertEqual(fd_account.deposit_start_date, date(2026, 10, 1))
        self.assertEqual(fd_account.deposit_compounding, 'QUARTERLY')
        self.assertTrue(fd_account.show_accrued_balance)
        self.assertTrue(fd_account.record_maturity_income)

    def test_fd_commit_is_idempotent(self):
        flow = FdFlow()
        idem_key = '22222222-2222-4222-8222-222222222222'
        first = flow.commit(
            self.user,
            {
                'name': 'Idempotent FD',
                'principal': Decimal('50000.00'),
                'annual_rate': Decimal('10.00'),
                'deposit_start_date': date(2026, 10, 1),
                'maturity_date': date(2026, 10, 2),
                'deposit_compounding': 'MONTHLY',
                'show_accrued_balance': True,
                'record_maturity_income': False,
                'deposit_closed_date': None,
                'from_account': self.cash,
            },
            idempotency_key=idem_key,
        )
        second = flow.commit(
            self.user,
            {
                'name': 'Idempotent FD',
                'principal': Decimal('50000.00'),
                'annual_rate': Decimal('10.00'),
                'deposit_start_date': date(2026, 10, 1),
                'maturity_date': date(2026, 10, 2),
                'deposit_compounding': 'MONTHLY',
                'show_accrued_balance': True,
                'record_maturity_income': False,
                'deposit_closed_date': None,
                'from_account': self.cash,
            },
            idempotency_key=idem_key,
        )

        self.assertEqual(first.flow_id, second.flow_id)
        self.assertEqual(Account.objects.filter(user=self.user, account_type='FD', name='Idempotent FD').count(), 1)

    def test_sip_commit_creates_recurring_transfer(self):
        flow = SipRdFlow()
        result = flow.commit(
            self.user,
            {
                'instrument_type': 'SIP',
                'name': 'Equity SIP',
                'amount': Decimal('5000.00'),
                'frequency': 'MONTHLY',
                'from_account': self.cash,
            },
            idempotency_key=uuid.UUID('33333333-3333-4333-8333-333333333333'),
        )

        self.assertEqual(len(result.created), 2)
        self.assertEqual(Account.objects.filter(user=self.user, account_type='MUTUAL_FUND').count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='TRANSFER').count(), 1)

    def test_rd_commit_creates_deposit_account_and_transfer(self):
        flow = SipRdFlow()
        result = flow.commit(
            self.user,
            {
                'instrument_type': 'RD',
                'name': 'Monthly RD',
                'amount': Decimal('5000.00'),
                'frequency': 'MONTHLY',
                'deposit_principal': Decimal('5000.00'),
                'deposit_rate': Decimal('7.50'),
                'deposit_start_date': date(2026, 10, 1),
                'deposit_compounding': 'QUARTERLY',
                'deposit_maturity_date': date(2028, 10, 1),
                'rd_installment_day': 1,
                'show_accrued_balance': True,
                'record_maturity_income': False,
                'from_account': self.cash,
            },
            idempotency_key=uuid.UUID('33333333-3333-4333-8333-333333333334'),
        )

        self.assertEqual(len(result.created), 2)
        rd_account = Account.objects.get(user=self.user, account_type='RD')
        self.assertEqual(rd_account.deposit_principal, Decimal('5000.00'))
        self.assertEqual(rd_account.deposit_rate, Decimal('7.50'))
        self.assertEqual(rd_account.deposit_start_date, date(2026, 10, 1))
        self.assertEqual(rd_account.deposit_compounding, 'QUARTERLY')
        self.assertEqual(rd_account.rd_installment_amount, Decimal('5000.00'))
        self.assertEqual(rd_account.rd_installment_day, 1)

    def test_ppf_flow_commit_carries_deposit_metadata(self):
        flow = PpfEpfNpsFlow()
        result = flow.commit(
            self.user,
            {
                'scheme_type': 'PPF',
                'name': 'Public Provident Fund',
                'annual_amount': Decimal('150000.00'),
                'deposit_principal': Decimal('150000.00'),
                'deposit_rate': Decimal('7.10'),
                'deposit_start_date': date(2026, 10, 1),
                'deposit_compounding': 'QUARTERLY',
                'deposit_maturity_date': date(2036, 10, 1),
                'deposit_closed_date': None,
                'show_accrued_balance': True,
                'record_maturity_income': True,
                'from_account': self.cash,
            },
            idempotency_key=uuid.UUID('44444444-4444-4444-8444-444444444445'),
        )

        self.assertEqual(len(result.created), 2)
        ppf_account = Account.objects.get(user=self.user, account_type='PPF')
        self.assertEqual(ppf_account.deposit_principal, Decimal('150000.00'))
        self.assertEqual(ppf_account.deposit_rate, Decimal('7.10'))
        self.assertEqual(ppf_account.deposit_start_date, date(2026, 10, 1))
        self.assertEqual(ppf_account.deposit_compounding, 'QUARTERLY')
        self.assertTrue(ppf_account.show_accrued_balance)
        self.assertTrue(ppf_account.record_maturity_income)

    def test_ppf_commit_creates_account_and_transfer(self):
        flow = PpfEpfNpsFlow()
        result = flow.commit(
            self.user,
            {
                'scheme_type': 'PPF',
                'name': 'Public Provident Fund',
                'annual_amount': Decimal('150000.00'),
                'deposit_principal': Decimal('150000.00'),
                'deposit_rate': Decimal('7.10'),
                'deposit_start_date': date(2026, 10, 1),
                'deposit_compounding': 'QUARTERLY',
                'deposit_maturity_date': date(2036, 10, 1),
                'deposit_closed_date': None,
                'show_accrued_balance': True,
                'record_maturity_income': True,
                'from_account': self.cash,
            },
            idempotency_key=uuid.UUID('44444444-4444-4444-8444-444444444444'),
        )

        self.assertEqual(len(result.created), 2)
        self.assertEqual(Account.objects.filter(user=self.user, account_type='PPF').count(), 1)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, transaction_type='TRANSFER').count(), 1)


class TestSalaryFlowHistoricalCatchup(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='salary-flow-user', password='pass')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.cash = Account.objects.create(
            user=self.user,
            name='Cash',
            account_type='CASH_WALLET',
            balance=Decimal('1000.00'),
            currency='₹',
        )

    def test_salary_flow_backfills_historical_entries_when_enabled(self):
        flow = SalaryFlow()
        start_date = date.today() - timedelta(days=70)

        flow.commit(
            self.user,
            {
                'amount': Decimal('30000.00'),
                'currency': '₹',
                'account': self.cash,
                'salary_date': 1,
                'start_date': start_date,
                'create_historical_entries': True,
            },
            idempotency_key=uuid.UUID('77777777-7777-4777-8777-777777777771'),
        )

        self.assertTrue(
            Income.objects.filter(user=self.user, source='Salary', description__contains='Recurring').exists()
        )

    def test_salary_flow_does_not_backfill_when_disabled(self):
        flow = SalaryFlow()
        start_date = date.today() - timedelta(days=70)

        flow.commit(
            self.user,
            {
                'amount': Decimal('30000.00'),
                'currency': '₹',
                'account': self.cash,
                'salary_date': 1,
                'start_date': start_date,
                'create_historical_entries': False,
            },
            idempotency_key=uuid.UUID('77777777-7777-4777-8777-777777777772'),
        )

        self.assertFalse(
            Income.objects.filter(user=self.user, source='Salary', description__contains='Recurring').exists()
        )


class TestCreditCardFlow(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='cc-user', password='pass')
        UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})

    def test_credit_card_flow_form_only_exposes_revolving_credit_fields(self):
        form = CreditCardFlow().form_class(user=self.user)
        self.assertIn('currency', form.fields)
        self.assertIn('credit_limit', form.fields)
        self.assertIn('billing_day', form.fields)
        self.assertNotIn('monthly_payment', form.fields)
        self.assertNotIn('annual_rate', form.fields)
        self.assertNotIn('payment_account', form.fields)

    def test_credit_card_commit_creates_revolving_credit_account(self):
        flow = CreditCardFlow()
        result = flow.commit(
            self.user,
            {
                'name': 'Omkar CC',
                'balance': Decimal('15000.00'),
                'currency': '₹',
                'credit_limit': Decimal('120000.00'),
                'billing_day': 2,
                'existing_account': None,
            },
            idempotency_key=uuid.UUID('55555555-5555-4555-8555-555555555555'),
        )

        self.assertEqual(len(result.created), 1)
        card = Account.objects.get(user=self.user, account_type='CREDIT_CARD')
        self.assertEqual(card.balance, Decimal('-15000.00'))
        self.assertEqual(card.credit_limit, Decimal('120000.00'))
        self.assertEqual(card.credit_card_billing_day, 2)

    def test_credit_card_commit_updates_existing_account(self):
        existing = Account.objects.create(
            user=self.user,
            name='Old CC',
            account_type='CREDIT_CARD',
            balance=Decimal('-1000.00'),
            currency='₹',
            credit_limit=Decimal('50000.00'),
            credit_card_billing_day=10,
        )
        flow = CreditCardFlow()
        flow.commit(
            self.user,
            {
                'existing_account': existing,
                'name': 'Updated CC',
                'balance': Decimal('2500.00'),
                'currency': '₹',
                'credit_limit': Decimal('150000.00'),
                'billing_day': 18,
            },
            idempotency_key=uuid.UUID('66666666-6666-4666-8666-666666666666'),
        )

        existing.refresh_from_db()
        self.assertEqual(existing.name, 'Updated CC')
        self.assertEqual(existing.balance, Decimal('-2500.00'))
        self.assertEqual(existing.credit_limit, Decimal('150000.00'))
        self.assertEqual(existing.credit_card_billing_day, 18)


class TestFlowLimitEnforcementAndHardening(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='limit-tester', password='pass')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.profile.tier = 'FREE'
        self.profile.save()

        # Free tier limits: accounts=2, recurring_transactions=2, loans=0, savings_goals=1
        self.acc1 = Account.objects.create(
            user=self.user, name='Wallet', account_type='CASH_WALLET', balance=Decimal('50000.00'), currency='₹'
        )
        self.acc2 = Account.objects.create(
            user=self.user, name='Savings', account_type='SAVINGS_ACCOUNT', balance=Decimal('100000.00'), currency='₹'
        )
        self.rec1 = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', category='Utilities', amount=Decimal('1000.00'),
            account=self.acc1, start_date=date.today()
        )
        self.rec2 = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', category='Broadband', amount=Decimal('800.00'),
            account=self.acc1, start_date=date.today()
        )
        self.goal = SavingsGoal.objects.create(
            user=self.user, name='Emergency Fund', target_amount=Decimal('50000.00')
        )

    def test_all_11_flows_enforce_free_tier_limits_in_preview_and_commit(self):
        flow_cases = [
            (
                'loan',
                NewLoanFlow(),
                {
                    'name': 'Limit Loan',
                    'loan_type': 'PERSONAL',
                    'principal': Decimal('100000.00'),
                    'annual_rate': Decimal('10.00'),
                    'tenure_months': 12,
                    'start_date': date.today(),
                    'payment_account': self.acc1,
                    'create_repayment_schedule': False,
                },
                {
                    'principal': Decimal('100000.00'),
                    'annual_rate': Decimal('10.00'),
                    'tenure_months': 12,
                    'payment_account': self.acc1,
                },
            ),
            (
                'creditcard',
                CreditCardFlow(),
                {
                    'name': 'Limit CC',
                    'balance': Decimal('5000.00'),
                    'currency': '₹',
                    'credit_limit': Decimal('50000.00'),
                    'billing_day': 1,
                    'existing_account': None,
                },
                {
                    'name': 'Limit CC',
                    'balance': Decimal('5000.00'),
                    'credit_limit': Decimal('50000.00'),
                    'billing_day': 1,
                },
            ),
            (
                'salary',
                SalaryFlow(),
                {
                    'amount': Decimal('50000.00'),
                    'currency': '₹',
                    'account': self.acc1,
                    'salary_date': 1,
                    'start_date': date.today(),
                    'create_historical_entries': False,
                },
                {
                    'amount': Decimal('50000.00'),
                    'account': self.acc1,
                    'salary_date': 1,
                },
            ),
            (
                'rentbill',
                RentBillFlow(),
                {
                    'description': 'Limit Rent',
                    'amount': Decimal('15000.00'),
                    'currency': '₹',
                    'frequency': 'MONTHLY',
                    'account': self.acc1,
                    'start_date': date.today(),
                },
                {
                    'description': 'Limit Rent',
                    'amount': Decimal('15000.00'),
                    'account': self.acc1,
                },
            ),
            (
                'insurance',
                InsuranceFlow(),
                {
                    'name': 'Limit Insurance',
                    'policy_number': 'POL-LIMIT',
                    'sum_assured': Decimal('1000000.00'),
                    'premium_amount': Decimal('12000.00'),
                    'premium_frequency': 'ANNUAL',
                    'start_date': date.today(),
                    'premium_payment_account': self.acc1,
                },
                {
                    'name': 'Limit Insurance',
                    'policy_number': 'POL-LIMIT',
                    'sum_assured': Decimal('1000000.00'),
                    'premium_amount': Decimal('12000.00'),
                    'premium_frequency': 'ANNUAL',
                    'start_date': date.today(),
                    'premium_payment_account': self.acc1,
                },
            ),
            (
                'sip',
                SipRdFlow(),
                {
                    'instrument_type': 'SIP',
                    'name': 'Limit SIP',
                    'amount': Decimal('5000.00'),
                    'frequency': 'MONTHLY',
                    'from_account': self.acc1,
                },
                {
                    'instrument_type': 'SIP',
                    'name': 'Limit SIP',
                    'amount': Decimal('5000.00'),
                    'frequency': 'MONTHLY',
                    'from_account': self.acc1,
                },
            ),
            (
                'fd',
                FdFlow(),
                {
                    'name': 'Limit FD',
                    'principal': Decimal('50000.00'),
                    'annual_rate': Decimal('7.00'),
                    'deposit_start_date': date.today(),
                    'maturity_date': date.today() + timedelta(days=365),
                    'deposit_compounding': 'QUARTERLY',
                    'show_accrued_balance': True,
                    'record_maturity_income': False,
                    'deposit_closed_date': None,
                    'from_account': self.acc1,
                },
                {
                    'name': 'Limit FD',
                    'principal': Decimal('50000.00'),
                    'annual_rate': Decimal('7.00'),
                    'deposit_start_date': date.today(),
                    'maturity_date': date.today() + timedelta(days=365),
                    'from_account': self.acc1,
                },
            ),
            (
                'ppfepfnps',
                PpfEpfNpsFlow(),
                {
                    'scheme_type': 'PPF',
                    'name': 'Limit PPF',
                    'annual_amount': Decimal('50000.00'),
                    'deposit_principal': Decimal('50000.00'),
                    'deposit_rate': Decimal('7.10'),
                    'deposit_start_date': date.today(),
                    'deposit_compounding': 'QUARTERLY',
                    'deposit_maturity_date': date.today() + timedelta(days=5475),
                    'deposit_closed_date': None,
                    'show_accrued_balance': True,
                    'record_maturity_income': False,
                    'from_account': self.acc1,
                },
                {
                    'scheme_type': 'PPF',
                    'name': 'Limit PPF',
                    'annual_amount': Decimal('50000.00'),
                    'deposit_principal': Decimal('50000.00'),
                    'deposit_rate': Decimal('7.10'),
                    'deposit_start_date': date.today(),
                    'from_account': self.acc1,
                },
            ),
            (
                'savingsgoal',
                SavingsGoalFlow(),
                {
                    'name': 'Limit Goal',
                    'target_amount': Decimal('100000.00'),
                    'target_months': 12,
                    'icon': 'GOAL',
                    'color': 'success',
                },
                {
                    'name': 'Limit Goal',
                    'target_amount': Decimal('100000.00'),
                    'target_months': 12,
                    'icon': 'GOAL',
                    'color': 'success',
                },
            ),
            (
                'car',
                CarFlow(),
                {
                    'name': 'Limit Car',
                    'purchase_price': Decimal('600000.00'),
                    'financed': True,
                    'loan_name': 'Limit Car Loan',
                    'annual_rate': Decimal('9.00'),
                    'tenure_months': 36,
                    'acquisition_date': date.today(),
                    'from_account': self.acc1,
                },
                {
                    'name': 'Limit Car',
                    'purchase_price': Decimal('600000.00'),
                    'financed': True,
                    'loan_name': 'Limit Car Loan',
                    'annual_rate': Decimal('9.00'),
                    'tenure_months': 36,
                    'acquisition_date': date.today(),
                    'from_account': self.acc1,
                },
            ),
            (
                'gold',
                GoldFlow(),
                {
                    'route': 'digital',
                    'name': 'Limit Gold',
                    'amount': Decimal('20000.00'),
                    'acquisition_date': date.today(),
                    'from_account': self.acc1,
                },
                {
                    'route': 'digital',
                    'name': 'Limit Gold',
                    'amount': Decimal('20000.00'),
                    'acquisition_date': date.today(),
                    'from_account': self.acc1,
                },
            ),
        ]

        self.assertEqual(len(flow_cases), 11)

        for key, flow, commit_payload, preview_payload in flow_cases:
            with self.subTest(flow=key):
                preview_data = flow.preview(self.user, preview_payload)
                self.assertIn('warnings', preview_data, f"Preview for flow '{key}' should contain 'warnings'")
                self.assertTrue(len(preview_data['warnings']) > 0, f"Preview for flow '{key}' should have at least 1 limit warning")

                with self.assertRaises(ValidationError, msg=f"Commit for flow '{key}' should raise ValidationError on limit exceed"):
                    flow.commit(self.user, commit_payload, idempotency_key=uuid.uuid4())

    def test_idempotency_race_handling(self):
        flow = FdFlow()
        idem_key = uuid.uuid4()
        cleaned_data = {
            'name': 'Race FD',
            'principal': Decimal('50000.00'),
            'annual_rate': Decimal('10.00'),
            'deposit_start_date': date(2026, 10, 1),
            'maturity_date': date(2027, 10, 1),
            'deposit_compounding': 'QUARTERLY',
            'show_accrued_balance': True,
            'record_maturity_income': False,
            'deposit_closed_date': None,
            'from_account': self.acc1,
        }
        self.profile.tier = 'PRO'
        self.profile.save()
        self.user.refresh_from_db()

        # Pre-create the FinancialFlow record representing what the concurrent commit created
        existing_flow = FinancialFlow.objects.create(
            user=self.user,
            flow_key=flow.key,
            spec={'name': 'Race FD'},
            idempotency_key=idem_key,
        )

        original_filter = FinancialFlow.objects.filter
        call_count = 0

        def fake_filter(*args, **kwargs):
            nonlocal call_count
            qs = original_filter(*args, **kwargs)
            if kwargs.get('idempotency_key') == idem_key:
                call_count += 1
                if call_count == 1:
                    # First check returns empty queryset, simulating line 208 race window
                    return qs.none()
            return qs

        with patch('expenses.models.FinancialFlow.objects.filter', side_effect=fake_filter):
            result = flow.commit(self.user, cleaned_data, idempotency_key=idem_key)
            self.assertEqual(result.idempotency_key, idem_key)
            self.assertEqual(result.flow_id, existing_flow.id)


    def test_commit_calls_full_clean_before_saving(self):
        flow = CreditCardFlow()
        self.profile.tier = 'PRO'
        self.profile.save()
        self.user.refresh_from_db()

        # Account.credit_card_billing_day has MaxValueValidator(31); 99 fails full_clean()
        with self.assertRaises(ValidationError):
            flow.commit(
                self.user,
                {
                    'name': 'Valid Name',
                    'balance': Decimal('1000.00'),
                    'currency': '₹',
                    'credit_limit': Decimal('50000.00'),
                    'billing_day': 99,
                    'existing_account': None,
                },
                idempotency_key=uuid.uuid4(),
            )

    def test_fd_preview_calculates_interest_via_recurring_service(self):
        flow = FdFlow()
        start = date(2026, 1, 1)
        end = date(2026, 7, 1)
        principal = Decimal('100000.00')
        rate = Decimal('8.00')
        days = (end - start).days
        expected_interest = RecurringService.calculate_interest_for_days(principal, rate, days)

        preview = flow.preview(self.user, {
            'principal': principal,
            'annual_rate': rate,
            'deposit_start_date': start,
            'maturity_date': end,
            'from_account': self.acc1,
        })
        self.assertEqual(Decimal(str(preview['headline'])), round(principal + expected_interest, 2))


class TestFlowLandingPageAndConfiguredStatus(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='landing-user', password='pass')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.cash = Account.objects.create(
            user=self.user, name='Cash', account_type='CASH_WALLET', balance=Decimal('100000.00'), currency='₹'
        )

    def test_is_configured_detects_legacy_and_preexisting_records(self):
        all_flows = FlowRegistry.all()
        # Initial state: only a cash wallet exists, none of the 11 flows should be configured
        for key, flow in all_flows.items():
            self.assertFalse(flow.is_configured(self.user), f"Flow '{key}' should not be configured initially")

        # 1. CreditCardFlow
        Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', is_active=True)
        self.assertTrue(all_flows['creditcard'].is_configured(self.user))

        # 2. SalaryFlow
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', source='Salary', amount=Decimal('50000.00'),
            account=self.cash, start_date=date.today()
        )
        self.assertTrue(all_flows['salary'].is_configured(self.user))

        # 3. NewLoanFlow
        Loan.objects.create(
            user=self.user, name='Home Loan', loan_type='HOME', initial_principal=Decimal('1000000.00'),
            duration_months=120, is_active=True
        )
        self.assertTrue(all_flows['loan'].is_configured(self.user))


        # 4. RentBillFlow
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', category='Rent', amount=Decimal('20000.00'),
            account=self.cash, start_date=date.today()
        )
        self.assertTrue(all_flows['rentbill'].is_configured(self.user))

        # 5. InsuranceFlow
        PhysicalAsset.objects.create(
            user=self.user, name='Term Plan', asset_class='INSURANCE', acquisition_cost=Decimal('10000.00'),
            acquisition_date=date.today(), is_active=True
        )
        self.assertTrue(all_flows['insurance'].is_configured(self.user))

        # 6. SipRdFlow
        Account.objects.create(user=self.user, name='Index Fund', account_type='MUTUAL_FUND', is_active=True)
        self.assertTrue(all_flows['sip'].is_configured(self.user))

        # 7. FdFlow
        Account.objects.create(user=self.user, name='Bank FD', account_type='FD', is_active=True)
        self.assertTrue(all_flows['fd'].is_configured(self.user))

        # 8. PpfEpfNpsFlow
        Account.objects.create(user=self.user, name='PPF Account', account_type='PPF', is_active=True)
        self.assertTrue(all_flows['ppfepfnps'].is_configured(self.user))

        # 9. SavingsGoalFlow
        SavingsGoal.objects.create(user=self.user, name='Vacation', target_amount=Decimal('50000.00'))
        self.assertTrue(all_flows['savingsgoal'].is_configured(self.user))

        # 10. CarFlow
        PhysicalAsset.objects.create(
            user=self.user, name='Sedan', asset_class='VEHICLE', acquisition_cost=Decimal('600000.00'),
            acquisition_date=date.today(), is_active=True
        )
        self.assertTrue(all_flows['car'].is_configured(self.user))

        # 11. GoldFlow
        PhysicalAsset.objects.create(
            user=self.user, name='Gold Coins', asset_class='GOLD', acquisition_cost=Decimal('50000.00'),
            acquisition_date=date.today(), is_active=True
        )
        self.assertTrue(all_flows['gold'].is_configured(self.user))

        # All 11 flows configured now
        for key, flow in all_flows.items():
            self.assertTrue(flow.is_configured(self.user), f"Flow '{key}' should now be configured")

    def test_landing_page_zero_configured(self):
        clean_user = User.objects.create_user(username='clean-user', password='pass')
        UserProfile.objects.get_or_create(user=clean_user, defaults={'currency': '₹'})
        self.client.force_login(clean_user)

        response = self.client.get(reverse('flow-landing'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['configured_count'], 0)
        self.assertEqual(response.context['pct_configured'], 0)
        self.assertEqual(response.context['total_count'], 11)

        # Section 1 rendered, Section 2 omitted
        self.assertContains(response, 'Set up a new flow')
        self.assertContains(response, "Don't see your situation?")
        self.assertContains(response, "What one form creates")
        self.assertNotContains(response, "Already set up")

    def test_landing_page_all_configured(self):
        # Configure all 11 flows for self.user
        Account.objects.create(user=self.user, name='Visa', account_type='CREDIT_CARD', is_active=True)
        RecurringTransaction.objects.create(
            user=self.user, transaction_type='INCOME', source='Salary', amount=Decimal('50000.00'),
            account=self.cash, start_date=date.today()
        )
        Loan.objects.create(
            user=self.user, name='Home Loan', loan_type='HOME', initial_principal=Decimal('1000000.00'),
            duration_months=120, is_active=True
        )

        RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', category='Rent', amount=Decimal('20000.00'),
            account=self.cash, start_date=date.today()
        )
        PhysicalAsset.objects.create(
            user=self.user, name='Term Plan', asset_class='INSURANCE', acquisition_cost=Decimal('10000.00'),
            acquisition_date=date.today(), is_active=True
        )
        Account.objects.create(user=self.user, name='Index Fund', account_type='MUTUAL_FUND', is_active=True)
        Account.objects.create(user=self.user, name='Bank FD', account_type='FD', is_active=True)
        Account.objects.create(user=self.user, name='PPF Account', account_type='PPF', is_active=True)
        SavingsGoal.objects.create(user=self.user, name='Vacation', target_amount=Decimal('50000.00'))
        PhysicalAsset.objects.create(
            user=self.user, name='Sedan', asset_class='VEHICLE', acquisition_cost=Decimal('600000.00'),
            acquisition_date=date.today(), is_active=True
        )
        PhysicalAsset.objects.create(
            user=self.user, name='Gold Coins', asset_class='GOLD', acquisition_cost=Decimal('50000.00'),
            acquisition_date=date.today(), is_active=True
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse('flow-landing'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['configured_count'], 11)
        self.assertEqual(response.context['pct_configured'], 100)

        # Section 2 rendered
        self.assertContains(response, 'Already set up')
        self.assertContains(response, 'Edit existing')
        # Request a flow card is still present in Section 1
        self.assertContains(response, "Don't see your situation?")

    def test_landing_page_categories_and_metadata(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('flow-landing'))
        self.assertEqual(response.status_code, 200)

        cat_keys = [c['key'] for c in response.context['categories']]
        self.assertEqual(cat_keys, ['all', 'debt', 'income', 'bills', 'savings', 'assets'])

        flows = response.context['flows']
        self.assertEqual(len(flows), 11)
        for flow in flows:
            self.assertTrue(flow['title'])
            self.assertTrue(flow['description'])
            self.assertTrue(flow['setup_url'])
            self.assertTrue(flow['edit_url'])
            self.assertTrue(len(flow['creates']) > 0)
            self.assertIn('is_configured', flow)


class TestFlowLimitMessagingAndUI(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='limit-msg-tester', password='pass')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.profile.tier = 'FREE'
        self.profile.save()

    def test_format_limit_warning_zero_limit(self):
        flow = NewLoanFlow()
        warning = flow._format_limit_warning(
            tier='FREE',
            tier_display='Free',
            limit_key='loans',
            limit=0,
            current_count=0,
            steps_creating=1,
        )
        self.assertIn('Free plan does not allow loans', warning)
        self.assertIn('limit: 0', warning)
        self.assertIn('Upgrade to Plus or Pro', warning)

    def test_format_limit_warning_limit_reached(self):
        flow = NewLoanFlow()
        warning = flow._format_limit_warning(
            tier='FREE',
            tier_display='Free',
            limit_key='accounts',
            limit=2,
            current_count=2,
            steps_creating=1,
        )
        self.assertIn('Free plan limit of 2 accounts', warning)
        self.assertIn('currently using 2 of 2', warning)
        self.assertIn('Upgrade to Plus or Pro', warning)
        self.assertIn('remove unused accounts', warning)

    def test_format_limit_warning_plus_tier_recommends_pro(self):
        flow = NewLoanFlow()
        warning = flow._format_limit_warning(
            tier='PLUS',
            tier_display='Plus',
            limit_key='loans',
            limit=1,
            current_count=1,
            steps_creating=1,
        )
        self.assertIn('Plus plan limit of 1 loan', warning)
        self.assertIn('Upgrade to Pro', warning)

    def test_preview_contains_informative_warning(self):
        flow = NewLoanFlow()
        preview = flow.preview(self.user, {
            'principal': Decimal('50000.00'),
            'annual_rate': Decimal('10.00'),
            'tenure_months': 12,
        })
        self.assertTrue(len(preview['warnings']) > 0)
        self.assertIn('Free plan does not allow loans', preview['warnings'][0])
        self.assertIn('Upgrade to Plus or Pro', preview['warnings'][0])

    def test_review_partial_renders_warning_card_and_upgrade_link(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('flow-preview', kwargs={'key': 'loan'}), {
            'name': 'Car Loan',
            'loan_type': 'PERSONAL',
            'principal': '500000',
            'annual_rate': '9.5',
            'tenure_months': '36',
            'start_date': date.today().isoformat(),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tmr-limit-warning')
        self.assertContains(response, 'Plan Limit Reached')
        self.assertContains(response, reverse('pricing'))
        self.assertContains(response, 'Upgrade Plan')
        self.assertContains(response, 'Creation is disabled because plan limits have been reached.')

    def test_detail_page_includes_disabled_binding_on_confirm_button(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('flow-detail', kwargs={'key': 'loan'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, ':disabled="hasWarnings"')
        self.assertContains(response, 'tmrFlowWizard(')




