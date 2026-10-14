"""Audit of the quick-add parser: it must read what people really type, and a wrong guess must be
flagged rather than confidently wrong."""
from datetime import date, timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from expenses.parser import parse_expense_nl
from finance_tracker.ai_utils import predict_category_rule_based

CATS = ['Food', 'Rent', 'Transport', 'Car']


def parse(text, **kw):
    kw.setdefault('user_categories', CATS)
    return parse_expense_nl(text, **kw)


class AmountTests(TestCase):
    def amount(self, text):
        return parse(text)['amount']

    def test_indian_and_western_grouping(self):
        self.assertEqual(self.amount('rent 1,25,000'), '125000.00')
        self.assertEqual(self.amount('pay 1,000,000 advance'), '1000000.00')
        self.assertEqual(self.amount('emi 12,500.00 hdfc'), '12500.00')

    def test_decimals_and_small_amounts(self):
        self.assertEqual(self.amount('coffee 3.5'), '3.50')
        self.assertEqual(self.amount('coffee 0.50'), '0.50')

    def test_multiplier_words(self):
        self.assertEqual(self.amount('1.5k fuel'), '1500.00')
        self.assertEqual(self.amount('2.5 lakh car service'), '250000.00')
        self.assertEqual(self.amount('2 crore flat'), '20000000.00')

    def test_zero_is_not_an_amount(self):
        r = parse('0 coffee')
        self.assertFalse(r['success'])
        self.assertIn('amount', r['check'])

    def test_a_currency_marked_number_beats_bare_numbers(self):
        r = parse('2 coffees ₹300')
        self.assertEqual((r['amount'], r['confidence']['amount']), ('300.00', 'high'))

    def test_a_clock_time_or_a_weight_is_not_the_price(self):
        self.assertEqual(self.amount('dinner 8pm 1200'), '1200.00')
        self.assertEqual(self.amount('5 kg potatoes 100'), '100.00')

    def test_a_phone_number_is_never_the_price(self):
        r = parse('recharge 9876543210 299')
        self.assertEqual((r['amount'], r['confidence']['amount']), ('299.00', 'high'))

    def test_two_bare_numbers_pick_the_larger_but_ask_for_a_check(self):
        r = parse('uber 250 for 2 people')
        self.assertEqual(r['amount'], '250.00')
        self.assertIn('amount', r['check'])


class CurrencyTests(TestCase):
    def test_symbol_word_and_suffix_forms(self):
        for text, cur in (('$20 lunch', '$'), ('20 usd lunch', '$'), ('cab 250 rs', '₹'), ('Rs.500 chai', '₹'),
                          ('500rs chai', '₹'), ('€15 museum', '€'), ('£8 pint', '£')):
            self.assertEqual(parse(text)['currency'], cur, text)

    def test_no_signal_uses_the_users_currency(self):
        r = parse('lunch 200', default_currency='$')
        self.assertEqual((r['currency'], r['currency_found']), ('$', False))


class DateTests(TestCase):
    def test_iso_dates_are_read_as_year_month_day(self):
        for text in ('lunch 2024-10-03 250', '250 lunch 2024/10/03', 'lunch 250 2024-10-3'):
            r = parse(text)
            self.assertEqual((r['date'], r['amount']), ('2024-10-03', '250.00'), text)

    def test_day_month_year_in_the_indian_order(self):
        r = parse('paid 450 on 03-10-2025 for groceries')
        self.assertEqual((r['date'], r['amount']), ('2025-10-03', '450.00'))
        self.assertEqual(parse('450 03/10/25')['date'], '2025-10-03')

    def test_names_of_months_either_way_round(self):
        today = timezone.localdate()
        for text in ('taxi 12th oct 340', 'taxi 340 oct 12th', 'taxi 340 12 October'):
            r = parse(text)
            self.assertEqual(r['amount'], '340.00', text)
            self.assertEqual((int(r['date'][5:7]), int(r['date'][8:])), (10, 12), text)
            self.assertLessEqual(date.fromisoformat(r['date']), today + timedelta(days=31))

    def test_a_date_without_a_year_never_lands_in_the_far_future(self):
        today = timezone.localdate()
        far = (today + timedelta(days=120))
        r = parse(f'rent 5000 {far.day} {far.strftime("%b")}')
        self.assertLess(date.fromisoformat(r['date']), today + timedelta(days=32))

    def test_an_impossible_date_is_left_in_the_description(self):
        r = parse('movie 600 feb 30')
        self.assertFalse(r['date_found'])
        self.assertEqual(r['amount'], '600.00')

    def test_the_date_is_not_taken_for_the_amount(self):
        r = parse('spent 1000 on 31 dec')
        self.assertEqual(r['amount'], '1000.00')


