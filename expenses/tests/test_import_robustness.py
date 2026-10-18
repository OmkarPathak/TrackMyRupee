"""The import against files shaped like real bank and card statements. Each class is a failure that was seen:
see the plan for making importing robust."""
import csv
import io
from datetime import date
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from expenses.ledger_service import LedgerPostingService
from expenses.models import Account, Expense

D = Decimal


def csv_file(rows, name='statement.csv', delimiter=',', encoding='utf-8'):
    out = io.StringIO()
    csv.writer(out, delimiter=delimiter).writerows(rows)
    return SimpleUploadedFile(name, out.getvalue().encode(encoding), content_type='text/csv')


def xlsx_file(rows, name='statement.xlsx'):
    workbook = openpyxl.Workbook()
    for row in rows:
        workbook.active.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue())


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='robust', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.currency = 'PRO', True, '₹'
        profile.save()
        self.client.force_login(self.user)
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=D('100000'), currency='₹')
        LedgerPostingService.post_opening_balance(account=self.bank)

    def upload(self, file):
        cache.clear()
        return self.client.post(reverse('upload'), {'account': self.bank.id, 'currency': '₹', 'file': file})

    def results(self, file):
        response = self.upload(file)
        self.assertEqual(response.status_code, 200)
        return response.context['results']

    def imported(self):
        return list(Expense.objects.filter(user=self.user).order_by('date', 'amount').values_list('description', 'amount'))


class IdenticalPaymentsTests(Base):
    rows = [['Date', 'Description', 'Amount'],
            ['2026-09-01', 'UPI Chai', 50], ['2026-09-01', 'UPI Chai', 50], ['2026-09-02', 'Uber', 200]]

    def test_two_identical_payments_on_one_day_are_both_kept(self):
        res = self.results(csv_file(self.rows))
        self.assertEqual((res['created_count'], res['duplicate_count']), (3, 0))
        self.assertEqual(Expense.objects.filter(description='UPI Chai').count(), 2)

    def test_importing_the_same_file_again_adds_nothing(self):
        self.results(csv_file(self.rows))
        res = self.results(csv_file(self.rows))
        self.assertEqual((res['created_count'], res['duplicate_count']), (0, 3))
        self.assertEqual(Expense.objects.count(), 3)

    def test_a_longer_file_adds_only_the_new_payment(self):
        self.results(csv_file(self.rows))
        res = self.results(csv_file(self.rows + [['2026-09-01', 'UPI Chai', 50]]))      # a third tea that day
        self.assertEqual((res['created_count'], res['duplicate_count']), (1, 3))
        self.assertEqual(Expense.objects.filter(description='UPI Chai').count(), 3)

    def test_deleting_one_of_two_and_importing_again_restores_it(self):
        self.results(csv_file(self.rows))
        Expense.objects.filter(description='UPI Chai').first().delete()
        res = self.results(csv_file(self.rows))
        self.assertEqual(res['created_count'], 1)
        self.assertEqual(Expense.objects.filter(description='UPI Chai').count(), 2)

    def test_a_reference_number_makes_a_repeated_row_a_real_duplicate(self):
        rows = [['Date', 'Narration', 'Amount', 'Chq./Ref.No.'],
                ['2026-09-01', 'UPI Chai', 50, 'R1'], ['2026-09-01', 'UPI Chai', 50, 'R2'],
                ['2026-09-01', 'UPI Chai', 50, 'R1']]                # the same transaction listed twice
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['duplicate_count']), (2, 1))
        self.assertEqual(self.results(csv_file(rows))['created_count'], 0)


