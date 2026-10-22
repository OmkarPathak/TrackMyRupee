"""Names for the modules, as a person sees them in Settings."""
from django.utils.translation import gettext_lazy as _

from .types import Module

MODULE_LABELS = {
    Module.EXPENSES: _('Expenses'),
    Module.INCOME: _('Income'),
    Module.BUDGETS: _('Budgets'),
    Module.ACCOUNTS: _('Accounts'),
    Module.LOANS: _('Loans'),
    Module.GOALS: _('Goals'),
    Module.RECURRING: _('Recurring payments'),
    Module.TRANSFERS: _('Transfers and investing'),
    Module.CAPITAL_EVENTS: _('Capital events'),
    Module.HOLDINGS: _('Holdings'),
    Module.CALENDAR: _('Calendar'),
    Module.ANALYTICS: _('Analytics'),
    Module.IMPORT: _('Import'),
    Module.CASHFLOW: _('Cash flow'),
}