class CategoryRuleTests(SimpleTestCase):
    """Keywords match whole words only: a word that merely contains one is not that thing."""

    def test_words_that_contain_a_keyword_do_not_match_it(self):
        for text in ('credit card annual', 'cola', 'vegas trip', 'barber', 'teacher gift', 'warehouse',
                     'marigold flowers', 'public transport'):
            self.assertIsNone(predict_category_rule_based(text), text)

    def test_plain_and_plural_keywords_match(self):
        expected = {'snacks': 'Dining Out', 'coffees': 'Dining Out', 'taxis': 'Transport', 'movies': 'Entertainment',
                    'books': 'Education', 'chai': 'Dining Out', 'petrol pump': 'Transport', 'SIP': 'Investment',
                    'gym membership': 'Health', 'mutual fund sip': 'Investment', 'जेवण': 'Dining Out'}
        for text, category in expected.items():
            self.assertEqual(predict_category_rule_based(text), category, text)

    def test_a_keyword_inside_a_sentence_matches(self):
        self.assertEqual(predict_category_rule_based('paid the electricity bill'), 'Utilities')
        self.assertEqual(predict_category_rule_based('Dinner at the restaurant!'), 'Dining Out')


class UsersOwnCategoryTests(TestCase):
    def test_a_category_named_as_a_whole_word_wins(self):
        r = parse('Rent for october 15000')
        self.assertEqual((r['category'], r['confidence']['category']), ('Rent', 'high'))

    def test_a_word_that_merely_contains_a_category_name_does_not(self):
        r = parse('parents dinner 500')
        self.assertNotEqual(r['category'], 'Rent')
        r = parse('carpet 500')
        self.assertNotEqual(r['category'], 'Car')

    def test_an_unknown_description_is_other_and_low_confidence(self):
        r = parse('zxqv 500')
        self.assertEqual((r['category'], r['confidence']['category']), ('Other', 'low'))
        self.assertIn('category', r['check'])


class AccountAndPaymentTests(TestCase):
    ACCOUNTS = [
        {'id': 1, 'name': 'HDFC Savings', 'type': 'SAVINGS_ACCOUNT'},
        {'id': 2, 'name': 'HDFC Regalia Card', 'type': 'CREDIT_CARD'},
        {'id': 3, 'name': 'SBI', 'type': 'SAVINGS_ACCOUNT'},
        {'id': 4, 'name': 'Paytm Wallet', 'type': 'WALLET'},
    ]

    def test_a_distinct_account_name_is_found(self):
        r = parse('300 sbi snacks', account_info=self.ACCOUNTS)
        self.assertEqual((r['account_id'], r['confidence']['account']), (3, 'high'))

    def test_a_shared_bank_name_is_ambiguous_not_guessed_confidently(self):
        r = parse('300 hdfc snacks', account_info=self.ACCOUNTS)
        self.assertEqual(r['confidence']['account'], 'low')
        self.assertIn('account', r['check'])

    def test_the_longer_name_wins_over_the_shared_word(self):
        r = parse('300 hdfc regalia snacks', account_info=self.ACCOUNTS)
        self.assertEqual(r['account_id'], 2)

    def test_the_words_in_the_text_are_removed_from_the_description(self):
        r = parse('300 sbi snacks upi', account_info=self.ACCOUNTS)
        self.assertEqual(r['description'], 'Snacks')
        self.assertEqual(r['payment_method'], 'UPI')

    def test_upi_apps_mean_upi(self):
        for text in ('200 gpay tea', '200 phonepe tea', '200 paytm tea'):
            self.assertEqual(parse(text)['payment_method'], 'UPI', text)

    def test_conflicting_payment_words_are_flagged(self):
        r = parse('500 upi cash dinner')
        self.assertEqual(r['confidence']['payment_method'], 'low')

    def test_payment_words_inside_other_words_are_ignored(self):
        self.assertIsNone(parse('500 piccolo dinner')['payment_method'])
        self.assertIsNone(parse('500 accra trip')['payment_method'])


class RobustnessTests(TestCase):
    def test_empty_and_blank_input(self):
        self.assertIsNone(parse(''))
        self.assertIsNone(parse('   '))

    def test_odd_input_never_raises(self):
        for text in ('₹', '!!!', '1e3', '--', '9' * 40, 'a' * 5000, '०१२३', '1/0', '0/0/0', '32/13', '₹₹₹ 5'):
            parse(text)

    def test_a_very_large_number_is_still_a_decimal_not_a_crash(self):
        self.assertEqual(parse('rent ₹99999999999')['amount'], '99999999999.00')

    def test_the_description_never_comes_back_empty(self):
        self.assertEqual(parse('450')['description'], 'Expense')
