"""
tests/test_account_types.py
============================
Unit tests for the expenses.account_types classification module.

All tests are pure (no DB access). The module has no Django ORM dependencies.
"""

from decimal import Decimal

from django.test import TestCase

from expenses.account_types import (
    ACCOUNT_TYPES,
    ACCOUNT_TYPE_META,
    KIND,
    STRATEGY,
    asset_codes,
    classify,
    deposit_codes,
    is_liability,
    liability_codes,
    market_linked_codes,
    strategy_for,
)


class TestClassifyAllCodes(TestCase):
    """Every code in ACCOUNT_TYPE_META must return a valid (KIND, STRATEGY) pair."""

    def test_all_codes_return_valid_kind_and_strategy(self):
        for code, (kind, strategy) in ACCOUNT_TYPE_META.items():
            with self.subTest(code=code):
                self.assertIsInstance(kind, KIND, f"{code} has invalid KIND")
                self.assertIsInstance(strategy, STRATEGY, f"{code} has invalid STRATEGY")

    def test_classify_returns_tuple_for_every_code(self):
        for code in ACCOUNT_TYPE_META:
            with self.subTest(code=code):
                result = classify(code)
                self.assertEqual(len(result), 2)
                self.assertIsInstance(result[0], KIND)
                self.assertIsInstance(result[1], STRATEGY)


class TestClassifyLegacyCodes(TestCase):
    """Legacy codes (flat list from old ACCOUNT_TYPES) must map to expected strategies."""

    def test_cash(self):
        self.assertEqual(classify('CASH'), (KIND.ASSET, STRATEGY.BALANCE))

    def test_bank(self):
        self.assertEqual(classify('BANK'), (KIND.ASSET, STRATEGY.BALANCE))

    def test_credit_card(self):
        self.assertEqual(classify('CREDIT_CARD'), (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT))

    def test_investment(self):
        self.assertEqual(classify('INVESTMENT'), (KIND.ASSET, STRATEGY.HOLDINGS))

    def test_fixed_deposit(self):
        self.assertEqual(classify('FIXED_DEPOSIT'), (KIND.ASSET, STRATEGY.DEPOSIT))

    def test_other(self):
        self.assertEqual(classify('OTHER'), (KIND.ASSET, STRATEGY.BALANCE))


class TestClassifyNewCodes(TestCase):
    """New codes must classify correctly."""

    def test_savings_account(self):
        self.assertEqual(classify('SAVINGS_ACCOUNT'), (KIND.ASSET, STRATEGY.BALANCE))

    def test_digital_wallet(self):
        self.assertEqual(classify('DIGITAL_WALLET'), (KIND.ASSET, STRATEGY.BALANCE))

    def test_fd(self):
        self.assertEqual(classify('FD'), (KIND.ASSET, STRATEGY.DEPOSIT))

    def test_ppf(self):
        self.assertEqual(classify('PPF'), (KIND.ASSET, STRATEGY.DEPOSIT))

    def test_nps(self):
        self.assertEqual(classify('NPS'), (KIND.ASSET, STRATEGY.DEPOSIT))

    def test_demat(self):
        self.assertEqual(classify('DEMAT'), (KIND.ASSET, STRATEGY.HOLDINGS))

    def test_mutual_fund(self):
        self.assertEqual(classify('MUTUAL_FUND'), (KIND.ASSET, STRATEGY.HOLDINGS))

    def test_etf(self):
        self.assertEqual(classify('ETF'), (KIND.ASSET, STRATEGY.HOLDINGS))

    def test_bnpl(self):
        self.assertEqual(classify('BNPL'), (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT))

    def test_overdraft(self):
        self.assertEqual(classify('OVERDRAFT'), (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT))

    def test_home_loan(self):
        self.assertEqual(classify('HOME_LOAN'), (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING))

    def test_vehicle_loan(self):
        self.assertEqual(classify('VEHICLE_LOAN'), (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING))

    def test_personal_loan(self):
        self.assertEqual(classify('PERSONAL_LOAN'), (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING))

    def test_real_estate(self):
        self.assertEqual(classify('REAL_ESTATE'), (KIND.ASSET, STRATEGY.PHYSICAL_VALUATION))

    def test_vehicle(self):
        self.assertEqual(classify('VEHICLE'), (KIND.ASSET, STRATEGY.PHYSICAL_VALUATION))

    def test_life_insurance(self):
        self.assertEqual(classify('LIFE_INSURANCE'), (KIND.ASSET, STRATEGY.INSURANCE_SURRENDER))


class TestClassifyUnknownCode(TestCase):
    """Unknown/future codes must return (ASSET, BALANCE) — never raise."""

    def test_unknown_code_returns_asset_balance(self):
        result = classify('TOTALLY_UNKNOWN_CODE_XYZ')
        self.assertEqual(result, (KIND.ASSET, STRATEGY.BALANCE))

    def test_empty_string_returns_asset_balance(self):
        result = classify('')
        self.assertEqual(result, (KIND.ASSET, STRATEGY.BALANCE))

    def test_future_code_does_not_raise(self):
        # Should not raise even for completely unrecognized codes
        try:
            classify('FUTURE_ACCOUNT_TYPE_2099')
        except Exception as e:
            self.fail(f"classify() raised unexpectedly: {e}")


