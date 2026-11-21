"""Which page of the user guide (docs.trackmyrupee.com) explains the page the person is on.

The help button in the top bar and the phone's More menu open the guide for the current page in a side panel
(templates/components/help_panel.html). `help_page` below is what they open: a docs path and a title. A page with no
entry of its own opens the guide's front page.
"""
from __future__ import annotations

from django.conf import settings
from django.utils.translation import gettext_lazy as _

# url name -> (docs path, title). A name not listed falls back to the first matching prefix in PREFIXES.
EXACT = {
    'home': ('15-dashboard/', _('Dashboard')),
    'dashboard-networth': ('02-accounts/', _('Accounts & Net Worth')),
    'budget': ('07-budgets/', _('Budgets')),
    'upload': ('03-transactions-expenses/importing/', _('Importing a statement')),
    'upload-preview': ('03-transactions-expenses/importing/', _('Importing a statement')),
    'import-history': ('03-transactions-expenses/importing/', _('Importing a statement')),
    'import-report': ('03-transactions-expenses/importing/', _('Importing a statement')),
    'all-transactions': ('14-filters-and-search/', _('Search and filters')),
    'calendar': ('10-calendar/', _('Calendar')),
    'pricing': ('23-plans-and-billing/', _('Plans and billing')),
    'payment-history': ('23-plans-and-billing/', _('Plans and billing')),
    'notification-list': ('16-notifications/', _('Notifications')),
    'insights-settings': ('18-insights/', _('Insights')),
    'settings-home': ('01-getting-started/', _('Getting started')),
    'contact': ('22-faq/', _('Frequently asked questions')),
}

PREFIXES = (
    ('expense', ('03-transactions-expenses/', _('Adding expenses'))),
    ('income', ('04-transactions-income/', _('Adding income'))),
    ('recurring', ('05-transactions-recurring/', _('Recurring and subscriptions'))),
    ('transfer', ('06-transfers/', _('Transfers'))),
    ('budget', ('07-budgets/', _('Budgets'))),
    ('loan', ('08-loans/', _('Loans'))),
    ('pay-emi', ('08-loans/', _('Loans'))),
    ('capital-event', ('09-capital-events/', _('Big purchases'))),
    ('holding', ('02-accounts/holdings/', _('Holdings and mutual funds'))),
    ('account', ('02-accounts/', _('Accounts & Net Worth'))),
    ('analytics', ('11-analytics-and-health/', _('Analytics'))),
    ('year_in_review', ('11-analytics-and-health/', _('Analytics'))),
    ('goal', ('12-goals/', _('Savings goals'))),
    ('tmr-flows', ('13-tmr-flows/', _('Guided Setup'))),
    ('flow-', ('13-tmr-flows/', _('Guided Setup'))),
    ('category', ('03-transactions-expenses/', _('Adding expenses'))),
    ('insights', ('18-insights/', _('Insights'))),
    ('family', ('24-family-sharing/', _('Family sharing'))),
    ('calendar', ('10-calendar/', _('Calendar'))),
)

# Each Guided Setup has its own page in the guide.
FLOW_PAGES = {
    'loan': ('13-tmr-flows/debt/', _('Guided Setup: Loan')),
    'creditcard': ('13-tmr-flows/debt/', _('Guided Setup: Credit card')),
    'salary': ('13-tmr-flows/income-and-bills/', _('Guided Setup: Salary')),
    'rentbill': ('13-tmr-flows/income-and-bills/', _('Guided Setup: Rent and bills')),
    'fd': ('13-tmr-flows/savings-and-investments/', _('Guided Setup: Fixed deposit')),
    'sip': ('13-tmr-flows/savings-and-investments/', _('Guided Setup: SIP')),
    'savingsgoal': ('13-tmr-flows/savings-and-investments/', _('Guided Setup: Savings goal')),
    'ppfepfnps': ('13-tmr-flows/savings-and-investments/', _('Guided Setup: PPF, EPF and NPS')),
    'insurance': ('13-tmr-flows/income-and-bills/', _('Guided Setup: Insurance')),
    'family': ('24-family-sharing/', _('Guided Setup: Family')),
    'car': ('13-tmr-flows/assets/', _('Guided Setup: Car')),
    'gold': ('13-tmr-flows/assets/', _('Guided Setup: Gold')),
}

FRONT_PAGE = ('', _('User guide'))


def help_for(url_name: str, flow_key: str = '') -> tuple:
    """(docs path, title) for a page, by its url name (and, on a Guided Setup page, the setup's key)."""
    if flow_key in FLOW_PAGES:
        return FLOW_PAGES[flow_key]
    if url_name in EXACT:
        return EXACT[url_name]
    for prefix, page in PREFIXES:
        if url_name.startswith(prefix):
            return page
    return FRONT_PAGE


def help_page(request):
    """Context processor: the guide page that explains the current page."""
    match = getattr(request, 'resolver_match', None)
    path, title = help_for(match.url_name or '' if match else '', (match.kwargs.get('key') or '') if match else '')
    return {'help_page': {'path': path, 'title': title}, 'DOCS_BASE_URL': settings.DOCS_BASE_URL}
