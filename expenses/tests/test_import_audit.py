"""Audit of the Excel/CSV expense import: it refuses what it cannot safely read, imports what a real statement
holds (and only the spending), keeps balances and the ledger right, and never lets one bad row spoil the rest."""
import csv
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.ledger_read_service import LedgerReadService
from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense, FXRate, JournalEntry, UserProfile

D = Decimal


def today():
    return timezone.localdate()


def csv_file(rows, name='statement.csv', encoding='utf-8'):
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode(encoding), content_type='text/csv')


def xlsx_file(rows, name='statement.xlsx'):
    wb = openpyxl.Workbook()
    for row in rows:
        wb.active.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='imp', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('10000'), currency='₹')
        LedgerPostingService.post_opening_balance(account=self.bank)

    def upload(self, file, **extra):
        data = {'account': self.bank.id, 'currency': '₹', 'file': file}
        data.update(extra)
        return self.client.post(reverse('upload'), data)

    def results(self, file, **extra):
        response = self.upload(file, **extra)
        self.assertEqual(response.status_code, 200)
        return response.context['results']

    def messages(self, response):
        return [str(m) for m in response.context['messages']]


class RefusalTests(Base):
    def test_a_file_of_the_wrong_kind_is_refused_with_a_reason(self):
        r = self.upload(SimpleUploadedFile('notes.pdf', b'%PDF-1.4 hello'))
        self.assertIsNone(r.context['results'])
        self.assertTrue(any('.xlsx' in m or 'CSV' in m for m in self.messages(r)))
        self.assertEqual(Expense.objects.count(), 0)

    def test_the_extension_is_not_case_sensitive(self):
        res = self.results(csv_file([['Date', 'Amount', 'Description'], [today().isoformat(), 50, 'Tea']], name='STATEMENT.CSV'))
        self.assertEqual(res['created_count'], 1)

    def test_a_file_over_five_megabytes_is_refused(self):
        big = SimpleUploadedFile('big.csv', b'Date,Amount,Description\n' + b'x' * (5 * 1024 * 1024 + 10))
        r = self.upload(big)
        self.assertIsNone(r.context['results'])
        self.assertTrue(any('too large' in m for m in self.messages(r)))

    def test_a_spreadsheet_that_unpacks_to_a_huge_size_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('[Content_Types].xml', '<Types/>')
            z.writestr('xl/padding.bin', b'\0' * (40 * 1024 * 1024))
        self.assertLess(len(buf.getvalue()), 5 * 1024 * 1024)
        r = self.upload(SimpleUploadedFile('bomb.xlsx', buf.getvalue()))
        self.assertIsNone(r.context['results'])
        self.assertTrue(any('too large' in m for m in self.messages(r)))

    def test_more_rows_than_the_limit_is_refused_whole_not_half_imported(self):
        rows = [['Date', 'Amount', 'Description']] + [[today().isoformat(), 1 + i, f'item {i}'] for i in range(60)]
        with patch('expenses.views.misc.MAX_IMPORT_ROWS', 50):
            r = self.upload(csv_file(rows))
        self.assertIsNone(r.context['results'])
        self.assertTrue(any('5,000' in m for m in self.messages(r)))
        self.assertEqual(Expense.objects.count(), 0)

    def test_exactly_the_limit_is_accepted(self):
        rows = [['Date', 'Amount', 'Description']] + [[today().isoformat(), 1 + i, f'item {i}'] for i in range(50)]
        with patch('expenses.views.misc.MAX_IMPORT_ROWS', 50):
            self.assertEqual(self.results(csv_file(rows))['created_count'], 50)

    def test_the_real_limit_is_five_thousand_rows_and_five_megabytes(self):
        from expenses.views import misc
        self.assertEqual((misc.MAX_IMPORT_ROWS, misc.MAX_IMPORT_BYTES), (5000, 5 * 1024 * 1024))

    def test_a_corrupt_spreadsheet_gets_a_plain_message_without_internals(self):
        r = self.upload(SimpleUploadedFile('broken.xlsx', b'PK\x03\x04 this is not a workbook'))
        self.assertTrue(r.status_code == 200)
        text = ' '.join(self.messages(r))
        self.assertNotIn('Traceback', text)
        self.assertNotIn('openpyxl', text.lower())

    def test_another_users_account_and_a_closed_one_cannot_be_used(self):
        stranger = User.objects.create_user(username='x', password='pw')
        theirs = Account.objects.create(user=stranger, name='Theirs', balance=D('5'), currency='₹')
        file = csv_file([['Date', 'Amount', 'Description'], [today().isoformat(), 50, 'Tea']])
        r = self.client.post(reverse('upload'), {'account': theirs.id, 'currency': '₹', 'file': file})
        self.assertIsNone(r.context['results'])
        Account.objects.filter(pk=self.bank.pk).update(is_active=False)
        file.seek(0)
        r = self.upload(file)
        self.assertEqual(Expense.objects.count(), 0)

    def test_it_needs_sign_in(self):
        self.client.logout()
        r = self.client.post(reverse('upload'), {'file': csv_file([['a']])})
        self.assertEqual(r.status_code, 302)


