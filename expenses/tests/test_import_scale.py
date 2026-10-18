"""Phase 5 of the import: it holds up for a long file. The lock is in the database, an import that runs out of time
carries on where it stopped, and the cost of an import no longer grows by queries per row."""
import csv as csvlib
import io
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from expenses.importing import ImportOptions, analyse, commit, run_import
from expenses.importing.locks import LOCK_TTL, ImportBusy, import_lock, is_locked
from expenses.ledger_service import LedgerPostingService
from expenses.models import (Account, Expense, ImportBatch, ImportLock, Income, JournalEntry, Transfer)

D = Decimal


def statement(expenses, incomes=0, transfers=0):
    out = io.StringIO()
    w = csvlib.writer(out)
    w.writerow(['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.'])
    d0 = date(2026, 1, 1)
    for i in range(expenses):
        w.writerow([(d0 + timedelta(days=i % 270)).isoformat(), f'UPI-MERCHANT{i % 300}-{i}@ybl', 100 + i % 900, ''])
    for i in range(incomes):
        w.writerow([(d0 + timedelta(days=i * 7)).isoformat(), f'NEFT-ACME-SALARY {i}', '', 5000 + i])
    for i in range(transfers):
        w.writerow([(d0 + timedelta(days=i * 11)).isoformat(), f'ICICI AMAZON CARD CC PAYMENT {i}', 2000 + i, ''])
    return out.getvalue().encode()


def upload_file(content, name='statement.csv'):
    return SimpleUploadedFile(name, content, content_type='text/csv')


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='scale', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='HDFC Savings', account_type='SAVINGS_ACCOUNT',
                                           balance=D('1000000'), currency='₹')
        self.card = Account.objects.create(user=self.user, name='ICICI Amazon Card', account_type='CREDIT_CARD',
                                           balance=D('0'), currency='₹')
        for account in (self.bank, self.card):
            LedgerPostingService.post_opening_balance(account=account)

    def do_import(self, content, budget=None, resume=None, income=False):
        return run_import(
            user=self.user, uploaded_file=upload_file(content), file_name='statement.csv', account=self.bank,
            currency='₹', predict_category=lambda *a, **k: 'Other', options=ImportOptions(import_income=income),
            reviewed=True, resume=resume, budget_seconds=budget)

    def unfinished(self):
        return ImportBatch.objects.filter(status=ImportBatch.IMPORTING).first()


class LockTests(Base):
    def test_the_lock_is_a_row_in_the_database_and_goes_when_the_block_ends(self):
        self.assertFalse(is_locked(self.user))
        with import_lock(self.user):
            self.assertTrue(is_locked(self.user))
            self.assertEqual(ImportLock.objects.count(), 1)
        self.assertFalse(ImportLock.objects.exists())

    def test_a_second_import_for_the_same_person_is_refused(self):
        with import_lock(self.user):
            with self.assertRaises(ImportBusy):
                with import_lock(self.user):
                    pass
            self.assertTrue(is_locked(self.user))          # the refused attempt did not release the first
        self.assertFalse(is_locked(self.user))

    def test_it_holds_when_the_worker_memory_is_gone(self):
        # The old lock was a key in the process cache, which a restart or a second worker lost.
        with import_lock(self.user):
            cache.clear()
            self.assertTrue(is_locked(self.user))

    def test_different_people_do_not_block_each_other(self):
        other = User.objects.create_user(username='other', password='pw')
        with import_lock(self.user):
            with import_lock(other):
                self.assertTrue(is_locked(other))

    def test_a_lock_that_was_never_released_expires(self):
        lock = ImportLock.objects.create(user=self.user)
        ImportLock.objects.filter(pk=lock.pk).update(acquired_at=timezone.now() - LOCK_TTL - timedelta(seconds=1))
        self.assertFalse(is_locked(self.user))
        with import_lock(self.user):                        # a crashed request cannot lock someone out for good
            self.assertTrue(is_locked(self.user))

    def test_it_is_released_when_the_import_fails(self):
        with self.assertRaises(RuntimeError):
            with import_lock(self.user):
                raise RuntimeError('boom')
        self.assertFalse(ImportLock.objects.exists())

    def test_the_page_refuses_while_another_import_holds_it(self):
        ImportLock.objects.create(user=self.user)
        response = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹',
                                                         'file': upload_file(statement(3))})
        self.assertTrue(any('already running' in str(m) for m in response.context['messages']))
        self.assertEqual(Expense.objects.count(), 0)