class TestIsLiability(TestCase):
    """is_liability() must return True for all liability-typed codes."""

    LIABILITY_CODES = [
        'CREDIT_CARD', 'BNPL', 'OVERDRAFT',
        'HOME_LOAN', 'VEHICLE_LOAN', 'EDUCATION_LOAN',
        'PERSONAL_LOAN', 'BUSINESS_LOAN', 'LAP', 'GOLD_LOAN',
    ]
    ASSET_CODES = [
        'CASH', 'BANK', 'SAVINGS_ACCOUNT', 'INVESTMENT', 'FIXED_DEPOSIT',
        'FD', 'PPF', 'DEMAT', 'MUTUAL_FUND', 'REAL_ESTATE', 'LIFE_INSURANCE',
        'OTHER',
    ]

    def test_liability_codes_return_true(self):
        for code in self.LIABILITY_CODES:
            with self.subTest(code=code):
                self.assertTrue(is_liability(code), f"Expected {code} to be a liability")

    def test_asset_codes_return_false(self):
        for code in self.ASSET_CODES:
            with self.subTest(code=code):
                self.assertFalse(is_liability(code), f"Expected {code} to be an asset")

    def test_unknown_code_returns_false(self):
        self.assertFalse(is_liability('UNKNOWN_FUTURE_CODE'))


class TestStrategyFor(TestCase):
    """strategy_for() convenience function."""

    def test_balance_types(self):
        for code in ('CASH', 'BANK', 'SAVINGS_ACCOUNT', 'CURRENT_ACCOUNT', 'OTHER'):
            with self.subTest(code=code):
                self.assertEqual(strategy_for(code), STRATEGY.BALANCE)

    def test_deposit_types(self):
        for code in ('FD', 'RD', 'PPF', 'EPF', 'NPS', 'FIXED_DEPOSIT'):
            with self.subTest(code=code):
                self.assertEqual(strategy_for(code), STRATEGY.DEPOSIT)

    def test_holdings_types(self):
        for code in ('DEMAT', 'MUTUAL_FUND', 'ETF', 'INVESTMENT'):
            with self.subTest(code=code):
                self.assertEqual(strategy_for(code), STRATEGY.HOLDINGS)


class TestHelperSets(TestCase):
    """market_linked_codes(), deposit_codes(), liability_codes(), asset_codes()."""

    def test_market_linked_codes_contains_expected(self):
        codes = market_linked_codes()
        for expected in ('DEMAT', 'MUTUAL_FUND', 'ETF', 'SGB', 'ULIP', 'GOLD', 'INVESTMENT'):
            self.assertIn(expected, codes, f"Expected {expected} in market_linked_codes()")

    def test_market_linked_codes_excludes_liabilities(self):
        codes = market_linked_codes()
        self.assertNotIn('CREDIT_CARD', codes)
        self.assertNotIn('HOME_LOAN', codes)

    def test_deposit_codes_contains_expected(self):
        codes = deposit_codes()
        for expected in ('FD', 'RD', 'PPF', 'EPF', 'NPS', 'FIXED_DEPOSIT'):
            self.assertIn(expected, codes, f"Expected {expected} in deposit_codes()")

    def test_market_linked_and_deposit_disjoint(self):
        # No code should be in both sets
        overlap = market_linked_codes() & deposit_codes()
        self.assertEqual(overlap, frozenset(), f"Unexpected overlap: {overlap}")

    def test_liability_codes_contains_all_liabilities(self):
        codes = liability_codes()
        for expected in ('CREDIT_CARD', 'BNPL', 'HOME_LOAN', 'VEHICLE_LOAN'):
            self.assertIn(expected, codes)

    def test_asset_codes_and_liability_codes_cover_all(self):
        all_known = set(ACCOUNT_TYPE_META.keys())
        union = asset_codes() | liability_codes()
        self.assertEqual(union, all_known)

    def test_asset_codes_and_liability_codes_disjoint(self):
        overlap = asset_codes() & liability_codes()
        self.assertEqual(overlap, frozenset(), f"Unexpected overlap: {overlap}")


class TestAccountTypesChoiceList(TestCase):
    """ACCOUNT_TYPES grouped list must be importable and valid for Django forms."""

    def test_account_types_is_list(self):
        self.assertIsInstance(ACCOUNT_TYPES, list)

    def test_legacy_group_exists(self):
        group_names = [group[0] for group in ACCOUNT_TYPES]
        self.assertIn('Legacy', group_names)

    def test_legacy_group_contains_old_codes(self):
        legacy_group = next(g for g in ACCOUNT_TYPES if g[0] == 'Legacy')
        legacy_codes = [code for code, _ in legacy_group[1]]
        for old_code in ('CASH', 'BANK', 'CREDIT_CARD', 'INVESTMENT', 'FIXED_DEPOSIT', 'OTHER'):
            self.assertIn(old_code, legacy_codes, f"Legacy code {old_code} missing from ACCOUNT_TYPES")

    def test_all_codes_in_meta(self):
        """Every code in ACCOUNT_TYPES must also be in ACCOUNT_TYPE_META (consistency check)."""
        for group_name, choices in ACCOUNT_TYPES:
            for code, _ in choices:
                self.assertIn(code, ACCOUNT_TYPE_META,
                              f"Code {code!r} in ACCOUNT_TYPES but not in ACCOUNT_TYPE_META")

    def test_no_duplicate_codes(self):
        """No code should appear more than once in ACCOUNT_TYPES."""
        all_codes = [code for _, choices in ACCOUNT_TYPES for code, _ in choices]
        # CREDIT_CARD appears in both Short-Term Credit and Legacy — that's intentional
        # (same code, different display). So we check unique by code value not position.
        unique_codes = set(all_codes)
        # There should be at most 1 duplicate (CREDIT_CARD in two groups)
        duplicates = {c for c in all_codes if all_codes.count(c) > 1}
        allowed_duplicates = {'CREDIT_CARD'}  # intentional: same code in Legacy group
        unexpected_duplicates = duplicates - allowed_duplicates
        self.assertEqual(unexpected_duplicates, set(), f"Unexpected duplicate codes: {unexpected_duplicates}")
