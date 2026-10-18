"""Import preview (nothing saved), the choices made in it, import history, undo and the skipped-rows download."""
import csv as csvlib
import io
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from expenses.importing import analyse, ImportOptions
from expenses.importing.undo import UndoBusy, undo_batch
from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, FinancialAuditLog, ImportBatch

D = Decimal


def csv_file(rows, name='statement.csv'):
    out = io.StringIO()
    csvlib.writer(out).writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode('utf-8'), content_type='text/csv')


def predict(description, **kwargs):
    return 'Food'


ROWS = [['Date', 'Description', 'Amount'],
        ['2026-09-01', 'Tea', 50], ['2026-09-02', 'Bus', 30], ['2026-09-03', 'Lunch', 200]]


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='prev', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('10000'), currency='₹')
        LedgerPostingService.post_opening_balance(account=self.bank)

    def data(self, rows=ROWS, **extra):
        data = {'account': self.bank.id, 'currency': '₹', 'file': csv_file(rows)}
        data.update(extra)
        return data

    def preview(self, rows=ROWS, **extra):
        cache.clear()
        return self.client.post(reverse('upload-preview'), self.data(rows, **extra))

    def do_import(self, rows=ROWS, **extra):
        cache.clear()
        response = self.client.post(reverse('upload'), self.data(rows, **extra))
        self.assertEqual(response.status_code, 200)
        return response.context['results']


@override_settings(LEDGER_WRITE_ENABLED=True)
class AnalyseTests(Base):
    def test_analysing_decides_every_row_and_writes_nothing(self):
        Expense.objects.create(user=self.user, date=date(2026, 9, 1), amount=D('50'), description='Tea', category='Food',
                               currency='₹', base_amount=D('50'), exchange_rate=1)
        rows = ROWS + [['2026-09-04', 'Salary', -5000], ['not a date', 'Bad', 10]]
        before = (Expense.objects.count(), ImportBatch.objects.count(), FinancialAuditLog.objects.count())
        analysis = analyse(user=self.user, uploaded_file=csv_file(rows), file_name='s.csv', account=self.bank,
                           currency='₹', predict_category=predict)
        self.assertEqual([d.status for d in analysis.decisions],
                         ['duplicate', 'new', 'new', 'credit', 'error'])
        self.assertEqual(analysis.new_total, D('230'))
        self.assertEqual((Expense.objects.count(), ImportBatch.objects.count(), FinancialAuditLog.objects.count()), before)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('10000'))

    def test_the_file_digest_is_the_same_for_the_same_bytes(self):
        a = analyse(user=self.user, uploaded_file=csv_file(ROWS), file_name='s.csv', account=self.bank, currency='₹', predict_category=predict)
        b = analyse(user=self.user, uploaded_file=csv_file(ROWS), file_name='s.csv', account=self.bank, currency='₹', predict_category=predict)
        c = analyse(user=self.user, uploaded_file=csv_file(ROWS[:3]), file_name='s.csv', account=self.bank, currency='₹', predict_category=predict)
        self.assertEqual(a.file_hash, b.file_hash)
        self.assertNotEqual(a.file_hash, c.file_hash)