class OneImportAtATimeTests(Base):
    def test_a_second_import_while_one_runs_is_refused(self):
        cache.set(f'expense_import_lock_{self.user.id}', 1, 300)
        r = self.upload(csv_file([['Date', 'Amount', 'Description'], [today().isoformat(), 50, 'Tea']]))
        self.assertTrue(any('already running' in m for m in self.messages(r)))
        self.assertEqual(Expense.objects.count(), 0)

    def test_the_lock_is_released_after_a_normal_import_and_after_a_failure(self):
        key = f'expense_import_lock_{self.user.id}'
        self.results(csv_file([['Date', 'Amount', 'Description'], [today().isoformat(), 50, 'Tea']]))
        self.assertIsNone(cache.get(key))
        self.upload(SimpleUploadedFile('broken.xlsx', b'PK\x03\x04 nope'))
        self.assertIsNone(cache.get(key))
        rows = [['Date', 'Amount', 'Description']] + [[today().isoformat(), 1, f'i{i}'] for i in range(60)]
        with patch('expenses.views.misc.MAX_IMPORT_ROWS', 50):
            self.upload(csv_file(rows))
        self.assertIsNone(cache.get(key))


class WhatARealStatementHoldsTests(Base):
    def test_a_bank_statement_with_separate_withdrawal_and_deposit_columns(self):
        rows = [
            ['Date', 'Narration', 'Chq/Ref No', 'Value Dt', 'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'],
            [today().isoformat(), 'UPI-SWIGGY', '123456789012', today().isoformat(), 450, '', 9550],
            [today().isoformat(), 'SALARY CREDIT', '987654321098', today().isoformat(), '', 50000, 59550],
            [today().isoformat(), 'UPI-UBER', '555555555555', today().isoformat(), 230, '', 59320],
        ]
        res = self.results(csv_file(rows))
        self.assertEqual(res['created_count'], 2)
        self.assertEqual(sorted(Expense.objects.values_list('description', flat=True)), ['UPI-SWIGGY', 'UPI-UBER'])
        self.assertEqual(Expense.objects.get(description='UPI-SWIGGY').amount, D('450.00'))

    def test_a_single_signed_column_imports_the_debits_and_leaves_out_the_credits(self):
        rows = [['Date', 'Description', 'Amount'],
                [today().isoformat(), 'Groceries', -1200], [today().isoformat(), 'Fuel', -800],
                [today().isoformat(), 'Rent', -9000], [today().isoformat(), 'Salary', 60000]]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (3, 1))
        self.assertFalse(Expense.objects.filter(description='Salary').exists())
        self.assertEqual(Expense.objects.get(description='Rent').amount, D('9000.00'))

    def test_my_own_sheet_of_positive_spending_leaves_out_a_negative_refund(self):
        rows = [['Date', 'Description', 'Amount'],
                [today().isoformat(), 'Groceries', 1200], [today().isoformat(), 'Fuel', 800],
                [today().isoformat(), 'Jacket', 2500], [today().isoformat(), 'Jacket refund', -2500]]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (3, 1))

    def test_all_one_sign_is_all_spending(self):
        for sign in (1, -1):
            Expense.objects.all().delete()
            rows = [['Date', 'Description', 'Amount'], [today().isoformat(), f'a{sign}', sign * 10],
                    [today().isoformat(), f'b{sign}', sign * 20]]
            self.assertEqual(self.results(csv_file(rows))['created_count'], 2)

    def test_an_even_split_of_signs_is_not_guessed_everything_is_kept(self):
        rows = [['Date', 'Description', 'Amount'], [today().isoformat(), 'a', 10], [today().isoformat(), 'b', -10]]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (2, 0))

    def test_accounting_brackets_count_as_negative(self):
        rows = [['Date', 'Description', 'Amount'], [today().isoformat(), 'a', '(100.00)'],
                [today().isoformat(), 'b', '(50.00)'], [today().isoformat(), 'pay', '5,000.00']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (2, 1))

    def test_a_transaction_type_column_is_not_taken_for_a_category(self):
        rows = [['Date', 'Description', 'Amount', 'Transaction Type'],
                [today().isoformat(), 'Swiggy dinner', 450, 'UPI'], [today().isoformat(), 'Uber', 230, 'DEBIT']]
        self.results(csv_file(rows))
        self.assertFalse(Expense.objects.filter(category__in=['UPI', 'DEBIT']).exists())

    def test_a_real_category_column_is_used(self):
        rows = [['Date', 'Description', 'Amount', 'Category'], [today().isoformat(), 'Anything', 100, 'Holiday']]
        self.results(csv_file(rows))
        self.assertEqual(Expense.objects.get().category, 'Holiday')

    def test_an_unknown_description_is_filed_under_other_not_a_made_up_category(self):
        self.results(csv_file([['Date', 'Description', 'Amount'], [today().isoformat(), 'zxqv', 100]]))
        self.assertEqual(Expense.objects.get().category, 'Other')

    def test_an_excel_file_reads_the_same_way(self):
        res = self.results(xlsx_file([['Date', 'Description', 'Amount'], [today(), 'Coffee', 120]]))
        self.assertEqual(res['created_count'], 1)
        self.assertEqual(Expense.objects.get().date, today())

    def test_a_row_without_a_description_or_with_a_bad_date_is_reported_and_the_rest_imported(self):
        rows = [['Date', 'Description', 'Amount'], ['not a date', 'x', 10], [today().isoformat(), '', 20],
                [today().isoformat(), 'good', 30]]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['error_count']), (1, 2))
        self.assertEqual(len(res['errors']), 2)


