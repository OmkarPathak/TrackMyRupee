"""Which column is which, which rows are transactions, and which way the money moved: the import's pure logic."""
from decimal import Decimal

from django.test import SimpleTestCase

from expenses.importing.layout import classify_header, detect_layout
from expenses.importing.parsing import decode_bytes, sniff_delimiter
from expenses.importing.rows import CREDIT, DEBIT, assign_directions, parse_rows

D = Decimal


class ClassifyHeaderTests(SimpleTestCase):
    def test_each_cell_is_classified_on_its_own(self):
        expected = {
            'Date': 'date', 'Txn Date': 'date', 'Transaction Date': 'date', 'Posting Date': 'date',
            'Value Dt': 'value_date', 'Value Date': 'value_date',
            'Description': 'description', 'Narration': 'description', 'Particulars': 'description',
            'Transaction Remarks': 'description', 'Transaction Details': 'description', 'Payee': 'description',
            'Amount': 'amount', 'Amt': 'amount', 'Transaction Amount (INR)': 'amount', 'Total': 'amount',
            'Withdrawal Amt.': 'debit', 'Debit': 'debit', 'Dr': 'debit', 'Paid Out': 'debit',
            'Deposit Amt.': 'credit', 'Credit': 'credit', 'Cr': 'credit', 'Money In': 'credit',
            'Closing Balance': 'balance', 'Balance': 'balance', 'Running Balance': 'balance',
            'Chq./Ref.No.': 'reference', 'UTR No': 'reference', 'Transaction ID': 'reference',
            'Transaction Type': 'type', 'Dr/Cr': 'type', 'Debit/Credit': 'type',
            'Category': 'category', 'Tag': 'category', 'Type': 'category',
        }
        for header, role in expected.items():
            self.assertEqual(classify_header(header), role, header)

    def test_unrelated_headers_are_not_classified(self):
        for header in ('', None, 'Sr No', 'Branch', 'Notes2x'):
            self.assertIsNone(classify_header(header), header)

    def test_transaction_remarks_is_not_mistaken_for_a_date(self):
        # Every cell used to be tested for the word "transaction", which made the first such column the date.
        self.assertNotEqual(classify_header('Transaction Remarks'), 'date')
        self.assertNotEqual(classify_header('Transaction Amount'), 'date')


class DetectLayoutTests(SimpleTestCase):
    def test_a_header_below_a_long_block_of_account_details(self):
        rows = [['Account Statement'], ['Customer', 'A B']] + [['detail', i] for i in range(35)] + [
            [], ['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance'],
            ['01/10/2026', 'Zomato', '350', '', '9650'],
        ]
        layout = detect_layout(rows)
        self.assertEqual(layout.columns, {'date': 0, 'description': 1, 'debit': 2, 'credit': 3, 'balance': 4})
        self.assertEqual(layout.start, 39)

    def test_value_date_is_used_only_when_there_is_no_other_date(self):
        both = detect_layout([['Value Dt', 'Txn Date', 'Narration', 'Amount'], ['x']])
        self.assertEqual(both.columns['date'], 1)
        only_value = detect_layout([['Value Date', 'Narration', 'Amount'], ['x']])
        self.assertEqual(only_value.columns['date'], 0)

    def test_no_header_is_guessed_from_the_data(self):
        layout = detect_layout([['2026-04-03', 'Coffee', 120], ['2026-04-04', 'Taxi', 300]])
        self.assertFalse(layout.has_header)
        self.assertEqual((layout.columns['date'], layout.columns['amount'], layout.columns['description']), (0, 2, 1))

    def test_nothing_recognisable(self):
        self.assertIsNone(detect_layout([['hello', 'world'], ['foo', 'bar']]))
        self.assertIsNone(detect_layout([]))


class ParseRowsTests(SimpleTestCase):
    def parse(self, rows):
        layout = detect_layout(rows)
        parsed = parse_rows(rows, layout, True)
        assign_directions(parsed)
        return parsed

    def test_footers_and_repeated_headers_are_not_transactions(self):
        rows = [
            ['Date', 'Narration', 'Amount'],
            ['01/10/2026', 'Tea', '50'],
            ['Date', 'Narration', 'Amount'],                    # the header repeated on page two
            ['02/10/2026', 'Bus', '30'],
            ['Closing Balance', '', '12345'],
            ['Total', '', '80'],
            ['*** End of statement ***', '', ''],
        ]
        self.assertEqual([r.description for r in self.parse(rows)], ['Tea', 'Bus'])

    def test_a_row_with_an_amount_but_a_bad_date_is_an_error_not_skipped(self):
        parsed = self.parse([['Date', 'Narration', 'Amount'], ['someday', 'Tea', '50']])
        self.assertEqual(len(parsed), 1)
        self.assertIn('Invalid date', parsed[0].error)

    def test_rows_with_no_amount_are_not_transactions(self):
        parsed = self.parse([['Date', 'Narration', 'Amount'], ['01/10/2026', 'Memo only', ''], ['01/10/2026', 'Zero', '0.00']])
        self.assertEqual(parsed, [])

    def test_text_in_the_amount_column_is_reported(self):
        parsed = self.parse([['Date', 'Narration', 'Amount'], ['01/10/2026', 'Tea', 'fifty']])
        self.assertIn('Invalid amount', parsed[0].error)

    def test_a_row_cannot_have_both_a_debit_and_a_credit(self):
        parsed = self.parse([['Date', 'Narration', 'Debit', 'Credit'], ['01/10/2026', 'Odd', '10', '20']])
        self.assertIn('debit and a credit', parsed[0].error)

    def test_reference_and_category_columns_are_read(self):
        parsed = self.parse([['Date', 'Narration', 'Amount', 'Chq./Ref.No.', 'Category'],
                             ['01/10/2026', 'Tea', '50', ' 0012345 ', 'Food']])
        self.assertEqual((parsed[0].reference, parsed[0].category), ('0012345', 'Food'))