class CarryingOnTests(Base):
    def test_an_import_that_runs_out_of_time_leaves_a_not_finished_batch_and_says_what_is_left(self):
        res = self.do_import(statement(450), budget=0)
        self.assertEqual((res['created_count'], res['remaining']), (200, 250))          # one 200-row unit per attempt
        batch = self.unfinished()
        self.assertEqual((batch.status, batch.created_count, batch.total_rows), (ImportBatch.IMPORTING, 200, 450))
        self.assertEqual(Expense.objects.count(), 200)

    def test_importing_the_same_file_again_carries_on_and_writes_nothing_twice(self):
        content = statement(450)
        self.do_import(content, budget=0)
        batch = self.unfinished()
        second = self.do_import(content, budget=0, resume=batch)
        self.assertEqual((second['created_count'], second['remaining']), (400, 50))
        third = self.do_import(content, budget=0, resume=self.unfinished())
        self.assertEqual((third['created_count'], third['remaining']), (450, 0))
        self.assertEqual(Expense.objects.count(), 450)
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.created_count, ImportBatch.objects.count()), (ImportBatch.ACTIVE, 450, 1))
        self.assertEqual(third['duplicate_count'], 0)                                    # its own rows are not duplicates
        self.assertEqual(Expense.objects.filter(import_batch=batch).count(), 450)

    def test_a_finished_import_of_the_file_is_not_resumed_it_is_all_duplicates(self):
        content = statement(30)
        self.do_import(content)
        res = self.do_import(content)
        self.assertEqual((res['created_count'], res['duplicate_count'], res['batch_uuid']), (0, 30, None))

    def test_income_and_transfers_are_carried_on_too(self):
        content = statement(250, incomes=3, transfers=2)
        res = self.do_import(content, budget=0, income=True)
        steps = 1
        while res['remaining']:
            res = self.do_import(content, budget=0, resume=self.unfinished(), income=True)
            steps += 1
            self.assertLess(steps, 10)
        self.assertEqual((Expense.objects.count(), Income.objects.count(), Transfer.objects.count()), (250, 3, 2))
        self.assertEqual((res['created_count'], res['income_count'], res['transfer_count']), (250, 3, 2))
        batch = ImportBatch.objects.get()
        self.assertEqual((batch.created_count, batch.income_count, batch.transfer_count, batch.status),
                         (250, 3, 2, ImportBatch.ACTIVE))

    def test_balances_and_the_ledger_are_right_after_several_attempts(self):
        content = statement(450)
        with override_settings(LEDGER_WRITE_ENABLED=True):
            res = self.do_import(content, budget=0)
            while res['remaining']:
                res = self.do_import(content, budget=0, resume=self.unfinished())
        total = sum(Expense.objects.values_list('amount', flat=True))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('1000000') - total)
        self.assertEqual(JournalEntry.objects.filter(source_type='EXPENSE').count(), 450)

    def test_a_different_file_or_account_does_not_resume_it(self):
        self.do_import(statement(450), budget=0)
        batch = self.unfinished()
        other_file = ('Date,Narration,Withdrawal Amt.,Deposit Amt.\n'
                      + ''.join(f'2026-03-{1 + i % 28:02d},CORNER SHOP {i},{7 + i},\n' for i in range(40))).encode()
        response = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹',
                                                         'file': upload_file(other_file)})
        self.assertEqual(response.context['results']['created_count'], 40)
        self.assertEqual(ImportBatch.objects.count(), 2)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.IMPORTING)

    def test_the_page_resumes_by_itself_when_the_same_file_is_chosen_again(self):
        content = statement(450)
        with patch('expenses.views.misc.IMPORT_BUDGET_SECONDS', 0):
            first = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹', 'file': upload_file(content)})
            self.assertTrue(any('cut short' in str(m) and '250 rows' in str(m) for m in first.context['messages']))
            second = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹', 'file': upload_file(content)})
            self.assertEqual(second.context['results']['remaining'], 50)
            third = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹', 'file': upload_file(content)})
        self.assertEqual((third.context['results']['remaining'], third.context['results']['created_count']), (0, 450))
        self.assertTrue(any('Processing complete' in str(m) for m in third.context['messages']))
        self.assertEqual(ImportBatch.objects.get().status, ImportBatch.ACTIVE)

    def test_the_history_marks_an_unfinished_import(self):
        self.do_import(statement(450), budget=0)
        html = self.client.get(reverse('import-history')).content.decode()
        self.assertIn('Not finished', html)
        self.assertIn('Import the same file again', html)

    def test_the_preview_of_an_unfinished_import_says_so_and_shows_what_is_in(self):
        content = statement(450)
        self.do_import(content, budget=0)
        response = self.client.post(reverse('upload-preview'), {'account': self.bank.id, 'currency': '₹',
                                                                 'file': upload_file(content)})
        self.assertEqual(response.context['done_count'], 200)
        self.assertIn('did not finish', response.content.decode())
        self.assertEqual(response.context['counts']['new'], 250)

    def test_undoing_an_unfinished_import_removes_what_it_wrote_and_a_new_attempt_starts_fresh(self):
        content = statement(450)
        self.do_import(content, budget=0)
        batch = self.unfinished()
        self.client.post(reverse('import-undo', args=[batch.uuid]))
        self.assertEqual(Expense.objects.count(), 0)
        batch.refresh_from_db()
        self.assertEqual(batch.status, ImportBatch.UNDONE)
        res = self.do_import(content)
        self.assertEqual((res['created_count'], res['remaining']), (450, 0))
        self.assertEqual(ImportBatch.objects.exclude(status=ImportBatch.UNDONE).count(), 1)

    def test_analysing_an_unfinished_import_gives_the_same_decisions_as_the_first_time(self):
        content = statement(450)
        first = analyse(user=self.user, uploaded_file=upload_file(content), file_name='s.csv', account=self.bank,
                        currency='₹', predict_category=lambda *a, **k: 'Other')
        self.do_import(content, budget=0)
        again = analyse(user=self.user, uploaded_file=upload_file(content), file_name='s.csv', account=self.bank,
                        currency='₹', predict_category=lambda *a, **k: 'Other', resume=self.unfinished())
        self.assertEqual([d.row.number for d in first.decisions], [d.row.number for d in again.decisions])
        self.assertEqual({d.status for d in first.decisions}, {'new'})
        self.assertEqual(sorted({d.status for d in again.decisions}), ['done', 'new'])
        self.assertEqual(again.count('done'), 200)


