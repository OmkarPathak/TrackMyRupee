"""Where a click on an Analytics chart, tile or row goes: All Transactions, with the same period and filters.

Every link names its dates explicitly (``time_period=custom``), so the list always shows exactly the days the
chart bar or tile covered, whatever the toolbar of the other page defaults to.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date
from urllib.parse import urlencode

from django.urls import reverse

EXPENSE, INCOME, TRANSFER, LOAN, CAPITAL_EVENT = 'EXPENSE', 'INCOME', 'TRANSFER', 'LOAN', 'CAPITAL_EVENT'
SPENDING_TYPES = (EXPENSE, LOAN, CAPITAL_EVENT)


class LinkBuilder:
    """Builds All Transactions URLs that carry the toolbar's category and account filters along."""

    def __init__(self, categories: Sequence[str] = (), account_ids: Sequence[int] = ()):
        self.categories = list(categories)
        self.account_ids = list(account_ids)

    def transactions(self, start: date, end: date, types: Iterable[str] = (), category: str | None = None,
                     accounts: Sequence[int] | None = None) -> str:
        types = list(types)
        params = [('type', t) for t in types]
        # a category only exists on expenses and capital events, so it is dropped from links to other kinds of row
        has_category = not types or any(t in (EXPENSE, CAPITAL_EVENT) for t in types)
        if has_category:
            names = [category] if category else self.categories
            params += [('category', c) for c in names]
        for account_id in (self.account_ids if accounts is None else accounts):
            params.append(('account', str(account_id)))
        params += [('time_period', 'custom'), ('start_date', start.isoformat()), ('end_date', end.isoformat())]
        return reverse('all-transactions') + '?' + urlencode(params)

    @staticmethod
    def accounts(account_types: Iterable[str] = ()) -> str:
        """The Accounts page narrowed to some account types."""
        url = reverse('account-list')
        params = [('type', t) for t in account_types]
        return f'{url}?{urlencode(params)}' if params else url
