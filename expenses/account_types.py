"""
expenses/account_types.py
=========================
Single source of truth for account type classification and valuation strategy.

This is a pure, DB-free module — it imports nothing from Django models.
All other modules (ledger_read_service, utils, forms) import from here.

Design principles:
- Unknown / future codes → (KIND.ASSET, STRATEGY.BALANCE): never raises, never crashes
- Liabilities carry negative ledger balances (existing convention, unchanged)
- Classification is a read-side / display / valuation concern only
- DO NOT change the ledger sign convention or ASSET hardcoding in _get_or_create_account_ledger
"""

from __future__ import annotations

from enum import Enum


class KIND(str, Enum):
    """Whether an account contributes positively (ASSET) or negatively (LIABILITY) to net worth."""
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"


class STRATEGY(str, Enum):
    """
    Valuation strategy — how current_value is computed for this account type.

    BALANCE            — ledger-derived balance (cash/bank accounts, fallback for all)
    HOLDINGS           — Σ latest Valuation.value per active Holding
    DEPOSIT            — ledger balance; optionally principal + accrued_interest if deposit_* fields set
    REVOLVING_CREDIT   — ledger balance (already negative when owed)
    LOAN_OUTSTANDING   — outstanding principal from linked Loan or ledger fallback
    PHYSICAL_VALUATION — latest AssetValuation.value of linked PhysicalAsset
    INSURANCE_SURRENDER— latest surrender value = latest AssetValuation.value of linked PhysicalAsset
    """
    BALANCE = "BALANCE"
    HOLDINGS = "HOLDINGS"
    DEPOSIT = "DEPOSIT"
    REVOLVING_CREDIT = "REVOLVING_CREDIT"
    LOAN_OUTSTANDING = "LOAN_OUTSTANDING"
    PHYSICAL_VALUATION = "PHYSICAL_VALUATION"
    INSURANCE_SURRENDER = "INSURANCE_SURRENDER"