class ExistingExpensesTests(Base):
    def test_changing_a_category_afterwards_does_not_make_a_re_import_add_rows(self):
        rows = [['Date', 'Description', 'Amount'], ['2026-09-01', 'Swiggy dinner', 300]]
        self.results(csv_file(rows))
        Expense.objects.update(category='Eating Out')
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['duplicate_count']), (0, 1))

    def test_an_expense_typed_by_hand_is_not_imported_again(self):
        Expense.objects.create(user=self.user, date=date(2026, 9, 1), amount=D('300'), description='Swiggy  Dinner',
                               category='Food', currency='₹', base_amount=D('300'), exchange_rate=1)
        res = self.results(csv_file([['Date', 'Description', 'Amount'], ['2026-09-01', 'swiggy dinner', 300]]))
        self.assertEqual((res['created_count'], res['duplicate_count']), (0, 1))

    def test_the_same_amount_well_apart_or_with_another_description_is_new(self):
        Expense.objects.create(user=self.user, date=date(2026, 9, 1), amount=D('300'), description='Swiggy',
                               category='Food', currency='₹', base_amount=D('300'), exchange_rate=1)
        res = self.results(csv_file([['Date', 'Description', 'Amount'],
                                     ['2026-09-05', 'Swiggy', 300], ['2026-09-01', 'Zomato', 300]]))
        self.assertEqual(res['created_count'], 2)

    def test_a_soft_deleted_expense_does_not_block_the_import(self):
        rows = [['Date', 'Description', 'Amount'], ['2026-09-01', 'Tea', 50]]
        self.results(csv_file(rows))
        Expense.objects.update(is_deleted=True)
        self.assertEqual(self.results(csv_file(rows))['created_count'], 1)