class DirectionTests(SimpleTestCase):
    def directions(self, rows):
        layout = detect_layout(rows)
        parsed = parse_rows(rows, layout, True)
        notes = assign_directions(parsed)
        return [r.direction for r in parsed], notes

    def test_separate_debit_and_credit_columns(self):
        directions, notes = self.directions([
            ['Date', 'Narration', 'Withdrawal Amt.', 'Deposit Amt.'],
            ['01/10/2026', 'Zomato', '350', ''], ['02/10/2026', 'Salary', '', '50000']])
        self.assertEqual((directions, notes), ([DEBIT, CREDIT], []))

    def test_a_dr_cr_column(self):
        directions, _ = self.directions([
            ['Date', 'Narration', 'Amount', 'Dr/Cr'],
            ['01/10/2026', 'Zomato', '350', 'DR'], ['02/10/2026', 'Salary', '50000', 'CR']])
        self.assertEqual(directions, [DEBIT, CREDIT])

    def test_dr_cr_written_after_the_amount(self):
        directions, _ = self.directions([
            ['Date', 'Narration', 'Amount'],
            ['01/10/2026', 'Zomato', '350.00 Dr'], ['02/10/2026', 'Salary', '50,000.00 Cr']])
        self.assertEqual(directions, [DEBIT, CREDIT])

    def test_the_balance_column_oldest_first(self):
        directions, notes = self.directions([
            ['Date', 'Narration', 'Amount', 'Balance'],
            ['01/10/2026', 'Opening', '100', '10100'],
            ['02/10/2026', 'Tea', '50', '10050'],
            ['03/10/2026', 'Salary', '5000', '15050'],
            ['04/10/2026', 'Rent', '2000', '13050']])
        self.assertEqual(directions[1:], [DEBIT, CREDIT, DEBIT])
        self.assertEqual(notes, [])

    def test_the_balance_column_newest_first(self):
        directions, _ = self.directions([
            ['Date', 'Narration', 'Amount', 'Balance'],
            ['04/10/2026', 'Rent', '2000', '13050'],
            ['03/10/2026', 'Salary', '5000', '15050'],
            ['02/10/2026', 'Tea', '50', '10050'],
            ['01/10/2026', 'Opening', '100', '10100']])
        self.assertEqual(directions[:3], [DEBIT, CREDIT, DEBIT])

    def test_a_balance_that_does_not_fit_the_amounts_is_ignored(self):
        directions, _ = self.directions([
            ['Date', 'Narration', 'Amount', 'Balance'],
            ['01/10/2026', 'A', '100', '555'], ['02/10/2026', 'B', '200', '12'], ['03/10/2026', 'C', '300', '999']])
        self.assertEqual(directions, [DEBIT, DEBIT, DEBIT])         # all one sign: all spending

    def test_words_tell_which_sign_is_the_credits_even_when_credits_are_the_majority(self):
        directions, notes = self.directions([
            ['Date', 'Narration', 'Amount'],
            ['01/10/2026', 'Salary October', '50000'], ['02/10/2026', 'Interest credited', '120'],
            ['03/10/2026', 'Refund Amazon', '300'], ['04/10/2026', 'Rent', '-15000']])
        self.assertEqual(directions, [CREDIT, CREDIT, CREDIT, DEBIT])
        self.assertEqual(notes, [])

    def test_with_no_words_to_go_on_the_commoner_sign_is_the_spending_and_the_person_is_told(self):
        directions, notes = self.directions([
            ['Date', 'Narration', 'Amount'],
            ['01/10/2026', 'a', '10'], ['02/10/2026', 'b', '20'], ['03/10/2026', 'c', '-5']])
        self.assertEqual(directions, [DEBIT, DEBIT, CREDIT])
        self.assertEqual(notes, ['assumed_positive_are_expenses'])

    def test_an_even_split_with_no_words_is_not_guessed(self):
        directions, notes = self.directions([
            ['Date', 'Narration', 'Amount'], ['01/10/2026', 'a', '10'], ['02/10/2026', 'b', '-10']])
        self.assertEqual((directions, notes), ([DEBIT, DEBIT], []))


class FileReadingTests(SimpleTestCase):
    def test_delimiters(self):
        self.assertEqual(sniff_delimiter('a,b,c\n1,2,3\n'), ',')
        self.assertEqual(sniff_delimiter('a;b;c\n1;2;3\n'), ';')
        self.assertEqual(sniff_delimiter('a\tb\tc\n1\t2\t3\n'), '\t')
        self.assertEqual(sniff_delimiter('a|b|c\n1|2|3\n'), '|')
        self.assertEqual(sniff_delimiter('only one column\nanother\n'), ',')

    def test_a_decimal_comma_does_not_beat_the_real_delimiter(self):
        self.assertEqual(sniff_delimiter('Date;Description;Amount\n2026-10-01;Tea;50,5\n2026-10-02;Bus;30,25\n'), ';')

    def test_encodings(self):
        self.assertEqual(decode_bytes('﻿Date'.encode('utf-8')), 'Date')          # a BOM is dropped
        self.assertEqual(decode_bytes('Café'.encode('cp1252')), 'Café')
        self.assertEqual(decode_bytes('₹ 50'.encode('utf-8')), '₹ 50')
        self.assertEqual(decode_bytes('€50'.encode('cp1252')), '€50')
