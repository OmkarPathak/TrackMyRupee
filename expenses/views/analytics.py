"""The Analytics page: Spending, Cashflow and Net worth tabs under the app's unified filter toolbar.

Each tab is its own address (so the toolbar, which keeps the path and rewrites the query, works unchanged) and is
swapped over htmx; only the tab being looked at is computed. A finished tab is kept for a few minutes, per user,
language and filter combination, and any write by the user makes the next visit recompute it.
"""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from ..analytics import cashflow, spending, wealth
from ..analytics.common import Page, cache_key, cache_seconds
from ..filters.definitions import ANALYTICS_FILTERS
from .mixins import HtmxPartialTemplateMixin

BUILDERS = {'spend': spending.build, 'cashflow': cashflow.build, 'networth': wealth.build}
TAB_URLS = {'spend': 'analytics', 'cashflow': 'analytics-cashflow', 'networth': 'analytics-networth'}


def _tab_label(tab: str) -> str:
    return {'spend': _('Spending'), 'cashflow': _('Cashflow'), 'networth': _('Net worth')}[tab]


class AnalyticsView(HtmxPartialTemplateMixin, LoginRequiredMixin, TemplateView):
    template_name = 'expenses/analytics.html'
    htmx_template_name = 'expenses/partials/_analytics_shell.html'
    tab = 'spend'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        profile = getattr(user, 'profile', None)
        context['tab'] = self.tab
        if profile is None or not profile.has_ai_access:
            context['is_locked'] = True
            return context

        page = Page(self.request, self.tab)
        key = cache_key(page) if cache_seconds() else None
        data = cache.get(key) if key else None
        if data is None:
            data = BUILDERS[self.tab](page)
            if key:
                cache.set(key, data, cache_seconds())

        query = self.request.GET.urlencode()
        context.update(data)
        context.update({
            'filter_config': ANALYTICS_FILTERS,
            'applied_state': page.state,
            'tabs': [{'key': t, 'label': _tab_label(t), 'active': t == self.tab,
                      'url': reverse(TAB_URLS[t]) + (f'?{query}' if query else '')} for t in BUILDERS],
            'range_text': page.range_text(*page.period.range),
            'compare_text': page.range_text(*page.period.compare) if page.period.compare else '',
            'compare_name': page.compare_name(),
            'granularity': page.period.granularity,
            'currency_symbol': page.money.symbol,
            'filters_active': page.filters_active,
            'include_capital_events': page.include_capital_events,
            'wrapped_year': page.period.end.year,
            'is_locked': False,
        })
        return context
