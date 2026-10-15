"""Audit of the maintenance and backfill commands: the destructive ones refuse to run carelessly and the
repair ones repair without making things worse."""
import json
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    ExpenseKeywordHint,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerReconciliationReport,
    NetWorthSnapshot,
    RecurringTransaction,
)

D = Decimal


def today():
    return timezone.localdate()


def run(name, *args, **kwargs):
    out = StringIO()
    call_command(name, *args, stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue()


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='cmd', password='pw')
        self.other = User.objects.create_user(username='cmd2', password='pw')

    def account(self, user=None, name='Bank', balance='10000', opening=True, kind='SAVINGS_ACCOUNT'):
        account = Account.objects.create(user=user or self.user, name=name, account_type=kind, balance=D(balance), currency='₹')
        if opening:
            LedgerPostingService.post_opening_balance(account=account)
        return account

    def spend(self, account, amount, when=None, **kw):
        return Expense.objects.create(user=account.user, account=account, amount=D(str(amount)), date=when or today(),
                                      category='Food', currency='₹', **kw)

    def ledger_balance(self, account):
        account.refresh_from_db()
        return LedgerReadService.get_account_balance(account)


class OpeningBalanceTests(Base):
    def test_a_new_account_opens_at_its_balance(self):
        acc = self.account(balance='7500')
        self.assertEqual(self.ledger_balance(acc), D('7500.00'))

    def test_a_negative_card_opens_negative_and_a_zero_account_gets_a_marker(self):
        card = self.account(name='Card', balance='-3000', kind='CREDIT_CARD')
        zero = self.account(name='Zero', balance='0')
        self.assertEqual(self.ledger_balance(card), D('-3000.00'))
        self.assertEqual(self.ledger_balance(zero), D('0.00'))

    def test_a_legacy_account_with_postings_but_no_opening_entry_is_not_double_counted(self):
        acc = self.account(balance='10000', opening=False)
        self.spend(acc, 2000)          # a posting made once ledger writing was on, before any opening entry
        self.spend(acc, 500)
        acc.refresh_from_db()
        self.assertEqual(acc.balance, D('7500.00'))
        LedgerPostingService.post_opening_balance(account=acc)
        self.assertEqual(self.ledger_balance(acc), D('7500.00'))
        entry = JournalEntry.objects.get(source_type='ADJUSTMENT', source_id=acc.id)
        self.assertEqual(entry.metadata['opening_balance'], '10000.00')

    def test_the_backfill_command_repairs_such_an_account_and_reconcile_then_matches(self):
        acc = self.account(balance='10000', opening=False)
        self.spend(acc, 2500)
        run('backfill_ledger_opening_balances')
        self.assertEqual(self.ledger_balance(acc), D('7500.00'))
        run('reconcile_ledgers')
        self.assertEqual(LedgerReconciliationReport.objects.get(account=acc).status, 'MATCH')

    def test_the_backfill_is_safe_to_run_twice_and_dry_run_posts_nothing(self):
        acc = self.account(balance='100', opening=False)
        out = run('backfill_ledger_opening_balances', '--dry-run')
        self.assertIn('dry-run', out)
        self.assertEqual(JournalEntry.objects.count(), 0)
        run('backfill_ledger_opening_balances')
        run('backfill_ledger_opening_balances')
        self.assertEqual(JournalEntry.objects.filter(source_type='ADJUSTMENT').count(), 1)

    def test_an_income_posted_before_the_opening_entry_is_also_taken_into_account(self):
        from expenses.models import Income
        acc = self.account(balance='1000', opening=False)
        Income.objects.create(user=self.user, account=acc, amount=D('400'), date=today(), source='Salary', currency='₹')
        acc.refresh_from_db()
        self.assertEqual(acc.balance, D('1400.00'))
        LedgerPostingService.post_opening_balance(account=acc)
        self.assertEqual(self.ledger_balance(acc), D('1400.00'))


