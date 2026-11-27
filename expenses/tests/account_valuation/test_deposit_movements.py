"""What has moved in and out of a deposit changes what it is worth, and finding that out costs queries: the figures are
right in the deposit's own currency, a closed deposit holds nothing, the maturity value is a projection from the terms,
and the movements of many deposits are loaded together instead of one account at a time."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from expenses.account_valuation import (
    DepositMovements,
    get_current_deposit,
    get_maturity_value,
    get_rd_plan,
)
from expenses.models import Account, FXRate, Transfer, UserProfile

D = Decimal


class Base(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('movements', password='x')
        UserProfile.objects.filter(user=self.user).update(currency='₹')
        self.today = timezone.localdate()
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('1000000'), currency='₹')

    def deposit(self, name='FD', kind='FD', principal='100000', rate='7', start=None, maturity=None, closed=None, **extra):
        start = start or self.today - timedelta(days=400)
        return Account.objects.create(
            user=self.user, name=name, account_type=kind, balance=D(principal), currency='₹', deposit_principal=D(principal),
            deposit_rate=D(rate), deposit_compounding='ANNUAL', deposit_start_date=start, deposit_maturity_date=maturity,
            deposit_closed_date=closed, **extra)

    def move(self, source, target, amount, when):
        return Transfer.objects.create(user=self.user, from_account=source, to_account=target, amount=D(amount), date=when)


class TopUpsInTheDepositsCurrencyTests(Base):
    """A transfer's `converted_amount` is in the person's own currency, not the deposit's: a deposit in another currency
    must count what was paid in, in its own."""

    def setUp(self):
        super().setUp()
        UserProfile.objects.filter(user=self.user).update(currency='$')
        self.user = User.objects.get(pk=self.user.pk)               # (a fresh copy: the old one still has its profile cached)
        for (src, dst), rate in {('INR', 'USD'): '0.0125', ('USD', 'INR'): '80'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=self.today, defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()                                               # (rates are cached)
        self.ppf = self.deposit('PPF', 'PPF', principal='100000', rate='7.1', start=self.today - timedelta(days=200))

    def test_a_top_up_counts_at_its_value_in_the_deposits_currency_not_the_persons(self):
        when = self.today - timedelta(days=10)
        transfer = self.move(self.bank, self.ppf, '8000', when)
        self.assertEqual(transfer.converted_amount, D('100.00'))             # (in dollars, the person's currency)
        with_top_up = get_current_deposit(self.ppf, ledger_balance=D('108000'))
        without = get_current_deposit(self.ppf, ledger_balance=D('100000'), adjust=False)         # (the terms alone)
        added = with_top_up - without
        self.assertGreaterEqual(added, D('8000'))              # eight thousand rupees went in, not a hundred
        self.assertLess(added, D('8100'))                      # and ten days' interest on it is small

    def test_a_top_up_in_the_same_currency_is_unchanged(self):
        UserProfile.objects.filter(user=self.user).update(currency='₹')
        self.user = User.objects.get(pk=self.user.pk)
        self.move(self.bank, self.ppf, '8000', self.today - timedelta(days=10))
        added = get_current_deposit(self.ppf, ledger_balance=D('108000')) - get_current_deposit(self.ppf, ledger_balance=D('100000'), adjust=False)
        self.assertGreaterEqual(added, D('8000'))
        self.assertLess(added, D('8100'))


class ClosedDepositTests(Base):
    def setUp(self):
        super().setUp()
        self.start = self.today - timedelta(days=400)
        self.closed = self.today - timedelta(days=35)

    def test_a_closed_deposit_holds_nothing_even_when_the_bank_paid_less_than_it_accrued(self):
        fd = self.deposit(start=self.start, maturity=self.closed, closed=self.closed)
        accrued = get_current_deposit(fd, ledger_balance=D('100000'))
        self.assertGreater(accrued, D('105000'))
        self.move(fd, self.bank, accrued - D('1186'), self.closed)          # (tax deducted at source: ₹1,186 short)
        self.assertEqual(get_current_deposit(fd, ledger_balance=D('0')), D('0.00'))

    def test_a_deposit_past_maturity_but_not_closed_keeps_what_was_not_paid_out(self):
        fd = self.deposit(start=self.start, maturity=self.closed)
        accrued = get_current_deposit(fd, ledger_balance=D('100000'))
        self.move(fd, self.bank, accrued - D('1186'), self.closed)
        self.assertEqual(get_current_deposit(fd, ledger_balance=D('0')), D('1186.00'))

    def test_a_closed_deposit_with_nothing_paid_out_still_shows_what_it_accrued(self):
        fd = self.deposit(start=self.start, maturity=self.closed, closed=self.closed)
        self.assertGreater(get_current_deposit(fd, ledger_balance=D('100000')), D('105000'))

    def test_a_closed_deposit_is_not_zero_before_it_was_closed(self):
        fd = self.deposit(start=self.start, maturity=self.closed, closed=self.closed)
        self.move(fd, self.bank, '106000', self.closed)
        earlier = self.closed - timedelta(days=5)
        self.assertGreater(get_current_deposit(fd, ledger_balance=D('0'), today=earlier), D('0'))


class MaturityValueIsAProjectionTests(Base):
    def test_a_deposit_redeemed_on_its_maturity_day_still_matures_for_what_its_terms_say(self):
        start = self.today - timedelta(days=364)
        fd = self.deposit(start=start, maturity=start + timedelta(days=365), rate='7')
        expected = get_maturity_value(fd, ledger_balance=D('100000'))
        self.move(fd, self.bank, '107000', fd.deposit_maturity_date)
        self.assertEqual(get_maturity_value(fd, ledger_balance=D('0')), expected)
        self.assertGreater(expected, D('106000'))

    def test_money_already_withdrawn_does_not_change_the_projection_but_does_change_todays_value(self):
        start = self.today - timedelta(days=200)
        fd = self.deposit(start=start, maturity=self.today + timedelta(days=165))
        projected = get_maturity_value(fd, ledger_balance=D('100000'))
        today_before = get_current_deposit(fd, ledger_balance=D('100000'))
        self.move(fd, self.bank, '30000', self.today - timedelta(days=5))
        self.assertEqual(get_maturity_value(fd, ledger_balance=D('70000')), projected)
        self.assertEqual(get_current_deposit(fd, ledger_balance=D('70000')), (today_before - D('30000')).quantize(D('0.01')))


class RdFormTests(TestCase):
    def test_an_rd_form_drops_a_principal_it_would_never_use(self):
        from expenses.forms import AccountForm
        user = User.objects.create_user('rd-form', password='x')
        UserProfile.objects.filter(user=user).update(currency='₹')
        user.refresh_from_db()
        form = AccountForm(data={
            'name': 'My RD', 'account_type': 'RD', 'balance': '0', 'currency': '₹', 'deposit_principal': '5000', 'deposit_rate': '7',
            'deposit_start_date': '2026-01-01', 'deposit_maturity_date': '2027-01-01', 'deposit_compounding': 'QUARTERLY',
            'rd_installment_amount': '1000', 'rd_installment_day': '5'}, user=user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data['deposit_principal'])

    def test_an_fd_form_still_requires_its_principal(self):
        from expenses.forms import AccountForm
        user = User.objects.create_user('fd-form', password='x')
        UserProfile.objects.filter(user=user).update(currency='₹')
        user.refresh_from_db()
        form = AccountForm(data={'name': 'My FD', 'account_type': 'FD', 'balance': '0', 'currency': '₹', 'deposit_rate': '7',
                                 'deposit_start_date': '2026-01-01', 'deposit_maturity_date': '2027-01-01'}, user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('deposit_principal', form.errors)

    def test_the_add_form_hides_the_principal_for_an_rd(self):
        user = User.objects.create_user('rd-page', password='x')
        UserProfile.objects.filter(user=user).update(currency='₹', has_seen_tutorial=True, consent_granted=True)
        self.client.force_login(user)
        html = self.client.get('/accounts/add/').content.decode()
        self.assertIn('x-show="accountType !== \'RD\'"', html)


class BatchedMovementsTests(Base):
    def setUp(self):
        super().setUp()
        self.deposits = [self.deposit(f'FD {n}', 'FD', principal='100000') for n in range(4)] + \
                        [self.deposit(f'PPF {n}', 'PPF', principal='50000', rate='7.1') for n in range(4)]
        for n, account in enumerate(self.deposits):
            self.move(account, self.bank, '1000', self.today - timedelta(days=20 + n))
            self.move(self.bank, account, '2000', self.today - timedelta(days=10 + n))

    def value_all(self, movements):
        return [get_current_deposit(a, ledger_balance=a.balance + D('7'), movements=movements) for a in self.deposits]

    def test_registering_deposits_queries_nothing(self):
        with CaptureQueriesContext(connection) as queries:
            DepositMovements.load(self.deposits)
        self.assertEqual(len(queries), 0)

    def test_valuing_many_deposits_takes_a_fixed_number_of_queries_however_many_there_are(self):
        movements = DepositMovements.load(self.deposits)
        with CaptureQueriesContext(connection) as queries:
            self.value_all(movements)
        self.assertLessEqual(len(queries), 3)          # out, spent, and what was paid into the schemes: once for all eight

    def test_a_second_pass_over_the_same_movements_takes_no_queries(self):
        movements = DepositMovements.load(self.deposits)
        self.value_all(movements)
        with CaptureQueriesContext(connection) as queries:
            self.value_all(movements)
        self.assertEqual(len(queries), 0)

    def test_a_ledger_equal_to_the_principal_is_still_looked_into_in_the_same_few_queries(self):
        movements = DepositMovements.load(self.deposits)
        with CaptureQueriesContext(connection) as queries:
            for account in self.deposits:
                get_current_deposit(account, ledger_balance=account.deposit_principal, movements=movements)
        self.assertLessEqual(len(queries), 3)

    def test_one_account_at_a_time_costs_queries_for_each_account_so_batching_is_what_keeps_it_flat(self):
        with CaptureQueriesContext(connection) as one_by_one:
            self.value_all(None)
        self.assertGreater(len(one_by_one), 8)

    def test_loaded_or_not_the_values_are_the_same(self):
        self.assertEqual(self.value_all(DepositMovements.load(self.deposits)), self.value_all(None))

    def test_an_account_not_loaded_is_loaded_when_asked_for(self):
        movements = DepositMovements.load(self.deposits[:2])
        late = self.deposits[5]
        self.assertEqual(movements.withdrawn(late, self.today), D('1000.00'))
        self.assertEqual(len(movements.top_ups(late, late.deposit_start_date, self.today)), 1)

    def test_withdrawals_are_cut_off_at_the_date_asked_for(self):
        movements = DepositMovements.load(self.deposits)
        account = self.deposits[0]
        self.assertEqual(movements.withdrawn(account, self.today - timedelta(days=30)), D('0.00'))
        self.assertEqual(movements.withdrawn(account, self.today), D('1000.00'))

    def test_a_deposit_without_a_row_in_the_database_has_no_movements(self):
        movements = DepositMovements()
        unsaved = Account(user=self.user, name='x', account_type='FD', currency='₹')
        self.assertEqual(movements.withdrawn(unsaved, self.today), D('0.00'))
        self.assertEqual(movements.top_ups(unsaved, date(2020, 1, 1), self.today), [])

    def test_the_maturity_value_reuses_a_plan_it_is_given(self):
        rd = Account.objects.create(
            user=self.user, name='RD', account_type='RD', balance=D('0'), currency='₹', deposit_rate=D('7'), deposit_compounding='SIMPLE',
            deposit_start_date=self.today - timedelta(days=90), deposit_maturity_date=self.today + timedelta(days=275),
            rd_installment_amount=D('1000'), rd_installment_day=5)
        plan = get_rd_plan(rd)
        with patch('expenses.account_valuation.get_rd_plan') as rebuilt:
            self.assertEqual(get_maturity_value(rd, plan=plan), plan['maturity_value'])
        rebuilt.assert_not_called()

    def test_a_missing_plan_is_not_built_again_either(self):
        fd = self.deposit('Plain FD', maturity=self.today + timedelta(days=100))
        with patch('expenses.account_valuation.get_rd_plan') as rebuilt:
            get_maturity_value(fd, ledger_balance=D('100000'), plan=None)
        rebuilt.assert_not_called()


class MovementEdgeTests(Base):
    def test_money_in_matching_money_out_does_not_hide_the_withdrawal(self):
        """The ledger equals the principal (interest credited, part withdrawn), yet ₹5,000 has left the deposit."""
        fd = self.deposit(start=self.today - timedelta(days=100), maturity=self.today + timedelta(days=265))
        untouched = get_current_deposit(fd, ledger_balance=D('100000'))
        self.move(fd, self.bank, '5000', self.today - timedelta(days=3))
        self.assertEqual(get_current_deposit(fd, ledger_balance=D('100000')), (untouched - D('5000')).quantize(D('0.01')))

    def test_an_expense_in_another_currency_is_counted_in_the_accounts_own(self):
        from expenses.models import Expense
        for (src, dst), rate in {('USD', 'INR'): '80', ('INR', 'USD'): '0.0125'}.items():
            FXRate.objects.update_or_create(from_currency=src, to_currency=dst, as_of_date=self.today, defaults={'rate': D(rate), 'source': 'test'})
        cache.clear()
        fd = self.deposit(start=self.today - timedelta(days=100), maturity=self.today + timedelta(days=265))
        untouched = get_current_deposit(fd, ledger_balance=D('100000'))
        Expense.objects.create(user=self.user, account=fd, amount=D('100'), currency='$', category='Fees', description='x', date=self.today - timedelta(days=2))
        self.assertEqual(DepositMovements().withdrawn(fd, self.today), D('8000.00'))          # a hundred dollars, in rupees
        self.assertEqual(get_current_deposit(fd, ledger_balance=D('100000')), (untouched - D('8000')).quantize(D('0.01')))

    def test_an_expense_in_the_accounts_own_currency_is_counted_as_it_is(self):
        from expenses.models import Expense
        fd = self.deposit(start=self.today - timedelta(days=100), maturity=self.today + timedelta(days=265))
        Expense.objects.create(user=self.user, account=fd, amount=D('250'), currency='₹', category='Fees', description='x', date=self.today - timedelta(days=2))
        self.assertEqual(DepositMovements().withdrawn(fd, self.today), D('250.00'))

    def test_money_paid_into_a_scheme_after_it_matured_counts_but_earns_nothing(self):
        start = self.today - timedelta(days=400)
        matured = self.today - timedelta(days=30)
        ppf = self.deposit('PPF', 'PPF', principal='100000', rate='7.1', start=start, maturity=matured)
        before = get_current_deposit(ppf, ledger_balance=D('100000'))
        self.move(self.bank, ppf, '5000', self.today - timedelta(days=10))
        self.assertEqual(get_current_deposit(ppf, ledger_balance=D('105000')), (before + D('5000')).quantize(D('0.01')))

    def test_an_rd_with_nothing_scheduled_or_paid_has_no_plan(self):
        rd = Account.objects.create(
            user=self.user, name='Empty RD', account_type='RD', balance=D('0'), currency='₹', deposit_rate=D('7'), deposit_compounding='SIMPLE',
            deposit_start_date=self.today - timedelta(days=90), deposit_maturity_date=self.today + timedelta(days=275))
        self.assertIsNone(get_rd_plan(rd))

    def test_a_deposit_that_cannot_be_valued_on_its_page_is_logged(self):
        from django.urls import reverse
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)
        self.client.force_login(User.objects.get(pk=self.user.pk))
        asset = Account.objects.create(user=self.user, name='Flat', account_type='REAL_ESTATE', balance=D('0'), currency='₹')
        with patch('expenses.views.accounts.get_current', side_effect=ValueError('no valuation')):
            with self.assertLogs('expenses.views.accounts', level='WARNING') as logged:
                response = self.client.get(reverse('account-detail', kwargs={'pk': asset.uuid}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any('Could not value' in line for line in logged.output))
