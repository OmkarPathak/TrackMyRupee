from decimal import Decimal
from datetime import date, timedelta
import re
import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.flows.investment import FdFlow, PpfEpfNpsFlow, SipRdFlow
from expenses.flows.credit_card import CreditCardFlow
from expenses.flows.income import SalaryFlow
from expenses.flows.loan import NewLoanFlow
from expenses.flows.registry import FlowRegistry
from expenses.models import Account, CapitalEvent, Income, Loan, LoanInterestRate, PhysicalAsset, RecurringTransaction, SavingsGoal, UserProfile


class TestFlowRegistry(TestCase):
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
                lambda: self.assertTrue(
                    PhysicalAsset.objects.filter(user=self.user, name='Audit Car', asset_class='VEHICLE').exists()
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
                lambda: self.assertTrue(
                    PhysicalAsset.objects.filter(user=self.user, name='Audit Gold', asset_class='GOLD').exists()
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