class PreviewViewTests(Base):
    def test_it_shows_what_would_happen_and_saves_nothing(self):
        response = self.preview(ROWS + [['2026-09-04', 'Refund', -100]])
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'expenses/partials/_import_preview.html')
        html = response.content.decode()
        for text in ('Tea', 'Bus', 'Lunch', 'id="import-count">3<', 'name="file_hash"', 'name="shown"'):
            self.assertIn(text, html)
        self.assertEqual(response.context['counts'], {'new': 3, 'duplicate': 0, 'credit': 1, 'error': 0, 'limit': 0, 'excluded': 0,
                                                      'possible': 0, 'transfer': 0, 'income': 0})
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(ImportBatch.objects.count(), 0)

    def test_errors_are_explained_row_by_row(self):
        html = self.preview(ROWS + [['someday', 'Bad', 10]]).content.decode()
        self.assertIn('Invalid date format', html)

    def test_a_guess_is_shown_as_a_warning_in_the_preview(self):
        rows = [['Date', 'Narration', 'Amount'], ['2026-09-01', 'a', 10], ['2026-09-02', 'b', 20], ['2026-09-03', 'c', -5]]
        response = self.preview(rows)
        self.assertEqual(len(response.context['notes']), 1)
        self.assertIn('positive amounts', response.context['notes'][0])

    def test_problems_with_the_request_come_back_as_a_message_not_a_crash(self):
        cases = [
            (self.client.post(reverse('upload-preview'), {'account': self.bank.id, 'currency': '₹'}), 'choose a file'),
            (self.client.post(reverse('upload-preview'), {
                'account': self.bank.id, 'currency': '₹', 'file': SimpleUploadedFile('x.txt', b'hi')}), 'Excel (.xlsx) or CSV'),
            (self.client.post(reverse('upload-preview'), {'account': 99999, 'currency': '₹', 'file': csv_file(ROWS)}), 'invalid'),
            (self.client.post(reverse('upload-preview'), {'account': self.bank.id, 'currency': '₹',
                                                            'file': csv_file([['hello', 'world'], ['a', 'b']])}), 'Could not detect'),
        ]
        for response, expected in cases:
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.content.decode())
            self.assertIn('alert-danger', response.content.decode())

    def test_another_users_account_cannot_be_used(self):
        other = User.objects.create_user(username='other', password='pw')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='BANK', balance=0, currency='₹')
        response = self.client.post(reverse('upload-preview'), {'account': theirs.id, 'currency': '₹', 'file': csv_file(ROWS)})
        self.assertIn('invalid', response.content.decode())

    def test_only_post_and_only_when_signed_in(self):
        self.assertEqual(self.client.get(reverse('upload-preview')).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse('upload-preview'), self.data()).status_code, 302)

    def test_a_running_import_blocks_the_preview(self):
        cache.set(f'expense_import_lock_{self.user.id}', 1, 60)
        response = self.client.post(reverse('upload-preview'), self.data())
        self.assertIn('already running', response.content.decode())

    def test_the_plan_limit_is_shown_in_the_preview(self):
        from django.utils import timezone
        today = timezone.localdate().isoformat()
        with patch('finance_tracker.plans.get_limit', return_value=1):
            response = self.preview([['Date', 'Description', 'Amount'], [today, 'a', 10], [today, 'b', 20]])
        self.assertEqual((response.context['counts']['new'], response.context['counts']['limit']), (1, 1))

    def test_the_demo_user_may_preview_but_not_import(self):
        demo = User.objects.create_user(username='demo', password='pw')
        demo.profile.has_seen_tutorial = True
        demo.profile.save()
        account = Account.objects.create(user=demo, name='Demo bank', account_type='BANK', balance=D('100'), currency='₹')
        self.client.force_login(demo)
        cache.clear()
        response = self.client.post(reverse('upload-preview'), {'account': account.id, 'currency': '₹', 'file': csv_file(ROWS)})
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="import-count">3<', response.content.decode())
        cache.clear()
        response = self.client.post(reverse('upload'), {'account': account.id, 'currency': '₹', 'file': csv_file(ROWS)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Expense.objects.filter(user=demo).count(), 0)


class ChoicesMadeInThePreviewTests(Base):
    def test_unticked_rows_are_not_imported(self):
        preview = self.preview()
        shown = preview.context['shown_ids']
        ids = shown.split(',')
        res = self.do_import(shown=shown, include=[ids[0], ids[2]], file_hash=preview.context['analysis'].file_hash)
        self.assertEqual((res['created_count'], res['excluded_count']), (2, 1))
        self.assertEqual(set(Expense.objects.values_list('description', flat=True)), {'Tea', 'Lunch'})
        self.assertEqual(ImportBatch.objects.get().excluded_count, 1)

    def test_with_nothing_unticked_everything_is_imported(self):
        preview = self.preview()
        res = self.do_import(shown=preview.context['shown_ids'], include=preview.context['shown_ids'].split(','))
        self.assertEqual(res['created_count'], 3)

    def test_a_different_file_from_the_one_previewed_is_refused(self):
        preview = self.preview()
        cache.clear()
        response = self.client.post(reverse('upload'), self.data(ROWS[:3], file_hash=preview.context['analysis'].file_hash))
        self.assertIsNone(response.context['results'])
        self.assertTrue(any('not the one you previewed' in str(m) for m in response.context['messages']))
        self.assertEqual(Expense.objects.count(), 0)

    def test_junk_in_the_posted_choices_is_ignored(self):
        res = self.do_import(shown='../../etc,1-2;drop', include=['x', '9999999999-1'], map_date='abc', map_amount='999',
                             sign_mode='sideways', date_order='whenever')
        self.assertIn(res['created_count'], (0, 3))      # an absurd column index may make the file unreadable, never crash

    def test_choosing_a_column_by_hand(self):
        rows = [['Value Date', 'Date', 'Narration', 'Amount'], ['2026-08-30', '2026-09-01', 'Tea', 50]]
        res = self.do_import(rows)
        self.assertEqual(Expense.objects.get().date, date(2026, 9, 1))
        Expense.objects.all().delete()
        ImportBatch.objects.all().delete()
        res = self.do_import(rows, map_date='0')
        self.assertEqual(Expense.objects.get().date, date(2026, 8, 30))

    def test_a_role_can_be_switched_off(self):
        rows = [['Date', 'Narration', 'Amount', 'Closing Balance'], ['2026-09-01', 'Tea', 50, 9950], ['2026-09-02', 'Salary', 5000, 14950],
                ['2026-09-03', 'Bus', 30, 14920]]
        # the balance column would make the salary a credit; switched off, every positive amount is spending
        self.do_import(rows, map_balance='-1')
        self.assertEqual(Expense.objects.count(), 3)

    def test_which_sign_is_spending_can_be_forced(self):
        rows = [['Date', 'Narration', 'Amount'], ['2026-09-01', 'a', 10], ['2026-09-02', 'b', 20], ['2026-09-03', 'c', -5]]
        res = self.do_import(rows, sign_mode='negative')
        self.assertEqual((res['created_count'], res['credit_skipped'], res['notes']), (1, 2, []))
        self.assertEqual(Expense.objects.get().description, 'c')

    def test_how_dates_are_written_can_be_forced(self):
        rows = [['Date', 'Description', 'Amount'], ['03/04/2026', 'A', 10]]
        self.do_import(rows, date_order='month')
        self.assertEqual(Expense.objects.get().date, date(2026, 3, 4))

    def test_the_preview_keeps_the_choices_when_asked_again(self):
        response = self.preview(sign_mode='negative', date_order='month', map_amount='2')
        self.assertEqual((response.context['options'].sign_mode, response.context['options'].date_order), ('negative', 'month'))
        self.assertIn('<option value="negative" selected>', response.content.decode())


@override_settings(LEDGER_WRITE_ENABLED=True)
class BatchRecordTests(Base):
    def test_an_import_is_recorded_and_its_expenses_point_back_to_it(self):
        res = self.do_import(ROWS + [['2026-09-04', 'Refund', -100]])
        batch = ImportBatch.objects.get()
        self.assertEqual(str(batch.uuid), res['batch_uuid'])
        self.assertEqual((batch.created_count, batch.credit_count, batch.total_rows, batch.status),
                         (3, 1, 4, ImportBatch.ACTIVE))
        self.assertEqual((batch.file_name, batch.account_id, batch.total_amount), ('statement.csv', self.bank.id, D('280.00')))
        self.assertEqual(Expense.objects.filter(import_batch=batch).count(), 3)
        self.assertEqual(len(batch.file_hash), 64)

    def test_nothing_imported_leaves_no_record(self):
        self.do_import()
        res = self.do_import()                                  # the same file again: all duplicates
        self.assertEqual((res['created_count'], res['duplicate_count'], res['batch_uuid']), (0, 3, None))
        self.assertEqual(ImportBatch.objects.count(), 1)

    def test_every_skipped_row_is_kept_not_just_the_first_fifteen(self):
        rows = [['Date', 'Description', 'Amount']] + [['bad date', f'Row {i}', 10] for i in range(30)] + [['2026-09-01', 'Good', 5]]
        res = self.do_import(rows)
        self.assertEqual((res['error_count'], len(res['errors']), res['created_count']), (30, 15, 1))
        self.assertEqual(len(ImportBatch.objects.get().report), 30)


@override_settings(LEDGER_WRITE_ENABLED=True)
class UndoTests(Base):
    def setUp(self):
        super().setUp()
        self.manual = Expense.objects.create(user=self.user, date=date(2026, 8, 1), amount=D('75'), description='By hand',
                                             category='Food', currency='₹', account=self.bank)
        self.bank.refresh_from_db()
        self.start_balance = self.bank.balance
        self.do_import()
        self.batch = ImportBatch.objects.get()

    def undo(self, batch=None):
        return self.client.post(reverse('import-undo', args=[(batch or self.batch).uuid]), follow=True)

    def test_undo_removes_the_expenses_and_puts_the_balance_and_ledger_back(self):
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, self.start_balance - D('280'))
        response = self.undo()
        self.assertTrue(any('was undone' in str(m) for m in response.context['messages']))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.UNDONE)
        self.assertIsNotNone(self.batch.undone_at)
        self.assertEqual(Expense.objects.filter(import_batch=self.batch).count(), 0)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, self.start_balance)
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), self.start_balance)

    def test_undo_leaves_everything_else_alone(self):
        self.undo()
        self.assertTrue(Expense.objects.filter(pk=self.manual.pk).exists())
        self.assertEqual(Expense.objects.count(), 1)

    def test_undo_writes_the_audit_trail_of_a_normal_delete(self):
        ids = list(self.batch.expenses.values_list('id', flat=True))
        self.undo()
        self.assertEqual(FinancialAuditLog.objects.filter(model_name='Expense', object_id__in=ids, action='CREATE').count(), 3)

    def test_the_same_file_can_be_imported_again_after_an_undo(self):
        self.undo()
        res = self.do_import()
        self.assertEqual((res['created_count'], res['duplicate_count']), (3, 0))

    def test_undoing_twice_does_nothing_the_second_time(self):
        self.undo()
        response = self.undo()
        self.assertTrue(any('already been undone' in str(m) for m in response.context['messages']))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, self.start_balance)

    def test_a_big_import_is_undone_a_time_budget_at_a_time(self):
        deleted, left = undo_batch(self.batch, budget_seconds=0)
        self.assertEqual((deleted, left), (1, 2))                 # always makes progress, never stalls
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.UNDOING)
        history = self.client.get(reverse('import-history'))
        self.assertIn('undo-continue', history.content.decode())  # the page carries on by itself
        self.assertEqual(undo_batch(self.batch, budget_seconds=0), (1, 1))
        self.assertEqual(undo_batch(self.batch, budget_seconds=0), (1, 0))
        self.batch.refresh_from_db()
        self.assertEqual(self.batch.status, ImportBatch.UNDONE)
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, self.start_balance)

    def test_two_undos_cannot_run_at_once(self):
        cache.set(f'import_undo_lock_{self.batch.pk}', 1, 60)
        with self.assertRaises(UndoBusy):
            undo_batch(self.batch)
        response = self.undo()
        self.assertTrue(any('already being undone' in str(m) for m in response.context['messages']))
        self.assertEqual(Expense.objects.filter(import_batch=self.batch).count(), 3)

    def test_someone_elses_import_cannot_be_undone(self):
        other = User.objects.create_user(username='intruder', password='pw')
        self.client.force_login(other)
        response = self.client.post(reverse('import-undo', args=[self.batch.uuid]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Expense.objects.filter(import_batch=self.batch).count(), 3)

    def test_undo_needs_a_post_and_a_login(self):
        url = reverse('import-undo', args=[self.batch.uuid])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(Expense.objects.filter(import_batch=self.batch).count(), 3)


class HistoryAndReportTests(Base):
    def test_the_history_lists_only_your_imports_with_a_way_to_undo(self):
        self.do_import()
        other = User.objects.create_user(username='someone', password='pw')
        ImportBatch.objects.create(user=other, file_name='secret-file.csv', currency='₹')
        response = self.client.get(reverse('import-history'))
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('statement.csv', html)
        self.assertNotIn('secret-file.csv', html)
        self.assertIn('Undo', html)

    def test_an_empty_history(self):
        self.assertIn('not imported any files', self.client.get(reverse('import-history')).content.decode())

    def test_an_undone_import_has_no_undo_button(self):
        self.do_import()
        batch = ImportBatch.objects.get()
        self.client.post(reverse('import-undo', args=[batch.uuid]))
        html = self.client.get(reverse('import-history')).content.decode()
        self.assertIn('Undone', html)
        self.assertNotIn('data-url="' + reverse('import-undo', args=[batch.uuid]), html)

    def test_history_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('import-history')).status_code, 302)

    def test_the_skipped_rows_download(self):
        rows = ROWS + [['2026-09-04', 'Refund', -100], ['bad', '=HYPERLINK("http://x")', 5], ['2026-09-01', 'Tea', 50]]
        self.do_import(ROWS[:2])                                    # Tea is now a duplicate
        res = self.do_import(rows)
        response = self.client.get(reverse('import-report', args=[res['batch_uuid']]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('attachment', response['Content-Disposition'])
        body = response.content.decode('utf-8-sig')
        table = list(csvlib.reader(io.StringIO(body)))
        self.assertEqual(table[0], ['Row', 'Date', 'Description', 'Amount', 'Status', 'Reason'])
        statuses = {row[2]: row[4] for row in table[1:]}
        self.assertEqual(statuses['Tea'], 'Duplicate')
        self.assertEqual(statuses['Refund'], 'Money in')
        self.assertEqual(statuses["'=HYPERLINK(\"http://x\")"], 'Error')        # a formula is defused

    def test_no_download_when_nothing_was_skipped_or_for_someone_else(self):
        res = self.do_import()
        url = reverse('import-report', args=[res['batch_uuid']])
        self.assertEqual(self.client.get(url).status_code, 404)
        res = self.do_import(ROWS + [['2026-09-04', 'Refund', -100]])
        # (the first three rows are duplicates now, so this import adds nothing and has no record)
        self.assertIsNone(res['batch_uuid'])
        batch = ImportBatch.objects.create(user=self.user, file_name='x.csv', currency='₹', report=[{'row': 1, 'status': 'error', 'reason': 'r'}])
        other = User.objects.create_user(username='thief', password='pw')
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('import-report', args=[batch.uuid])).status_code, 404)

    def test_the_results_page_links_to_the_history_and_the_download(self):
        rows = ROWS + [['2026-09-04', 'Refund', -100]]
        response = self.client.post(reverse('upload'), self.data(rows))
        html = response.content.decode()
        self.assertIn(reverse('import-history'), html)
        self.assertIn(reverse('import-report', args=[response.context['results']['batch_uuid']]), html)