@override_settings(LEDGER_WRITE_ENABLED=True)
class CostTests(Base):
    """Guards on how much work a long import does, so nobody quietly puts a query back inside a loop."""

    def test_a_thousand_rows_cost_a_few_dozen_queries_not_nine_a_row(self):
        content = statement(1000)
        analysis = analyse(user=self.user, uploaded_file=upload_file(content), file_name='s.csv', account=self.bank,
                           currency='₹', predict_category=lambda *a, **k: 'Other')
        with CaptureQueriesContext(connection) as ctx:
            summary = commit(analysis, user=self.user, account=self.bank, currency='₹', file_name='s.csv')
        self.assertEqual(summary['created_count'], 1000)
        self.assertLess(len(ctx), 150, f'{len(ctx)} queries for 1000 rows (it was about 8,900)')

    def test_five_thousand_rows_import_in_one_go_with_the_ledger_on(self):
        content = statement(4400, incomes=500, transfers=0)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹', 'import_income': '1',
                                                             'file': upload_file(content)})
        res = response.context['results']
        self.assertEqual((res['created_count'], res['income_count'], res['remaining']), (4400, 500, 0))
        self.assertEqual(JournalEntry.objects.count(), 4900 + 2)           # plus the two opening balances
        self.assertLess(len(ctx), 800, f'{len(ctx)} queries for a 4,900-row import')
        self.bank.refresh_from_db()
        from expenses.ledger_read_service import LedgerReadService
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), self.bank.balance)
