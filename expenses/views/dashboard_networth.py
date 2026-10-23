"""The Dashboard's Net Worth card and Asset Allocation card, worked out apart from the rest of the page.

Net worth is the most expensive thing on the Dashboard (the ledger read, loans, valuations) and it does not depend on
the date filter, so it can be computed on its own. `net_worth_card` does the work; `dashboard_networth` serves it as a
fragment that the page asks for after it has painted. When the figures are already cached the page includes them
directly and does not ask.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.views.decorators.http import require_GET

from ..account_types import is_liability
from ..ledger_read_service import LedgerReadService
from ..models import Account
from ..savings import monthly_parts, savings_for_period
from ..services import FinancialService, LoanService

# What the page's context holds for a Net Worth card that is already worked out (tests and templates read these)
NET_WORTH_KEYS = ('net_worth', 'net_worth_change', 'net_worth_percent', 'net_worth_trend', 'net_worth_labels',
                  'forecast_index_start', 'asset_allocation', 'net_worth_before_liabilities')

NETWORTH_CACHE_SECONDS = 4 * 3600      # like the page's own cache; any change to a transaction or account retires it


def networth_cache_key(user_id) -> str:
    return f'dashboard_networth_{user_id}'


def monthly_summary_map(user, is_testing: bool = False) -> dict:
    """{(year, month): {'income', 'expense', 'expense_base', 'loan_interest'}} for the last two years.
    'expense' is the combined total (expenses + loan interest + capital events counted in averages); the other two are
    kept apart so year-to-date and historical averages can be derived without more queries."""
    cache_key = f'monthly_summary_map_{user.id}' if not is_testing else None
    result = cache.get(cache_key) if cache_key else None
    if result is None:
        hist_start = (timezone.now().replace(day=1) - timedelta(days=730)).date()
        result = {}
        for key, part in monthly_parts(user, hist_start).items():
            expense_base, interest = float(part['expenses']), float(part['interest'])
            result[key] = {
                'income': float(part['income']),
                'expense': expense_base + interest + float(part['capital']),
                'expense_base': expense_base,
                'loan_interest': interest,
            }
        if cache_key:
            cache.set(cache_key, result, 300)
    return result


def net_worth_card(user, *, currency_symbol, accounts=None, ledger_result=None, month_savings=None, summary_map=None) -> dict:
    """Everything the two cards show. Callers that already hold part of it (the page, in its legacy form) pass it in."""
    now = timezone.localtime(timezone.now())
    ledger_result = ledger_result or LedgerReadService.get_net_worth(user)
    net_worth, account_base_balances = ledger_result
    # get_net_worth has already subtracted liabilities; this is the figure before that
    net_worth_before_liabilities = net_worth + Decimal(str(LoanService.get_total_liabilities(user)))
    if accounts is None:
        accounts = list(Account.objects.filter(user=user, is_active=True))
    if month_savings is None:
        month_savings = savings_for_period(user, timezone.now().replace(day=1).date(), timezone.localdate())
    if summary_map is None:
        summary_map = monthly_summary_map(user)

    # Net worth grows by what you save: income minus spending, where repaying loan principal is not spending
    # (cash becomes less debt) and capital events count per their own switches.
    net_worth_change = month_savings.savings
    start_net_worth = net_worth - net_worth_change
    net_worth_percent = Decimal('0.00')
    if start_net_worth > 0:
        net_worth_percent = (net_worth_change / start_net_worth * 100).quantize(Decimal('0.1'))
    elif start_net_worth == 0 and net_worth_change > 0:
        net_worth_percent = Decimal('100.0')

    net_worth_trend = FinancialService.get_cumulative_net_worth_history(user, net_worth, 6)
    history = FinancialService.get_monthly_history(user, 6)
    net_worth_labels = [date_format(m['month'], 'M Y') for m in history]

    # Allocation by account-type group, from the converted balances
    code_to_group = {}
    for group_name, choices in Account.ACCOUNT_TYPES:
        for val, _label in choices:
            code_to_group.setdefault(val, group_name)
    group_totals = defaultdict(Decimal)
    liability_pks = set()
    for acc in accounts:
        if is_liability(acc.account_type):
            # Cards and loans are what you owe, not what you hold, so they never belong in the allocation.
            liability_pks.add(acc.pk)
            continue
        group_totals[code_to_group.get(acc.account_type, 'Other')] += account_base_balances[acc.pk]

    asset_allocation = []
    cumulative_percent = 0
    circumference = 2 * 3.14159 * 45
    # Positive balances only, so the donut never shows more than 100% or a negative segment
    total_assets = sum(float(v) for pk, v in account_base_balances.items() if v > 0 and pk not in liability_pks)
    for group_name, total in sorted(group_totals.items(), key=lambda x: x[1], reverse=True):
        if total <= 0:
            continue
        percent = round((float(total) / float(total_assets) * 100), 1) if total_assets > 0 else 0
        asset_allocation.append({
            'type_key': re.sub(r'[^A-Z0-9_]', '_', group_name.upper()),
            'type': group_name,
            'total': float(total),
            'percent': percent,
            'arc_length': (percent / 100) * circumference,
            'offset_length': (cumulative_percent / 100) * circumference,
        })
        cumulative_percent += percent

    # The sparkline: six months of history, then three projected months at the recent average saving
    hist_y, hist_m, hist_months = now.year, now.month, []
    for _ in range(3):
        hist_m -= 1
        if hist_m == 0:
            hist_m, hist_y = 12, hist_y - 1
        hist_months.append((hist_y, hist_m))
    hist_income = sum(summary_map.get(k, {}).get('income', 0.0) for k in hist_months)
    hist_expense = sum(summary_map.get(k, {}).get('expense_base', 0.0) + summary_map.get(k, {}).get('loan_interest', 0.0)
                       for k in hist_months)
    avg_monthly_savings = Decimal(str((hist_income - hist_expense) / 3))
    forecast_index_start = len(net_worth_trend)
    for i in range(1, 4):
        f_month, f_year = now.month + i, now.year
        while f_month > 12:
            f_month -= 12
            f_year += 1
        net_worth_labels.append(date_format(date(f_year, f_month, 1), 'M Y'))
        net_worth_trend.append(float(net_worth + avg_monthly_savings * i))

    return {
        'net_worth': net_worth,
        'net_worth_change': net_worth_change,
        'net_worth_percent': net_worth_percent,
        'net_worth_trend': net_worth_trend,
        'net_worth_labels': net_worth_labels,
        'forecast_index_start': forecast_index_start,
        'net_worth_before_liabilities': net_worth_before_liabilities,
        'asset_allocation': asset_allocation,
        'currency_symbol': currency_symbol,
        'language': translation.get_language(),
    }


def cached_net_worth_card(user_id):
    """The cached figures for the language being served, or None."""
    nw = cache.get(networth_cache_key(user_id))
    return nw if nw and nw.get('language') == translation.get_language() else None


@login_required
@require_GET
def dashboard_networth(request):
    """The Net Worth figures and the Asset Allocation body, fetched by the Dashboard after it has painted."""
    user = request.user
    nw = cached_net_worth_card(user.pk)
    if nw is None:
        profile = getattr(user, 'profile', None)
        nw = net_worth_card(user, currency_symbol=profile.currency if profile else '₹')
        cache.set(networth_cache_key(user.pk), nw, NETWORTH_CACHE_SECONDS)
    # No request: the page's context processors (badges, announcements) would cost queries this fragment never uses
    html = render_to_string('expenses/partials/_dashboard_networth_fragment.html',
                            {'nw': nw, 'currency_symbol': nw['currency_symbol']})
    response = HttpResponse(html)
    response['Cache-Control'] = 'private, no-store'
    return response
