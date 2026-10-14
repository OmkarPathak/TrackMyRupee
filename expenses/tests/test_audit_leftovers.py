"""The leads left after the module audits: foreign-currency amounts are read the way the ledger recorded them,
the reconcile command converts and uses the user's day, holding values follow the units held, and the saved
net-worth history changes currency with the user."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from expenses import reconciliation
from expenses.account_valuation import get_current
from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    Expense,
    FXRate,
    Holding,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerReconciliationReport,
    NetWorthSnapshot,
    Valuation,
)
from expenses.views.settings import recalculate_user_transactions

D = Decimal


def today():
    return timezone.localdate()


def ago(days):
    return today() - datetime.timedelta(days=days)


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='lead', password='pw')
        profile = self.user.profile
        profile.tier, profile.currency, profile.has_seen_tutorial = 'PRO', '₹', True
        profile.save()

    def account(self, name='Bank', kind='SAVINGS_ACCOUNT', balance='10000', currency='₹', **kw):
        account = Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance),
                                         currency=currency, **kw)
        LedgerPostingService.post_opening_balance(account=account)
        return account

    def fx(self, rate, frm='USD', to='INR'):
        FXRate.objects.update_or_create(from_currency=frm, to_currency=to, as_of_date=today(),
                                        defaults={'rate': D(str(rate)), 'source': 't'})
        cache.clear()


class ForeignFlowsAreReadAsRecordedTests(Base):
    """A $100 expense charged at 80 took 8,000 rupees; the rate moving to 90 does not change that."""

    def setUp(self):
        super().setUp()
        self.fx(80)
        self.bank = self.account()
        Expense.objects.create(user=self.user, account=self.bank, amount=D('100'), currency='$', category='Food',
                               date=ago(2))
        self.fx(90)

    def test_reconciliation_rebuilds_the_statement_date_balance_at_the_recorded_amount(self):
        self.assertEqual(reconciliation.current_balance(self.bank), D('2000.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, ago(5)), D('10000.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, ago(1)), D('2000.00'))
        self.assertTrue(reconciliation.compare(self.bank, ago(5), D('10000')).matched)

    def test_net_worth_on_a_past_date_takes_it_back_out_at_the_recorded_amount(self):
        self.assertEqual(LedgerReadService.get_net_worth(self.user, as_of=ago(5))[0], D('10000.00'))
        self.assertEqual(LedgerReadService.get_net_worth(self.user)[0], D('2000.00'))

    def test_a_transaction_with_no_ledger_record_falls_back_to_todays_rate(self):
        JournalEntry.objects.filter(source_type='EXPENSE').delete()
        # the ledger now reads 10,000; the dollar expense is converted at today's 90 (9,000) as the fallback
        self.assertEqual(reconciliation.current_balance(self.bank), D('10000.00'))
        self.assertEqual(reconciliation.balance_as_of(self.bank, ago(5)), D('10000.00') + D('9000.00'))

    def test_a_dollar_transfer_into_a_rupee_account_is_read_the_same_way(self):
        usd = self.account('USD', balance='500', currency='$')
        self.fx(80)
        from expenses.models import Transfer
        Transfer.objects.create(user=self.user, from_account=usd, to_account=self.bank, amount=D('10'), date=ago(2))
        self.fx(95)
        self.assertEqual(reconciliation.balance_as_of(self.bank, ago(5)), D('10000.00'))
        self.assertEqual(LedgerReadService.get_net_worth(self.user, as_of=ago(5))[0], D('10000.00') + D('500') * D('95'))


class ReconcileCommandTests(Base):
    def test_a_line_recorded_in_another_currency_is_converted_before_it_is_added_up(self):
        self.fx(80)
        bank = self.account(balance='0')
        legacy = JournalEntry.objects.create(user=self.user, source_type='ADJUSTMENT', source_id=0,
                                             idempotency_key='legacy-1', description='old', status='POSTED')
        ledger = LedgerAccount.objects.get(code=f'USR:{self.user.id}:ASSET:ACCOUNT:{bank.id}')
        JournalLine.objects.create(journal_entry=legacy, ledger_account=ledger, direction='DEBIT', amount=D('10'),
                                   currency='$', fx_rate_to_base=D('80'), base_amount=D('800'), account_ref=bank)
        Account.objects.filter(pk=bank.pk).update(balance=D('800'))
        call_command('reconcile_ledgers', stdout=StringIO())
        report = LedgerReconciliationReport.objects.get(account=bank)
        self.assertEqual((report.ledger_balance, report.status), (D('800.00'), 'MATCH'))

    def test_the_report_is_dated_in_the_users_day_not_utc(self):
        self.account()
        late = datetime.date(2026, 3, 31)
        with patch('django.utils.timezone.localdate', return_value=late):
            call_command('reconcile_ledgers', stdout=StringIO())
        self.assertEqual(LedgerReconciliationReport.objects.get().as_of_date, late)

    def test_the_whole_run_is_a_fixed_number_of_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.account()
        with CaptureQueriesContext(connection) as few:
            call_command('reconcile_ledgers', stdout=StringIO())
        for i in range(8):
            self.account(f'A{i}')
        with CaptureQueriesContext(connection) as many:
            call_command('reconcile_ledgers', stdout=StringIO())
        # one report write per account is expected; reading balances must not add a query per account
        reads = lambda ctx: [q for q in ctx.captured_queries if q['sql'].lstrip().upper().startswith('SELECT')
                             and 'journalline' in q['sql'].lower().replace('_', '')]
        self.assertEqual(len(reads(few)), len(reads(many)))


class HoldingValueFollowsUnitsTests(Base):
    def setUp(self):
        super().setUp()
        self.acc = self.account('Demat', 'DEMAT', '0')
        self.holding = Holding.objects.create(account=self.acc, instrument_name='Fund', instrument_type='MF',
                                              currency='₹', units=D('10'), avg_cost=D('90'), is_active=True)

    def test_a_valuation_made_from_a_unit_price_follows_a_change_in_units(self):
        Valuation.objects.create(holding=self.holding, value=D('1000'), unit_nav=D('100'), as_of_date=today())
        self.assertEqual(LedgerReadService.get_net_worth(self.user)[0], D('1000.00'))
        Holding.objects.filter(pk=self.holding.pk).update(units=D('25'))
        cache.clear()
        self.assertEqual(LedgerReadService.get_net_worth(self.user)[0], D('2500.00'))
        self.assertEqual(get_current(Account.objects.get(pk=self.acc.pk)), D('2500.00'))

    def test_a_total_entered_by_hand_is_used_as_it_is(self):
        Valuation.objects.create(holding=self.holding, value=D('1234'), as_of_date=today())
        Holding.objects.filter(pk=self.holding.pk).update(units=D('25'))
        self.assertEqual(LedgerReadService.get_net_worth(self.user)[0], D('1234.00'))

    def test_a_past_date_uses_the_total_recorded_then(self):
        Valuation.objects.create(holding=self.holding, value=D('1000'), unit_nav=D('100'), as_of_date=ago(30))
        Valuation.objects.create(holding=self.holding, value=D('2000'), unit_nav=D('200'), as_of_date=ago(1))
        Holding.objects.filter(pk=self.holding.pk).update(units=D('50'))
        self.assertEqual(LedgerReadService.get_net_worth(self.user, as_of=ago(10))[0], D('1000.00'))

    def test_value_for_handles_missing_units(self):
        v = Valuation(value=D('500'), unit_nav=D('50'))
        self.assertEqual(v.value_for(None), D('500'))
        self.assertEqual(v.value_for(D('0')), D('500'))
        self.assertEqual(v.value_for(D('3')), D('150.00'))


class SnapshotsFollowTheCurrencyTests(Base):
    def test_the_saved_history_is_converted_with_the_rest(self):
        self.fx(80)
        FXRate.objects.update_or_create(from_currency='INR', to_currency='USD', as_of_date=today(),
                                        defaults={'rate': D('0.0125'), 'source': 't'})
        cache.clear()
        NetWorthSnapshot.objects.create(user=self.user, as_of_date=ago(30), total_net_worth=D('80000'),
                                        total_assets=D('100000'), total_liabilities=D('20000'),
                                        breakdown={'1': '60000.00', '2': '40000.00', 'x': 'n/a'})
        self.user.profile.currency = '$'
        self.user.profile.save()
        with patch('expenses.views.settings.get_exchange_rate', return_value=D('0.0125')):
            self.assertTrue(recalculate_user_transactions(self.user.id, '₹', '$'))
        snap = NetWorthSnapshot.objects.get()
        self.assertEqual((snap.total_net_worth, snap.total_assets, snap.total_liabilities),
                         (D('1000.00'), D('1250.00'), D('250.00')))
        self.assertEqual(snap.breakdown, {'1': '750.00', '2': '500.00', 'x': 'n/a'})

    def test_a_failed_recalculation_leaves_the_history_alone(self):
        NetWorthSnapshot.objects.create(user=self.user, as_of_date=ago(30), total_net_worth=D('80000'),
                                        total_assets=D('100000'), total_liabilities=D('20000'), breakdown={})
        self.user.profile.currency = '$'
        self.user.profile.save()
        with patch('expenses.views.settings.get_exchange_rate', return_value=D('0.0125')), \
                patch('expenses.views.settings._renormalize_base_amounts', side_effect=RuntimeError('boom')):
            self.assertFalse(recalculate_user_transactions(self.user.id, '₹', '$'))
        self.assertEqual(NetWorthSnapshot.objects.get().total_net_worth, D('80000.00'))

    def test_a_user_with_no_history_is_fine(self):
        with patch('expenses.views.settings.get_exchange_rate', return_value=D('0.0125')):
            self.assertTrue(recalculate_user_transactions(self.user.id, '₹', '$'))
