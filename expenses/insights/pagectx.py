"""The context an inline rule runs in: one Analytics page request, not a budget cycle.

Inline rules (mode="inline") are the sentences on the Analytics tabs. They are computed per request from the same
Page the tab already built (its period, comparison, filters and loaded facts), never stored, and they read it
through two declared loaders: `page` and `category_cycles`.
"""
from __future__ import annotations

from ..analytics.facts import load_category_cycles
from .context import InsightContext

CYCLES = 3                      # complete budget months a category must have risen through


class PageContext(InsightContext):
    def __init__(self, page, **values):
        super().__init__(page.user, today=page.today)
        self._page, self.values = page, values
        self.links = page.links                       # carries the toolbar's category and account filters

    def _load_page(self):
        return self._page

    def _load_category_cycles(self):
        ranges = [self.cycle_of(key) for key in reversed(self.keys_back(CYCLES))]
        page = self._page
        return ranges, load_category_cycles(page.user, ranges, categories=page.categories, account_ids=page.account_ids)