# ---------------------------------------------------------------------------
# ACCOUNT_TYPE_META
# ---------------------------------------------------------------------------
# Maps every account_type code (new + legacy) to (KIND, STRATEGY).
# Authoritative table — update here when adding new codes.
# ---------------------------------------------------------------------------
ACCOUNT_TYPE_META: dict[str, tuple[KIND, STRATEGY]] = {
    # ── Liquid & Cash Assets ─────────────────────────────────────────────────
    "CASH_WALLET":    (KIND.ASSET, STRATEGY.BALANCE),
    "SAVINGS_ACCOUNT": (KIND.ASSET, STRATEGY.BALANCE),
    "SALARY_ACCOUNT": (KIND.ASSET, STRATEGY.BALANCE),
    "CURRENT_ACCOUNT": (KIND.ASSET, STRATEGY.BALANCE),
    "DIGITAL_WALLET": (KIND.ASSET, STRATEGY.BALANCE),

    # ── Fixed-Income & Govt Schemes (Assets) ─────────────────────────────────
    "FD":          (KIND.ASSET, STRATEGY.DEPOSIT),
    "RD":          (KIND.ASSET, STRATEGY.DEPOSIT),
    "PPF":         (KIND.ASSET, STRATEGY.DEPOSIT),
    "EPF":         (KIND.ASSET, STRATEGY.DEPOSIT),
    "VPF":         (KIND.ASSET, STRATEGY.DEPOSIT),
    "NPS":         (KIND.ASSET, STRATEGY.DEPOSIT),
    "SSY":         (KIND.ASSET, STRATEGY.DEPOSIT),
    "POST_OFFICE": (KIND.ASSET, STRATEGY.DEPOSIT),
    "APY":         (KIND.ASSET, STRATEGY.DEPOSIT),

    # ── Market-Linked Investments (Assets) ───────────────────────────────────
    "DEMAT":       (KIND.ASSET, STRATEGY.HOLDINGS),
    "MUTUAL_FUND": (KIND.ASSET, STRATEGY.HOLDINGS),
    "ETF":         (KIND.ASSET, STRATEGY.HOLDINGS),
    "SGB":         (KIND.ASSET, STRATEGY.HOLDINGS),
    "ULIP":        (KIND.ASSET, STRATEGY.HOLDINGS),
    "GOLD":        (KIND.ASSET, STRATEGY.HOLDINGS),

    # ── Short-Term Credit (Liabilities) ──────────────────────────────────────
    "CREDIT_CARD": (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT),
    "BNPL":        (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT),
    "OVERDRAFT":   (KIND.LIABILITY, STRATEGY.REVOLVING_CREDIT),

    # ── Long-Term Loans (Liabilities) ────────────────────────────────────────
    "HOME_LOAN":      (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "VEHICLE_LOAN":   (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "EDUCATION_LOAN": (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "PERSONAL_LOAN":  (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "BUSINESS_LOAN":  (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "LAP":            (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),
    "GOLD_LOAN":      (KIND.LIABILITY, STRATEGY.LOAN_OUTSTANDING),

    # ── Physical & Illiquid Assets ───────────────────────────────────────────
    "REAL_ESTATE":    (KIND.ASSET, STRATEGY.PHYSICAL_VALUATION),
    "VEHICLE":        (KIND.ASSET, STRATEGY.PHYSICAL_VALUATION),
    "LIFE_INSURANCE": (KIND.ASSET, STRATEGY.INSURANCE_SURRENDER),

    # ── Legacy codes — retained for backward compatibility ───────────────────
    # CREDIT_CARD is unchanged (same code in old and new list above)
    "CASH":         (KIND.ASSET, STRATEGY.BALANCE),
    "BANK":         (KIND.ASSET, STRATEGY.BALANCE),
    "INVESTMENT":   (KIND.ASSET, STRATEGY.HOLDINGS),   # preserve INVESTMENT→HOLDINGS
    "FIXED_DEPOSIT": (KIND.ASSET, STRATEGY.DEPOSIT),
    "OTHER":        (KIND.ASSET, STRATEGY.BALANCE),
}

# Default for unknown / future codes — never raise
_DEFAULT_KIND = KIND.ASSET
_DEFAULT_STRATEGY = STRATEGY.BALANCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify(code: str) -> tuple[KIND, STRATEGY]:
    """
    Return (KIND, STRATEGY) for the given account_type code.

    Unknown or future codes return (KIND.ASSET, STRATEGY.BALANCE) — never raise.
    """
    return ACCOUNT_TYPE_META.get(code, (_DEFAULT_KIND, _DEFAULT_STRATEGY))


def is_liability(code: str) -> bool:
    """Return True if the account type is a liability (contributes negatively to net worth)."""
    kind, _ = classify(code)
    return kind == KIND.LIABILITY


def strategy_for(code: str) -> STRATEGY:
    """Return the valuation STRATEGY for the given account_type code."""
    _, strategy = classify(code)
    return strategy


def market_linked_codes() -> frozenset[str]:
    """
    Return the set of account_type codes that use the HOLDINGS strategy
    (market-linked investments including legacy INVESTMENT code).

    Use this in utils.py savings-rate logic instead of hardcoded lists.
    """
    return frozenset(
        code for code, (kind, strategy) in ACCOUNT_TYPE_META.items()
        if strategy == STRATEGY.HOLDINGS
    )


def deposit_codes() -> frozenset[str]:
    """
    Return the set of account_type codes that use the DEPOSIT strategy
    (fixed-income / government schemes including legacy FIXED_DEPOSIT code).

    Use this in utils.py savings-rate logic instead of hardcoded lists.
    """
    return frozenset(
        code for code, (kind, strategy) in ACCOUNT_TYPE_META.items()
        if strategy == STRATEGY.DEPOSIT
    )


def investment_codes() -> frozenset[str]:
    """
    Return all account_type codes classified as market-linked investments or fixed deposits/schemes.
    Used for filtering transfers to investment/wealth-growth accounts.
    """
    return market_linked_codes().union(deposit_codes())


def liability_codes() -> frozenset[str]:
    """Return all account_type codes classified as liabilities."""
    return frozenset(
        code for code, (kind, _) in ACCOUNT_TYPE_META.items()
        if kind == KIND.LIABILITY
    )


def asset_codes() -> frozenset[str]:
    """Return all account_type codes classified as assets."""
    return frozenset(
        code for code, (kind, _) in ACCOUNT_TYPE_META.items()
        if kind == KIND.ASSET
    )


# ---------------------------------------------------------------------------
# The grouped ACCOUNT_TYPES list used in Account.ACCOUNT_TYPES
# Defined here so it can be imported by models.py and kept in sync with the
# ACCOUNT_TYPE_META mapping above.
# ---------------------------------------------------------------------------
ACCOUNT_TYPES = [
    ('Cash & Bank', (
        ('CASH_WALLET', 'Cash Wallet'),
        ('SAVINGS_ACCOUNT', 'Savings Account'),
        ('SALARY_ACCOUNT', 'Salary Account'),
        ('CURRENT_ACCOUNT', 'Current Account'),
        ('DIGITAL_WALLET', 'Digital Wallet (Paytm, Amazon Pay, etc.)'),
    )),
    ('Fixed-Income', (
        ('FD', 'Fixed Deposit (FD)'),
        ('RD', 'Recurring Deposit (RD)'),
        ('PPF', 'Public Provident Fund (PPF)'),
        ('EPF', "Employees' Provident Fund (EPF)"),
        ('VPF', 'Voluntary Provident Fund (VPF)'),
        ('NPS', 'National Pension System (NPS)'),
        ('SSY', 'Sukanya Samriddhi Yojana (SSY)'),
        ('POST_OFFICE', 'Post Office Savings (SCSS, NSC, KVP)'),
        ('APY', 'Atal Pension Yojana (APY)'),
    )),
    ('Investments', (
        ('DEMAT', 'Demat Account (Direct Equity)'),
        ('MUTUAL_FUND', 'Mutual Funds'),
        ('ETF', 'Exchange Traded Funds (ETF)'),
        ('SGB', 'Sovereign Gold Bonds (SGB)'),
        ('ULIP', 'Unit Linked Insurance Plan (ULIP)'),
        ('GOLD', 'Digital / Physical Gold'),
    )),
    ('Short-Term Credit', (
        ('CREDIT_CARD', 'Credit Card'),
        ('BNPL', 'Buy Now Pay Later (BNPL)'),
        ('OVERDRAFT', 'Overdraft (OD) / Cash Credit (CC)'),
    )),
    ('Long-Term Loans', (
        ('HOME_LOAN', 'Home Loan'),
        ('VEHICLE_LOAN', 'Vehicle Loan'),
        ('EDUCATION_LOAN', 'Education Loan'),
        ('PERSONAL_LOAN', 'Personal Loan'),
        ('BUSINESS_LOAN', 'Business Loan'),
        ('LAP', 'Loan Against Property (LAP)'),
        ('GOLD_LOAN', 'Gold Loan'),
    )),
    ('Physical Assets', (
        ('REAL_ESTATE', 'Real Estate'),
        ('VEHICLE', 'Vehicle'),
        ('LIFE_INSURANCE', 'Traditional Life Insurance (Surrender Value)'),
    )),
    # Legacy codes retained for backward compatibility — keep VALID, do not delete.
    ('Legacy', (
        ('CASH', 'Cash (legacy)'),
        ('BANK', 'Bank Account (legacy)'),
        ('CREDIT_CARD', 'Credit Card (legacy)'),
        ('INVESTMENT', 'Investment (legacy)'),
        ('FIXED_DEPOSIT', 'Fixed Deposit (legacy)'),
        ('OTHER', 'Other (legacy)'),
    )),
]


# ---------------------------------------------------------------------------
# FIELD_GROUPS_BY_STRATEGY (SPEC §8, point 1)
# Single source of truth for form field visibility per strategy and account type.
# ---------------------------------------------------------------------------
FIELD_GROUPS_BY_STRATEGY: dict[STRATEGY, list[str]] = {
    STRATEGY.BALANCE: [],
    STRATEGY.DEPOSIT: [
        'deposit_principal',
        'deposit_rate',
        'deposit_start_date',
        'deposit_compounding',
        'deposit_maturity_date',
        'deposit_closed_date',
        'show_accrued_balance',
    ],
    STRATEGY.HOLDINGS: [],
    STRATEGY.REVOLVING_CREDIT: ['credit_limit'],
    STRATEGY.LOAN_OUTSTANDING: ['linked_loan'],
    STRATEGY.PHYSICAL_VALUATION: [
        'linked_physical_asset',
        'create_new_asset',
        'asset_name',
        'acquisition_cost',
        'acquisition_date',
    ],
    STRATEGY.INSURANCE_SURRENDER: [
        'linked_physical_asset',
        'create_new_asset',
        'asset_name',
        'policy_number',
        'premium_amount',
        'premium_frequency',
        'policy_start_date',
        'sum_assured',
    ],
}

ACCOUNT_TYPE_EXTRA_FIELDS: dict[str, list[str]] = {
    'RD': ['rd_installment_amount', 'rd_installment_day'],
}


def get_fields_for_account_type(code: str) -> list[str]:
    """
    Return the list of strategy-specific field names relevant for the given account_type code.
    """
    strategy = strategy_for(code)
    base_fields = list(FIELD_GROUPS_BY_STRATEGY.get(strategy, []))
    extra_fields = ACCOUNT_TYPE_EXTRA_FIELDS.get(code, [])
    return base_fields + extra_fields

