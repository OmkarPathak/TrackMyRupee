from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from ..filters.definitions import (
    ACCOUNT_DETAIL_FILTERS,
    ACCOUNT_LIST_FILTERS,
    ALL_TRANSACTIONS_FILTERS,
    CAPITAL_EVENT_FILTERS,
    DASHBOARD_FILTERS,
    EXPENSE_FILTERS,
    INCOME_FILTERS,
    RECURRING_FILTERS,
)

CONFIG_MAP = {
    'expenses': EXPENSE_FILTERS,
    'income': INCOME_FILTERS,
    'transactions': ALL_TRANSACTIONS_FILTERS,
    'accounts': ACCOUNT_LIST_FILTERS,
    'account_detail': ACCOUNT_DETAIL_FILTERS,
    'recurring': RECURRING_FILTERS,
    'capital_events': CAPITAL_EVENT_FILTERS,
    'dashboard': DASHBOARD_FILTERS,
}


class FilterOptionsView(LoginRequiredMixin, View):
    """
    Lightweight JSON API returning options for dynamic or searchable filters.
    Supports ?page=expenses&filter=account&q=ICICI
    """

    def get(self, request, *args, **kwargs):
        page_key = request.GET.get('page')
        filter_key = request.GET.get('filter')
        query = request.GET.get('q', '')

        if not page_key or page_key not in CONFIG_MAP:
            return JsonResponse({'error': 'Invalid or missing page parameter'}, status=400)

        config = CONFIG_MAP[page_key]
        filter_def = config.get_filter(filter_key)
        if not filter_def:
            return JsonResponse({'error': f"Filter '{filter_key}' not found on page '{page_key}'"}, status=404)

        options = filter_def.get_options_list(user=request.user, q=query)
        return JsonResponse({
            'page': page_key,
            'filter': filter_key,
            'searchable': filter_def.is_searchable(),
            'options': options,
        })
