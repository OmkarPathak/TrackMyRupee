from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.core.cache import cache
from django.db.models import Q, QuerySet

from .schema import FilterDef, FilterSetConfig


# --- Dynamic Option Providers ---

def get_user_accounts(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    if not user or not user.is_authenticated:
        return []
    from ..models import Account
    qs = Account.objects.filter(user=user).order_by('name')
    if q and q.strip():
        qs = qs.filter(name__icontains=q.strip())
    return [{'value': str(acc.id), 'label': acc.name} for acc in qs]


def get_user_categories(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    if not user or not user.is_authenticated:
        return []
    cache_key = f"filter_categories:{user.id}"
    cats = cache.get(cache_key)
    if cats is None:
        from ..models import Category, Expense
        user_expenses = Expense.objects.filter(user=user)
        raw_used = user_expenses.values_list('category', flat=True).distinct()
        raw_defined = Category.objects.filter(user=user).values_list('name', flat=True)
        
        all_cats = {c.strip() for c in raw_used if c and c.strip()} | {c.strip() for c in raw_defined if c and c.strip()}
        cats = sorted(list(all_cats), key=str.lower)
        cache.set(cache_key, cats, 600)
    
    if q and q.strip():
        query = q.strip().lower()
        cats = [c for c in cats if query in c.lower()]

    return [{'value': c, 'label': c} for c in cats]


def get_user_merchants(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    if not user or not user.is_authenticated:
        return []
    cache_key = f"filter_merchants:{user.id}"
    sorted_merchants = cache.get(cache_key)
    if sorted_merchants is None:
        from ..models import Expense
        qs = Expense.objects.filter(user=user).values_list('description', flat=True).distinct()
        
        merchants = set()
        for desc in qs:
            if not desc:
                continue
            cleaned = desc.split('-')[0].split('(')[0].strip()
            if cleaned:
                first_word = cleaned.split(' ')[0].strip()
                if first_word:
                    merchants.add(first_word)
                merchants.add(cleaned)
                
        sorted_merchants = sorted(list(merchants), key=str.lower)
        cache.set(cache_key, sorted_merchants, 600)

    if q and q.strip():
        query = q.strip().lower()
        sorted_merchants = [m for m in sorted_merchants if query in m.lower()]
        
    return [{'value': m, 'label': m} for m in sorted_merchants]


def get_user_income_sources(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    if not user or not user.is_authenticated:
        return []
    from ..models import Income
    sources = Income.objects.filter(user=user).values_list('source_type', flat=True).distinct()
    clean_sources = sorted([s for s in sources if s], key=str.lower)
    if q and q.strip():
        query = q.strip().lower()
        clean_sources = [s for s in clean_sources if query in s.lower()]
    return [{'value': s, 'label': s} for s in clean_sources]


def get_user_account_types(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    from ..account_types import ACCOUNT_TYPES
    flat_choices = {}
    for group_name, choices in ACCOUNT_TYPES:
        for code, label in choices:
            flat_choices[code] = label

    if user and user.is_authenticated:
        from ..models import Account
        used_codes = set(Account.objects.filter(user=user).values_list('account_type', flat=True).distinct())
        types = [(code, flat_choices.get(code, code)) for code in used_codes if code]
    else:
        types = list(flat_choices.items())

    types.sort(key=lambda x: x[1].lower())
    if q and q.strip():
        query = q.strip().lower()
        types = [t for t in types if query in t[1].lower()]
    return [{'value': code, 'label': label} for code, label in types]


def get_user_recurring_categories(user=None, q: Optional[str] = None) -> List[Dict[str, str]]:
    if not user or not user.is_authenticated:
        return []
    from ..models import RecurringTransaction
    cats = (
        RecurringTransaction.objects.filter(user=user)
        .values_list('category', flat=True)
        .distinct()
    )
    clean_cats = sorted([c for c in cats if c and c.strip()], key=str.lower)
    if q and q.strip():
        query = q.strip().lower()
        clean_cats = [c for c in clean_cats if query in c.lower()]
    return [{'value': c, 'label': c} for c in clean_cats]


# --- Custom Filter Functions ---

def filter_amount_range(queryset: QuerySet, values: List[str]) -> QuerySet:
    if not values:
        return queryset
    
    amount_field = 'base_amount'
    model = getattr(queryset, 'model', None)
    if model:
        try:
            model._meta.get_field('base_amount')
            amount_field = 'base_amount'
        except Exception:
            try:
                model._meta.get_field('converted_amount')
                amount_field = 'converted_amount'
            except Exception:
                amount_field = 'amount'

    q_objects = Q()
    for val in values:
        if val == "Under ₹500":
            q_objects |= Q(**{f"{amount_field}__lt": Decimal('500')})
        elif val == "₹500 to ₹2,000":
            q_objects |= Q(**{f"{amount_field}__gte": Decimal('500'), f"{amount_field}__lte": Decimal('2000')})
        elif val == "₹2,000 to ₹10,000":
            q_objects |= Q(**{f"{amount_field}__gte": Decimal('2000'), f"{amount_field}__lte": Decimal('10000')})
        elif val == "Over ₹10,000":
            q_objects |= Q(**{f"{amount_field}__gt": Decimal('10000')})
            
    return queryset.filter(q_objects)



def filter_recurring(queryset: QuerySet, values: List[str]) -> QuerySet:
    if not values:
        return queryset
    
    val = values[0] if isinstance(values, list) else values
    if val == "Recurring only":
        return queryset.filter(description__icontains='recurring')
    elif val == "One-time only":
        return queryset.exclude(description__icontains='recurring')
    return queryset


def filter_merchant(queryset: QuerySet, values: List[str]) -> QuerySet:
    if not values:
        return queryset
    q_objects = Q()
    for m in values:
        q_objects |= Q(description__icontains=m)
    return queryset.filter(q_objects)


def filter_income_groups(queryset: QuerySet, values: List[str]) -> QuerySet:
    if not values:
        return queryset
    
    group_source_types = []
    if 'EARNED' in values:
        group_source_types.extend(['Salary', 'Freelance / Consulting', 'Business'])
    if 'PASSIVE' in values:
        group_source_types.extend(['Investment Returns', 'Rental Income'])
    if 'ONE_OFF' in values:
        group_source_types.extend(['Cashback & Rewards', 'Refund / Reimbursement', 'Other'])

    if group_source_types:
        return queryset.filter(source_type__in=group_source_types)
    return queryset


# --- Page Configurations ---

EXPENSE_FILTERS = FilterSetConfig(
    page_key="expenses",
    filters=[
        FilterDef(
            key="category",
            label="Category",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_categories,
            field_name="category",
            lookup_expr="in",
        ),
        FilterDef(
            key="payment_method",
            label="Payment Method",
            type="multi_select",
            source="static",
            options=[
                {"value": "Cash", "label": "Cash"},
                {"value": "Credit Card", "label": "Credit Card"},
                {"value": "Debit Card", "label": "Debit Card"},
                {"value": "UPI", "label": "UPI"},
                {"value": "NetBanking", "label": "NetBanking"},
            ],
            field_name="payment_method",
            lookup_expr="in",
        ),
        FilterDef(
            key="account",
            label="Account",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_accounts,
            field_name="account_id",
            lookup_expr="in",
        ),
        FilterDef(
            key="amount_range",
            label="Amount",
            type="single_select",
            source="static",
            options=["Under ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "Over ₹10,000"],
            custom_filter_fn=filter_amount_range,
        ),
        FilterDef(
            key="recurring",
            label="Recurring",
            type="single_select",
            source="static",
            options=["Recurring only", "One-time only"],
            custom_filter_fn=filter_recurring,
        ),
        FilterDef(
            key="status",
            label="Status",
            type="single_select",
            source="static",
            options=["Cleared", "Pending", "Disputed", "Refunded"],
        ),
    ],
    sort_options=[
        {"key": "date_desc", "label": "Date, newest"},
        {"key": "date_asc", "label": "Date, oldest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
    ],
    default_sort="date_desc",
    default_time_range="this_month",
    supports_search=True,
    search_placeholder="Search description...",
)


INCOME_FILTERS = FilterSetConfig(
    page_key="income",
    filters=[
        FilterDef(
            key="source_type",
            label="Source Type",
            type="multi_select",
            source="static",
            options=[
                {"value": "Salary", "label": "Salary"},
                {"value": "Freelance / Consulting", "label": "Freelance / Consulting"},
                {"value": "Business", "label": "Business"},
                {"value": "Investment Returns", "label": "Investment Returns"},
                {"value": "Rental Income", "label": "Rental Income"},
                {"value": "Cashback & Rewards", "label": "Cashback & Rewards"},
                {"value": "Refund / Reimbursement", "label": "Refund / Reimbursement"},
                {"value": "Other", "label": "Other"},
            ],
            field_name="source_type",
            lookup_expr="in",
        ),
        FilterDef(
            key="income_group",
            label="Income Group",
            type="multi_select",
            source="static",
            options=[
                {"value": "EARNED", "label": "Earned (Salary, Freelance, Business)"},
                {"value": "PASSIVE", "label": "Passive (Investment, Rental)"},
                {"value": "ONE_OFF", "label": "One-off (Rewards, Refund, Other)"},
            ],
            custom_filter_fn=filter_income_groups,
        ),
        FilterDef(
            key="account",
            label="Account",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_accounts,
            field_name="account_id",
            lookup_expr="in",
        ),
        FilterDef(
            key="amount_range",
            label="Amount",
            type="single_select",
            source="static",
            options=["Under ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "Over ₹10,000"],
            custom_filter_fn=filter_amount_range,
        ),
    ],
    sort_options=[
        {"key": "date_desc", "label": "Date, newest"},
        {"key": "date_asc", "label": "Date, oldest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
    ],
    default_sort="date_desc",
    default_time_range="this_month",
    supports_search=True,
    search_placeholder="Search description...",
)


ALL_TRANSACTIONS_FILTERS = FilterSetConfig(
    page_key="transactions",
    filters=[
        FilterDef(
            key="type",
            label="Transaction Type",
            type="multi_select",
            source="static",
            options=[
                {"value": "EXPENSE", "label": "Expenses"},
                {"value": "INCOME", "label": "Income"},
                {"value": "TRANSFER", "label": "Transfers"},
                {"value": "LOAN", "label": "Loan Repayments"},
                {"value": "CAPITAL_EVENT", "label": "Capital Events"},
            ],
        ),
        FilterDef(
            key="account",
            label="Account",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_accounts,
            field_name="account_id",
            lookup_expr="in",
        ),
        FilterDef(
            key="amount_range",
            label="Amount",
            type="single_select",
            source="static",
            options=["Under ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "Over ₹10,000"],
        ),
    ],
    sort_options=[
        {"key": "date_desc", "label": "Date, newest"},
        {"key": "date_asc", "label": "Date, oldest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
    ],
    default_sort="date_desc",
    default_time_range="this_month",
    supports_search=True,
    search_placeholder="Search transactions...",
)


ACCOUNT_LIST_FILTERS = FilterSetConfig(
    page_key="accounts",
    filters=[
        FilterDef(
            key="type",
            label="Account Type",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_account_types,
            field_name="account_type",
            lookup_expr="in",
        ),
        FilterDef(
            key="status",
            label="Status",
            type="single_select",
            source="static",
            options=[
                {"value": "active", "label": "Active"},
                {"value": "inactive", "label": "Inactive"},
            ],
        ),
        FilterDef(
            key="pinned",
            label="Pinned",
            type="single_select",
            source="static",
            options=[
                {"value": "pinned", "label": "Pinned only"},
            ],
        ),
    ],
    sort_options=[
        {"key": "balance_desc", "label": "Balance, highest"},
        {"key": "balance_asc", "label": "Balance, lowest"},
        {"key": "name_asc", "label": "Name, A to Z"},
        {"key": "name_desc", "label": "Name, Z to A"},
    ],
    default_sort="balance_desc",
    supports_time_period=False,
    supports_search=True,
    search_placeholder="Search accounts...",
    search_field="name",
)


ACCOUNT_DETAIL_FILTERS = FilterSetConfig(
    page_key="account_detail",
    filters=[
        FilterDef(
            key="tx_type",
            label="Transaction Type",
            type="multi_select",
            source="static",
            options=[
                {"value": "EXPENSE", "label": "Expenses"},
                {"value": "INCOME", "label": "Income"},
                {"value": "TRANSFER", "label": "Transfers"},
                {"value": "SAVINGS", "label": "Savings"},
                {"value": "LOAN", "label": "Loan Repayments"},
                {"value": "CAPITAL", "label": "Capital Events"},
            ],
        ),
        FilterDef(
            key="category",
            label="Category",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_categories,
        ),
        FilterDef(
            key="amount_range",
            label="Amount",
            type="single_select",
            source="static",
            options=["Under ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "Over ₹10,000"],
            custom_filter_fn=filter_amount_range,
        ),
    ],
    sort_options=[
        {"key": "date_desc", "label": "Date, newest"},
        {"key": "date_asc", "label": "Date, oldest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
    ],
    default_sort="date_desc",
    default_time_range="all",
    supports_time_period=True,
    supports_search=True,
    search_placeholder="Search transactions...",
    search_field="description",
)


RECURRING_FILTERS = FilterSetConfig(
    page_key="recurring",
    filters=[
        FilterDef(
            key="category",
            label="Category",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_recurring_categories,
            field_name="category",
            lookup_expr="in",
        ),
        FilterDef(
            key="frequency",
            label="Billing Cycle",
            type="multi_select",
            source="static",
            options=[
                {"value": "DAILY", "label": "Daily"},
                {"value": "WEEKLY", "label": "Weekly"},
                {"value": "BIWEEKLY", "label": "Bi-weekly"},
                {"value": "MONTHLY", "label": "Monthly"},
                {"value": "QUARTERLY", "label": "Quarterly"},
                {"value": "SEMIANNUALLY", "label": "Semi-annually"},
                {"value": "YEARLY", "label": "Yearly"},
            ],
            field_name="frequency",
            lookup_expr="in",
        ),
        FilterDef(
            key="status",
            label="Status",
            type="single_select",
            source="static",
            options=[
                {"value": "active", "label": "Active"},
                {"value": "cancelled", "label": "Cancelled"},
            ],
        ),
        FilterDef(
            key="account",
            label="Account",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_accounts,
        ),
        FilterDef(
            key="transaction_type",
            label="Type",
            type="multi_select",
            source="static",
            options=[
                {"value": "EXPENSE", "label": "Expense"},
                {"value": "TRANSFER", "label": "Transfer"},
                {"value": "INCOME", "label": "Income"},
                {"value": "LOAN", "label": "Loan"},
            ],
            field_name="transaction_type",
            lookup_expr="in",
        ),
    ],
    sort_options=[
        {"key": "next_date_asc", "label": "Renewal, soonest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
        {"key": "name_asc", "label": "Name, A to Z"},
    ],
    default_sort="next_date_asc",
    supports_time_period=False,
    supports_search=True,
    search_placeholder="Search subscriptions...",
    search_field="description",
)


CAPITAL_EVENT_FILTERS = FilterSetConfig(
    page_key="capital_events",
    filters=[
        FilterDef(
            key="subtype",
            label="Event Type",
            type="multi_select",
            source="static",
            options=[
                {"value": "loan_down_payment", "label": "Loan Down Payment"},
                {"value": "loan_prepayment", "label": "Loan Prepayment"},
                {"value": "large_purchase", "label": "Large Purchase"},
                {"value": "medical_lump_sum", "label": "Medical Lump Sum"},
                {"value": "gift_given", "label": "Gift Given"},
                {"value": "gift_received", "label": "Gift Received"},
                {"value": "investment_lump_sum", "label": "Investment Lump Sum"},
                {"value": "other", "label": "Other"},
            ],
            field_name="subtype",
            lookup_expr="in",
        ),
        FilterDef(
            key="account",
            label="Account",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_accounts,
            field_name="account_id",
            lookup_expr="in",
        ),
        FilterDef(
            key="amount_range",
            label="Amount",
            type="single_select",
            source="static",
            options=["Under ₹500", "₹500 to ₹2,000", "₹2,000 to ₹10,000", "Over ₹10,000"],
            custom_filter_fn=filter_amount_range,
        ),
    ],
    sort_options=[
        {"key": "date_desc", "label": "Date, newest"},
        {"key": "date_asc", "label": "Date, oldest"},
        {"key": "amount_desc", "label": "Amount, highest"},
        {"key": "amount_asc", "label": "Amount, lowest"},
    ],
    default_sort="date_desc",
    default_time_range="this_month",
    supports_time_period=True,
    supports_search=True,
    search_placeholder="Search capital events...",
    search_field="note",
)


DASHBOARD_FILTERS = FilterSetConfig(
    page_key="dashboard",
    filters=[
        FilterDef(
            key="category",
            label="Category",
            type="multi_select",
            source="dynamic",
            options_fn=get_user_categories,
            field_name="category",
            lookup_expr="in",
        ),
    ],
    sort_options=[],
    default_sort="",
    default_time_range="this_month",
    supports_time_period=True,
    supports_search=False,
    supports_sort=False,
    external_chip_row=True,
)