class WhichWayTheMoneyMovedTests(Base):
    def test_a_statement_with_more_credits_than_debits_does_not_import_the_credits_as_spending(self):
        rows = [['Date', 'Narration', 'Amount'], ['2026-09-01', 'Salary', 50000], ['2026-09-02', 'Interest', 120],
                ['2026-09-03', 'Refund Amazon', 300], ['2026-09-04', 'Rent', -15000]]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (1, 3))
        self.assertEqual(self.imported(), [('Rent', D('15000.00'))])

    def test_the_running_balance_decides_when_there_is_one(self):
        rows = [['Date', 'Narration', 'Amount', 'Balance'],            # newest first, all positive amounts
                ['2026-09-04', 'Rent', 2000, 13050], ['2026-09-03', 'Cash received', 5000, 15050],
                ['2026-09-02', 'Tea', 50, 10050], ['2026-09-01', 'Opening', 100, 10100]]
        res = self.results(csv_file(rows))
        self.assertEqual(res['credit_skipped'], 1)
        self.assertEqual({d for d, _ in self.imported()} >= {'Rent', 'Tea'}, True)
        self.assertNotIn('Cash received', [d for d, _ in self.imported()])

    def test_separate_columns_count_the_deposits_as_credits(self):
        rows = [['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'],
                ['01/09/2026', 'Zomato', '350.00', '', '99650.00'], ['02/09/2026', 'Salary', '', '50000.00', '149650.00']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (1, 1))

    def test_dr_cr_markers(self):
        rows = [['Date', 'Narration', 'Amount'], ['01/09/2026', 'Zomato', '350.00 Dr'], ['02/09/2026', 'Salary', '50,000.00 Cr'],
                ['03/09/2026', 'Bus', '30.00 Dr']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (2, 1))

    def test_a_transaction_type_column(self):
        rows = [['Date', 'Narration', 'Amount', 'Transaction Type'], ['01/09/2026', 'Zomato', 350, 'Debit'],
                ['02/09/2026', 'Salary', 50000, 'Credit']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped']), (1, 1))

    def test_a_guess_is_reported_to_the_person(self):
        rows = [['Date', 'Narration', 'Amount'], ['2026-09-01', 'a', 10], ['2026-09-02', 'b', 20], ['2026-09-03', 'c', -5]]
        response = self.upload(csv_file(rows))
        self.assertEqual(response.context['results']['notes'], ['assumed_positive_are_expenses'])
        self.assertTrue(any('positive amounts' in str(m) for m in response.context['messages']))

    def test_no_message_when_the_direction_was_certain(self):
        rows = [['Date', 'Narration', 'Amount', 'Dr/Cr'], ['2026-09-01', 'a', 10, 'DR']]
        response = self.upload(csv_file(rows))
        self.assertEqual(response.context['results']['notes'], [])


class FilesBanksActuallyWriteTests(Base):
    def test_semicolon_separated(self):
        res = self.results(csv_file([['Date', 'Description', 'Amount'], ['2026-09-01', 'Chai', 50], ['2026-09-02', 'Uber', 200]], delimiter=';'))
        self.assertEqual(res['created_count'], 2)

    def test_tab_separated(self):
        res = self.results(csv_file([['Date', 'Description', 'Amount'], ['2026-09-01', 'Chai', 50]], delimiter='\t', name='s.csv'))
        self.assertEqual(res['created_count'], 1)

    def test_a_utf8_bom_and_a_windows_1252_file(self):
        rows = [['Date', 'Description', 'Amount'], ['2026-09-01', 'Café', 50]]
        self.assertEqual(self.results(csv_file(rows, encoding='utf-8-sig'))['created_count'], 1)
        Expense.objects.all().delete()
        self.assertEqual(self.results(csv_file(rows, encoding='cp1252'))['created_count'], 1)
        self.assertEqual(Expense.objects.get().description, 'Café')

    def test_transaction_remarks_and_transaction_date_headers(self):
        rows = [['Transaction Remarks', 'Transaction Date', 'Withdrawal Amount (INR)'], ['UPI-Zomato', '02/09/2026', '350']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['error_count']), (1, 0))
        expense = Expense.objects.get()
        self.assertEqual((expense.description, expense.date, expense.amount), ('UPI-Zomato', date(2026, 9, 2), D('350.00')))

    def test_a_long_preamble_a_repeated_header_and_a_footer(self):
        rows = [['HDFC BANK Account Statement'], ['Customer ID', '12345']] + [['Address line', str(i)] for i in range(30)] + [
            [], ['Date', 'Narration', 'Chq./Ref.No.', 'Value Dt', 'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'],
            ['01/09/26', 'UPI-ZOMATO', 'R1', '01/09/26', '350.00', '', '99650.00'],
            ['02/09/26', 'UPI-OLA', 'R2', '02/09/26', '120.00', '', '99530.00'],
            ['Date', 'Narration', 'Chq./Ref.No.', 'Value Dt', 'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'],
            ['03/09/26', 'NEFT-SALARY', 'R3', '03/09/26', '', '50000.00', '149530.00'],
            ['Opening Balance', '', '', '', '', '', '100000.00'],
            ['Closing Balance', '', '', '', '', '', '149530.00'],
            ['Statement Summary', '', '', '', '', '', '']]
        res = self.results(csv_file(rows))
        self.assertEqual((res['created_count'], res['credit_skipped'], res['error_count']), (2, 1, 0))
        self.assertEqual({d for d, _ in self.imported()}, {'UPI-ZOMATO', 'UPI-OLA'})

    def test_month_first_dates_when_the_file_proves_it(self):
        rows = [['Date', 'Description', 'Amount'], ['04/25/2026', 'B', 20], ['03/04/2026', 'A', 10]]
        self.results(csv_file(rows))
        self.assertEqual(Expense.objects.get(description='A').date, date(2026, 3, 4))

    def test_indian_day_first_dates_when_nothing_says_otherwise(self):
        self.results(csv_file([['Date', 'Description', 'Amount'], ['03/04/2026', 'A', 10]]))
        self.assertEqual(Expense.objects.get().date, date(2026, 4, 3))

    def test_amounts_with_rupee_signs_and_words(self):
        rows = [['Date', 'Description', 'Amount'], ['2026-09-01', 'A', 'Rs. 1,200'], ['2026-09-02', 'B', '₹ 350.50'],
                ['2026-09-03', 'C', 'INR 99']]
        self.results(csv_file(rows))
        self.assertEqual([a for _, a in self.imported()], [D('1200.00'), D('350.50'), D('99.00')])

    def test_a_spreadsheet_with_real_date_cells(self):
        res = self.results(xlsx_file([['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.'],
                                      [date(2026, 9, 1), 'Zomato', 350, None], [date(2026, 9, 2), 'Salary', None, 50000]]))
        self.assertEqual((res['created_count'], res['credit_skipped']), (1, 1))
        self.assertEqual(Expense.objects.get().date, date(2026, 9, 1))

    def test_a_file_with_nothing_recognisable_says_so(self):
        response = self.upload(csv_file([['hello', 'world'], ['foo', 'bar']]))
        self.assertIsNone(response.context['results'])
        self.assertTrue(any('Could not detect' in str(m) for m in response.context['messages']))
