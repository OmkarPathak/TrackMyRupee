"""The two judgements the import makes about a row: is it an expense already stored (with other words), and is it
money moved between the person's own accounts. Pure functions, so each rule has a small test."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from expenses.importing.matching import (Candidate, account_names, best_match, find_transfer_target,
                                         looks_like_transfer, meaningful_tokens, similarity)

D = Decimal


def candidate(pk, day, amount, text):
    return Candidate(pk, date(2026, 9, day), D(str(amount)), text, meaningful_tokens(text))


class TokenTests(SimpleTestCase):
    def test_a_narration_keeps_only_the_words_that_say_who_was_paid(self):
        self.assertEqual(meaningful_tokens('UPI-SWIGGY-9876543210@ybl'), {'swiggy'})
        self.assertEqual(meaningful_tokens('POS 4512 BIG BAZAAR GROCERY'), {'big', 'bazaar', 'grocery'})
        self.assertEqual(meaningful_tokens('NEFT-Salary Sept'), {'salary', 'sept'})
        self.assertEqual(meaningful_tokens('Swiggy dinner'), {'swiggy', 'dinner'})
        self.assertEqual(meaningful_tokens(None), frozenset())
        self.assertEqual(meaningful_tokens('UPI 123 456'), frozenset())

    def test_similarity_is_how_much_of_the_shorter_one_is_in_the_other(self):
        swiggy_note, bank = meaningful_tokens('Swiggy dinner'), meaningful_tokens('UPI-SWIGGY-1@ybl')
        self.assertEqual(similarity(swiggy_note, bank), 1.0)
        self.assertEqual(similarity(meaningful_tokens('Big Bazaar'), meaningful_tokens('Reliance Fresh')), 0.0)
        self.assertEqual(similarity(frozenset(), bank), 0.0)

    def test_a_word_and_its_longer_form_match(self):
        self.assertEqual(similarity(frozenset({'swiggy'}), frozenset({'swiggyinstamart'})), 1.0)
        self.assertEqual(similarity(frozenset({'tea'}), frozenset({'teacher'})), 0.0)       # too short to be a prefix


class BestMatchTests(SimpleTestCase):
    def tokens(self, text):
        return meaningful_tokens(text)

    def test_same_amount_close_in_time_and_a_word_in_common(self):
        pool = [candidate(1, 1, 300, 'Swiggy dinner')]
        hit = best_match(date(2026, 9, 2), D('300'), self.tokens('UPI-SWIGGY-9@ybl'), pool)
        self.assertEqual(hit.id, 1)

    def test_two_days_is_the_edge(self):
        pool = [candidate(1, 1, 300, 'Swiggy dinner')]
        self.assertIsNotNone(best_match(date(2026, 9, 3), D('300'), self.tokens('swiggy'), pool))
        self.assertIsNone(best_match(date(2026, 9, 4), D('300'), self.tokens('swiggy'), pool))

    def test_the_amount_must_be_exactly_the_same(self):
        pool = [candidate(1, 1, 300, 'Swiggy dinner')]
        self.assertIsNone(best_match(date(2026, 9, 1), D('300.01'), self.tokens('swiggy'), pool))

    def test_no_word_in_common_is_not_a_match_however_close_the_rest(self):
        pool = [candidate(1, 1, 300, 'Swiggy dinner')]
        self.assertIsNone(best_match(date(2026, 9, 1), D('300'), self.tokens('UPI-RAHUL-1@ybl'), pool))

    def test_each_stored_expense_accounts_for_one_row_only(self):
        pool = [candidate(1, 1, 300, 'Swiggy dinner')]
        first = best_match(date(2026, 9, 1), D('300'), self.tokens('swiggy'), pool)
        first.used = True
        self.assertIsNone(best_match(date(2026, 9, 1), D('300'), self.tokens('swiggy'), pool))

    def test_the_better_description_wins_then_the_nearer_day(self):
        pool = [candidate(1, 2, 300, 'Swiggy'), candidate(2, 1, 300, 'Swiggy dinner order')]
        row = self.tokens('swiggy dinner')
        self.assertEqual(best_match(date(2026, 9, 1), D('300'), row, pool).id, 2)
        pool = [candidate(1, 3, 300, 'Swiggy dinner'), candidate(2, 2, 300, 'Swiggy dinner')]
        self.assertEqual(best_match(date(2026, 9, 1), D('300'), row, pool).id, 2)


ACCOUNTS = [
    SimpleNamespace(id=1, name='HDFC Savings'),
    SimpleNamespace(id=2, name='HDFC Regalia Credit Card'),
    SimpleNamespace(id=3, name='ICICI Amazon Pay Card'),
    SimpleNamespace(id=4, name='Cash Wallet'),
    SimpleNamespace(id=5, name='ICICI Savings'),
]


class TransferTargetTests(SimpleTestCase):
    def target(self, text, accounts=ACCOUNTS):
        return find_transfer_target(text, account_names(accounts))

    def test_a_credit_card_bill_names_the_card(self):
        self.assertEqual(self.target('HDFC REGALIA CC PAYMENT'), 2)
        self.assertEqual(self.target('BILLDESK ICICI AMAZON CREDIT CARD PAYMENT'), 3)

    def test_a_self_transfer_names_the_account(self):
        self.assertEqual(self.target('NEFT-HDFC-SELF'), 1)

    def test_a_plain_transfer_needs_the_account_to_be_named_in_full(self):
        self.assertEqual(self.target('TRANSFER TO HDFC REGALIA'), 2)
        self.assertIsNone(self.target('NEFT TRANSFER TO RAHUL HDFC'))      # only "hdfc": could be anyone at that bank

    def test_it_must_say_it_is_a_transfer(self):
        self.assertIsNone(self.target('HDFC REGALIA'))
        self.assertIsNone(self.target('ZOMATO ORDER'))
        self.assertIsNone(self.target(''))

    def test_two_equally_good_accounts_are_not_guessed_between(self):
        twins = [SimpleNamespace(id=1, name='ICICI Savings'), SimpleNamespace(id=2, name='ICICI Salary Account')]
        self.assertIsNone(self.target('ICICI SELF TRANSFER', twins))

    def test_the_more_specific_account_wins(self):
        self.assertEqual(self.target('HDFC REGALIA SELF'), 2)

    def test_an_account_with_nothing_distinctive_in_its_name_never_matches(self):
        self.assertIsNone(self.target('CASH WALLET SELF TRANSFER'))

    def test_looks_like_transfer(self):
        for text in ('NEFT-HDFC-SELF', 'CC PAYMENT', 'Fund Transfer to X', 'TRF TO 1234'):
            self.assertTrue(looks_like_transfer(text), text)
        for text in ('ZOMATO', 'Swiggy dinner', '', None):
            self.assertFalse(looks_like_transfer(text), text)
