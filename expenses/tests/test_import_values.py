"""Reading single cells of a statement: amounts and dates, one odd bank format at a time."""
from datetime import date, datetime
from decimal import Decimal

from django.test import SimpleTestCase

from expenses.importing.values import detect_day_first, parse_amount, parse_date

D = Decimal


class ParseAmountTests(SimpleTestCase):
    def check(self, raw, value, marker=None):
        result = parse_amount(raw)
        self.assertIsNotNone(result, raw)
        self.assertEqual((result.value, result.marker), (D(value), marker), raw)

    def test_plain_and_indian_grouped_numbers(self):
        self.check('350', '350')
        self.check('1,200.50', '1200.50')
        self.check('1,20,000.50', '120000.50')
        self.check(' 99.5 ', '99.5')

    def test_currency_words_and_symbols_are_ignored(self):
        for raw in ('₹350', 'Rs. 350', 'Rs 350', 'INR 350', 'inr350', '$ 350', '€350'):
            self.check(raw, '350')

    def test_negative_forms(self):
        self.check('-100', '-100')
        self.check('(100.00)', '-100.00')
        self.check('100-', '-100')
        self.check('−100', '-100')              # a typographic minus
        self.check('+100', '100')

    def test_a_debit_or_credit_marker_after_the_amount_is_reported(self):
        self.check('350.00 Dr', '350.00', 'DR')
        self.check('1,500.00 CR', '1500.00', 'CR')
        self.check('Dr. 20', '20', 'DR')
        self.check('120 Debit', '120', 'DR')

    def test_numbers_that_arrive_as_numbers(self):
        self.check(12.5, '12.5')
        self.check(7, '7')
        self.check(D('3.10'), '3.10')

    def test_text_that_is_not_an_amount(self):
        for raw in (None, '', '  ', 'abc', 'UPI 12345 350', '1.2.3', 'N/A', '--', True, float('nan'), float('inf')):
            self.assertIsNone(parse_amount(raw), raw)

    def test_zero_is_an_amount_of_zero_not_missing(self):
        self.assertEqual(parse_amount('0.00').value, D('0'))


class ParseDateTests(SimpleTestCase):
    def test_indian_day_first_is_the_default(self):
        self.assertEqual(parse_date('03/04/2026'), date(2026, 4, 3))
        self.assertEqual(parse_date('03-04-2026'), date(2026, 4, 3))
        self.assertEqual(parse_date('03.04.2026'), date(2026, 4, 3))
        self.assertEqual(parse_date('03/04/26'), date(2026, 4, 3))

    def test_month_first_when_the_file_says_so(self):
        self.assertEqual(parse_date('03/04/2026', day_first=False), date(2026, 3, 4))

    def test_a_day_above_twelve_is_read_correctly_either_way(self):
        self.assertEqual(parse_date('25/04/2026'), date(2026, 4, 25))
        self.assertEqual(parse_date('04/25/2026'), date(2026, 4, 25))

    def test_unambiguous_formats(self):
        expected = date(2026, 4, 3)
        for text in ('2026-04-03', '2026/04/03', '3 Apr 2026', '03 April 2026', '03-Apr-2026', '03-Apr-26',
                     'Apr 3, 2026', 'April 3, 2026', '2026-04-03 14:30:00', '2026-04-03T14:30:00',
                     '03/04/2026 14:30', '03/04/2026 14:30:59', '03-04-2026 09:05:00'):
            self.assertEqual(parse_date(text), expected, text)

    def test_cells_that_are_already_dates(self):
        self.assertEqual(parse_date(date(2026, 4, 3)), date(2026, 4, 3))
        self.assertEqual(parse_date(datetime(2026, 4, 3, 10, 0)), date(2026, 4, 3))

    def test_not_a_date(self):
        for value in (None, '', '  ', 'abc', 'Total', 'Opening Balance', '45'):
            self.assertIsNone(parse_date(value), value)


class DetectDayFirstTests(SimpleTestCase):
    def test_one_day_above_twelve_in_the_first_place_settles_it(self):
        self.assertTrue(detect_day_first(['25/04/2026', '03/04/2026']))

    def test_one_day_above_twelve_in_the_second_place_means_month_first(self):
        self.assertFalse(detect_day_first(['04/25/2026', '03/04/2026']))

    def test_nothing_to_go_on_means_day_first_as_indian_statements_are(self):
        self.assertTrue(detect_day_first(['03/04/2026', '05/06/2026']))
        self.assertTrue(detect_day_first(['2026-04-03', None, '']))

    def test_a_file_that_mixes_both_is_read_cell_by_cell(self):
        self.assertIsNone(detect_day_first(['25/04/2026', '04/25/2026']))

    def test_real_date_cells_are_ignored(self):
        self.assertTrue(detect_day_first([date(2026, 4, 3), datetime(2026, 4, 4)]))