class OneBadRowDoesNotSpoilTheRestTests(Base):
    def test_an_amount_too_big_for_the_database_is_that_rows_error_only(self):
        rows = [['Date', 'Description', 'Amount']] + [[today().isoformat(), f'ok{i}', 10 + i] for i in range(5)]
        rows.insert(3, [today().isoformat(), 'absurd', '99999999999999999999'])
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['error_count']), (5, 1))
        self.assertEqual(res['errors'][0]['reason'][:20], 'Amount is too large:')

    def test_a_very_long_category_is_cut_not_a_batch_failure(self):
        rows = [['Date', 'Description', 'Amount', 'Category'], [today().isoformat(), 'long', 10, 'C' * 400],
                [today().isoformat(), 'fine', 20, 'Food']]
        res = self.results(csv_file(rows))
        self.assertEqual(res['created_count'], 2)
        self.assertEqual(len(Expense.objects.get(description='long').category), 255)


class DatesTests(Base):
    def test_a_date_without_a_year_that_would_be_in_the_future_means_last_year(self):
        future = today() + timedelta(days=60)
        res = self.results(csv_file([['Date', 'Description', 'Amount'], [future.strftime('%d %b'), 'Late bill', 10]]))
        self.assertEqual(res['created_count'], 1)
        self.assertEqual(Expense.objects.get().date.year, today().year - 1)

    def test_a_recent_date_without_a_year_is_this_year(self):
        recent = today() - timedelta(days=5)
        self.results(csv_file([['Date', 'Description', 'Amount'], [recent.strftime('%d %b'), 'Recent', 10]]))
        self.assertEqual(Expense.objects.get().date, recent)

    def test_indian_day_first_dates_are_read_day_first(self):
        self.results(csv_file([['Date', 'Description', 'Amount'], ['03/10/2025', 'x', 10]]))
        self.assertEqual(Expense.objects.get().date, date(2025, 10, 3))