class ResetLedgerTests(Base):
    def setUp(self):
        super().setUp()
        self.mine = self.account(balance='5000')
        self.theirs = self.account(self.other, 'Theirs', balance='900')
        self.spend(self.mine, 100)
        self.spend(self.theirs, 50)

    def counts(self):
        return JournalEntry.objects.count(), JournalLine.objects.count(), LedgerAccount.objects.count()

    def test_a_dry_run_changes_nothing(self):
        before = self.counts()
        out = run('reset_ledger')
        self.assertIn('DRY RUN', out)
        self.assertEqual(self.counts(), before)

    def test_committing_without_a_backup_is_refused(self):
        before = self.counts()
        with self.assertRaises(CommandError):
            run('reset_ledger', '--commit', '--yes')
        self.assertEqual(self.counts(), before)

    def test_it_asks_before_wiping_and_a_no_answer_stops_it(self):
        before = self.counts()
        with patch('builtins.input', return_value='no'):
            run('reset_ledger', '--commit', '--no-backup')
        self.assertEqual(self.counts(), before)

    def test_a_backup_is_written_and_can_be_loaded_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'ledger.json')
            run('reset_ledger', '--commit', '--yes', '--backup', path)
            self.assertEqual(self.counts(), (0, 0, 0))
            data = json.load(open(path))
            models = {row['model'] for row in data}
            self.assertTrue({'expenses.journalentry', 'expenses.journalline', 'expenses.ledgeraccount'} <= models)
            call_command('loaddata', path, verbosity=0)
        self.assertGreater(JournalLine.objects.count(), 0)
        self.assertEqual(self.ledger_balance(self.mine), D('4900.00'))

    def test_one_user_can_be_reset_without_touching_the_others(self):
        run('reset_ledger', '--commit', '--yes', '--no-backup', '--user-id', str(self.user.id))
        self.assertFalse(JournalEntry.objects.filter(user=self.user).exists())
        self.assertTrue(JournalEntry.objects.filter(user=self.other).exists())
        self.assertEqual(self.ledger_balance(self.theirs), D('850.00'))

    def test_rebaselining_makes_the_ledger_equal_each_accounts_balance(self):
        run('reset_ledger', '--commit', '--yes', '--no-backup', '--rebaseline')
        self.mine.refresh_from_db()
        self.assertEqual(self.ledger_balance(self.mine), self.mine.balance)
        self.assertEqual(self.ledger_balance(self.theirs), D('850.00'))
        self.assertEqual(JournalEntry.objects.filter(source_type='ADJUSTMENT').count(), 2)

    def test_a_failure_part_way_leaves_the_ledger_as_it_was(self):
        before = self.counts()
        with patch('expenses.management.commands.reset_ledger.LedgerPostingService.post_opening_balance',
                   side_effect=RuntimeError('boom')):
            with self.assertRaises(RuntimeError):
                run('reset_ledger', '--commit', '--yes', '--no-backup', '--rebaseline')
        self.assertEqual(self.counts(), before)


class OrphanedPostingsTests(Base):
    def test_a_normally_deleted_transaction_is_not_an_orphan(self):
        acc = self.account()
        self.spend(acc, 100).delete()
        self.assertIn('No orphaned', run('find_orphaned_ledger_postings'))

    def test_a_transaction_removed_behind_the_ledgers_back_is_reported(self):
        acc = self.account()
        e = self.spend(acc, 100)
        Expense.objects.filter(pk=e.pk)._raw_delete(Expense.objects.db)
        out = run('find_orphaned_ledger_postings')
        self.assertIn(f'EXPENSE:{e.pk}', out)
        self.assertIn('Found 1 orphaned', out)


class CachedBalanceBackfillTests(Base):
    def test_it_fills_the_missing_ones_and_leaves_set_ones_unless_forced(self):
        acc = self.account()
        self.spend(acc, 100)
        ledger = LedgerAccount.objects.get(code=f'USR:{self.user.id}:ASSET:ACCOUNT:{acc.id}')
        LedgerAccount.objects.filter(pk=ledger.pk).update(cached_balance=None)
        run('backfill_ledger_cached_balances')
        ledger.refresh_from_db()
        self.assertEqual(ledger.cached_balance, D('9900.00'))
        LedgerAccount.objects.filter(pk=ledger.pk).update(cached_balance=D('1.00'))
        run('backfill_ledger_cached_balances')
        ledger.refresh_from_db()
        self.assertEqual(ledger.cached_balance, D('1.00'))
        run('backfill_ledger_cached_balances', '--force')
        ledger.refresh_from_db()
        self.assertEqual(ledger.cached_balance, D('9900.00'))


