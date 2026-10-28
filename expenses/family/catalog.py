"""What kind of thing an account is, in the words the family pages use. Pure functions, no queries."""
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _lazy

from ..account_types import STRATEGY, strategy_for

BANK, CARD, INSURANCE, LOAN, PROPERTY, INVESTMENT = 'bank', 'card', 'insurance', 'loan', 'property', 'investment'

_KIND_BY_STRATEGY = {
    STRATEGY.BALANCE: BANK,
    STRATEGY.DEPOSIT: BANK,
    STRATEGY.REVOLVING_CREDIT: CARD,
    STRATEGY.LOAN_OUTSTANDING: LOAN,
    STRATEGY.INSURANCE_SURRENDER: INSURANCE,
    STRATEGY.PHYSICAL_VALUATION: PROPERTY,
    STRATEGY.HOLDINGS: INVESTMENT,
}

# The headings of the "What I share" page and the family lists: several kinds can share a heading
SECTIONS = [
    ('bank', _lazy("Bank and cash"), (BANK,)),
    ('card', _lazy("Credit cards"), (CARD,)),
    ('insurance', _lazy("Insurance"), (INSURANCE,)),
    ('loan_property', _lazy("Loans and property"), (LOAN, PROPERTY)),
    ('investment', _lazy("Investments"), (INVESTMENT,)),
]


def kind_of(account_type):
    return _KIND_BY_STRATEGY.get(strategy_for(account_type), BANK)


def section_of(kind):
    for key, _label, kinds in SECTIONS:
        if kind in kinds:
            return key
    return SECTIONS[0][0]


# Short names for the family pages (the long names on the account form do not fit a phone-width row)
TYPE_LABELS = {
    'CASH_WALLET': _lazy("Cash wallet"), 'SAVINGS_ACCOUNT': _lazy("Savings account"),
    'SALARY_ACCOUNT': _lazy("Salary account"), 'CURRENT_ACCOUNT': _lazy("Current account"),
    'DIGITAL_WALLET': _lazy("Digital wallet"), 'FD': _lazy("Fixed deposit"), 'RD': _lazy("Recurring deposit"),
    'PPF': _lazy("Public Provident Fund"), 'EPF': _lazy("Employees' Provident Fund"),
    'VPF': _lazy("Voluntary Provident Fund"), 'NPS': _lazy("National Pension System"),
    'SSY': _lazy("Sukanya Samriddhi"), 'POST_OFFICE': _lazy("Post Office savings"), 'APY': _lazy("Atal Pension Yojana"),
    'DEMAT': _lazy("Demat account"), 'MUTUAL_FUND': _lazy("Mutual funds"), 'ETF': _lazy("ETFs"),
    'SGB': _lazy("Sovereign Gold Bonds"), 'ULIP': _lazy("ULIP"), 'GOLD': _lazy("Gold"),
    'CREDIT_CARD': _lazy("Credit card"), 'BNPL': _lazy("Buy now pay later"), 'OVERDRAFT': _lazy("Overdraft"),
    'HOME_LOAN': _lazy("Home loan"), 'VEHICLE_LOAN': _lazy("Vehicle loan"), 'EDUCATION_LOAN': _lazy("Education loan"),
    'PERSONAL_LOAN': _lazy("Personal loan"), 'BUSINESS_LOAN': _lazy("Business loan"),
    'LAP': _lazy("Loan against property"), 'GOLD_LOAN': _lazy("Gold loan"),
    'REAL_ESTATE': _lazy("Property"), 'VEHICLE': _lazy("Vehicle"),
    'LIFE_INSURANCE': _lazy("Life insurance"), 'HEALTH_INSURANCE': _lazy("Health insurance"),
    'CASH': _lazy("Cash"), 'BANK': _lazy("Bank account"), 'INVESTMENT': _lazy("Investment"),
    'FIXED_DEPOSIT': _lazy("Fixed deposit"), 'OTHER': _lazy("Other"),
}


def type_label(account):
    """The account's type in words (translated), e.g. "Savings account"."""
    label = TYPE_LABELS.get(account.account_type)
    return str(label) if label is not None else _("Account")


# How the family's assets are grouped in "What the family owns"
CASH, FIXED_INCOME, INVESTMENTS, PROPERTY_ASSETS, INSURANCE_PLANS = 'cash', 'fixed_income', 'investments', 'property', 'insurance'
ASSET_CLASSES = [
    (PROPERTY_ASSETS, _lazy("Property and vehicles")),
    (FIXED_INCOME, _lazy("Fixed income")),
    (INVESTMENTS, _lazy("Investments")),
    (CASH, _lazy("Cash and bank")),
    (INSURANCE_PLANS, _lazy("Insurance savings plans")),
]

_CLASS_BY_STRATEGY = {
    STRATEGY.BALANCE: CASH,
    STRATEGY.DEPOSIT: FIXED_INCOME,
    STRATEGY.HOLDINGS: INVESTMENTS,
    STRATEGY.PHYSICAL_VALUATION: PROPERTY_ASSETS,
    STRATEGY.INSURANCE_SURRENDER: INSURANCE_PLANS,
}


def asset_class_of(account_type):
    """Which "What the family owns" group an asset belongs to ('' for what is owed)."""
    return _CLASS_BY_STRATEGY.get(strategy_for(account_type), '')


# The rows of the "Who holds what" table, in order: (key, heading, the kinds counted in it)
HOLDINGS_ROWS = [
    ('bank', _lazy("Bank accounts and deposits"), (BANK,)),
    ('card', _lazy("Credit cards"), (CARD,)),
    ('insurance', _lazy("Insurance policies"), (INSURANCE,)),
    ('loan', _lazy("Loans"), (LOAN,)),
    ('property', _lazy("Property"), (PROPERTY,)),
    ('investment', _lazy("Investments"), (INVESTMENT,)),
]