class MoneyTests(Base):
    def test_the_account_balance_drops_by_what_was_imported_and_the_ledger_agrees(self):
        rows = [['Date', 'Description', 'Amount'], [today().isoformat(), 'a', 100.5], [today().isoformat(), 'b', 200]]
        self.results(csv_file(rows))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('9699.50'))
        self.assertEqual(LedgerReadService.get_account_balance(self.bank), D('9699.50'))
        self.assertEqual(JournalEntry.objects.filter(source_type='EXPENSE').count(), 2)

    def test_dollar_rows_on_a_rupee_account_convert_at_the_current_rate(self):
        FXRate.objects.update_or_create(from_currency='USD', to_currency='INR', as_of_date=today(),
                                        defaults={'rate': D('80'), 'source': 't'})
        cache.clear()
        self.results(csv_file([['Date', 'Description', 'Amount'], [today().isoformat(), 'Hosting', 10]]), currency='$')
        expense = Expense.objects.get()
        self.assertEqual((expense.currency, expense.base_amount), ('$', D('800.00')))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('9200.00'))

    def test_re_uploading_the_same_file_adds_nothing_and_changes_no_balance(self):
        rows = [['Date', 'Description', 'Amount'], [today().isoformat(), 'a', 100], [today().isoformat(), 'b', 200]]
        self.results(csv_file(rows))
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['duplicate_count']), (0, 2))
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.balance, D('9700.00'))

    def test_the_dashboard_figures_move_at_once_after_an_import(self):
        from expenses.views.dashboard import AnalyticsView
        AnalyticsView._force_cache_in_testing = True
        try:
            first = self.client.get(reverse('analytics')).context['total_expense_ytd']
            self.results(csv_file([['Date', 'Description', 'Amount'], [today().isoformat(), 'new', 500]]))
            second = self.client.get(reverse('analytics')).context['total_expense_ytd']
            self.assertEqual(second, first + 500)
        finally:
            AnalyticsView._force_cache_in_testing = False


class PlanLimitTests(Base):
    def test_a_free_account_imports_only_what_the_months_cap_has_room_for(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'expenses_per_month')
        for i in range(cap - 2):
            Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), date=today(), category='Food',
                                   currency='₹', description=f'old{i}')
        rows = [['Date', 'Description', 'Amount']] + [[today().isoformat(), f'new{i}', 5 + i] for i in range(5)]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['limit_skipped']), (2, 3))

    def test_rows_in_other_months_are_not_held_to_the_cap(self):
        UserProfile.objects.filter(user=self.user).update(tier='FREE')
        from finance_tracker.plans import get_limit
        cap = get_limit('FREE', 'expenses_per_month')
        for i in range(cap):
            Expense.objects.create(user=self.user, account=self.bank, amount=D('1'), date=today(), category='Food',
                                   currency='₹', description=f'old{i}')
        last_year = today().replace(year=today().year - 1)
        rows = [['Date', 'Description', 'Amount']] + [[last_year.isoformat(), f'past{i}', 5 + i] for i in range(10)]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['limit_skipped']), (10, 0))

    def test_the_demo_user_cannot_import(self):
        demo = User.objects.create_user(username='demo', email='demo@example.com', password='pw')
        self.client.force_login(demo)
        self.client.post(reverse('upload'), {'file': csv_file([['Date', 'Description', 'Amount'], [today().isoformat(), 'x', 5]])})
        self.assertEqual(Expense.objects.filter(user=demo).count(), 0)