class CleanOrphanCategoriesTests(Base):
    def setUp(self):
        super().setUp()
        User.objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=200))
        Category.objects.filter(user=self.user).delete()
        self.acc = self.account()

    def names(self):
        return set(Category.objects.filter(user=self.user).values_list('name', flat=True))

    def test_only_a_category_nothing_refers_to_is_deleted(self):
        for name in ('Used', 'Budgeted', 'Scheduled', 'Event', 'Learned', 'Unused'):
            Category.objects.create(user=self.user, name=name, limit=D('500') if name == 'Budgeted' else None)
        self.spend(self.acc, 10).__class__.objects.filter(user=self.user).update(category='used')
        RecurringTransaction.objects.create(user=self.user, description='Rent', amount=D('9'), frequency='MONTHLY',
                                            transaction_type='EXPENSE', start_date=today(), next_due_date=today(),
                                            account=self.acc, currency='₹', category='scheduled')
        CapitalEvent.objects.create(user=self.user, account=self.acc, date=today(), amount=D('5'), category='EVENT')
        ExpenseKeywordHint.objects.create(user=self.user, keyword='k', category='Learned')
        run('clean_orphan_categories')
        self.assertEqual(self.names(), {'Used', 'Budgeted', 'Scheduled', 'Event', 'Learned'})

    def test_a_new_users_starter_categories_are_not_touched(self):
        User.objects.filter(pk=self.user.pk).update(date_joined=timezone.now() - timedelta(days=2))
        Category.objects.create(user=self.user, name='Food')
        run('clean_orphan_categories')
        self.assertEqual(self.names(), {'Food'})
        run('clean_orphan_categories', '--min-user-age-days', '0')
        self.assertEqual(self.names(), set())

    def test_a_dry_run_deletes_nothing(self):
        Category.objects.create(user=self.user, name='Unused')
        out = run('clean_orphan_categories', '--dry-run')
        self.assertIn('Dry run', out)
        self.assertEqual(self.names(), {'Unused'})

    def test_one_user_can_be_targeted(self):
        User.objects.filter(pk=self.other.pk).update(date_joined=timezone.now() - timedelta(days=200))
        Category.objects.create(user=self.user, name='Unused')
        Category.objects.create(user=self.other, name='Unused')
        run('clean_orphan_categories', '--user-id', str(self.user.id))
        self.assertEqual(self.names(), set())
        self.assertTrue(Category.objects.filter(user=self.other, name='Unused').exists())


class SamplePopulationTests(TestCase):
    def test_it_refuses_outside_debug(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            run('populate_trackmyrupee')
        self.assertFalse(User.objects.filter(username='trackmyrupee').exists())

    def test_it_refuses_to_wipe_an_existing_users_data(self):
        user = User.objects.create_user(username='trackmyrupee', password='real-password')
        acc = Account.objects.create(user=user, name='Real', balance=D('1'), currency='₹')
        with override_settings(DEBUG=True), self.assertRaises(CommandError):
            run('populate_trackmyrupee')
        self.assertTrue(Account.objects.filter(pk=acc.pk).exists())

    def test_it_creates_the_sample_user_with_a_random_password(self):
        with override_settings(DEBUG=True):
            out = run('populate_trackmyrupee')
        user = User.objects.get(username='trackmyrupee')
        self.assertFalse(user.check_password('password123'))
        self.assertIn('with password', out)
        self.assertTrue(Expense.objects.filter(user=user).exists())

    def test_force_is_the_explicit_way_to_replace_data(self):
        user = User.objects.create_user(username='trackmyrupee', password='x')
        Account.objects.create(user=user, name='Old', balance=D('1'), currency='₹')
        with override_settings(DEBUG=False):
            run('populate_trackmyrupee', '--force')
        self.assertFalse(Account.objects.filter(user=user, name='Old').exists())


class SnapshotTests(Base):
    def test_a_snapshot_is_dated_in_the_users_day_and_rerunning_updates_it(self):
        self.account(balance='1000')
        late = date(2026, 3, 31)
        with patch('django.utils.timezone.localdate', return_value=late):
            run('capture_net_worth_snapshot')
            run('capture_net_worth_snapshot')
        snap = NetWorthSnapshot.objects.get(user=self.user)
        self.assertEqual((snap.as_of_date, snap.total_net_worth), (late, D('1000.00')))
