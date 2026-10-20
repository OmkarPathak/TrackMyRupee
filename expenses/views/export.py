import csv
import io
import zipfile
from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from ..ledger_read_service import LedgerReadService
from ..models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
)
from ..posthog_utils import ph_capture

# A spreadsheet runs any cell that starts with one of these as a formula.
_FORMULA_STARTS = ('=', '+', '-', '@', '\t', '\r')
# Excel only reads a UTF-8 CSV correctly (rupee sign, Hindi) when it starts with a byte-order mark.
UTF8_BOM = '\ufeff'


def safe_cell(value):
    """Text from the user (a description, a name) must not turn into a formula when the file is opened in
    a spreadsheet: a leading quote makes it plain text. Numbers, dates and booleans pass through."""
    if isinstance(value, str) and value.startswith(_FORMULA_STARTS):
        return "'" + value
    return value


def csv_text(header, rows):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return UTF8_BOM + out.getvalue()


def _name(obj):
    return obj.name if obj else ''


def _currency(obj):
    return obj.currency if obj else ''


def _expense_rows(user):
    for e in Expense.objects.select_related('account').filter(user=user, is_deleted=False).order_by('-date'):
        yield [e.date, e.description, e.amount, e.currency, e.exchange_rate, e.base_amount, e.category,
               e.payment_method, _name(e.account), _currency(e.account), _('Expense')]


def _income_rows(user):
    for i in Income.objects.select_related('account').filter(user=user, is_deleted=False).order_by('-date'):
        yield [i.date, i.source, i.amount, i.currency, i.exchange_rate, i.base_amount, i.description,
               _name(i.account), _currency(i.account), i.source_type]


def _transfer_rows(user):
    qs = Transfer.objects.select_related('from_account', 'to_account').filter(user=user, is_deleted=False)
    for t in qs.order_by('-date'):
        yield [t.date, t.from_account.name, t.from_account.currency, t.to_account.name, t.to_account.currency,
               t.amount, t.exchange_rate, t.converted_amount, t.description]


def _loan_repayment_rows(user):
    qs = LoanRepayment.objects.select_related('loan', 'from_account').filter(loan__user=user, is_deleted=False)
    for r in qs.order_by('-date'):
        yield [r.date, r.loan.name, r.amount, r.loan.currency, r.exchange_rate, r.base_amount, r.principal_portion,
               r.interest_portion, _name(r.from_account), _currency(r.from_account)]


def _recurring_rows(user):
    qs = RecurringTransaction.objects.select_related('account').filter(user=user).order_by('start_date')
    for r in qs:
        yield [r.description, r.amount, r.transaction_type, r.frequency, r.next_due_date, r.end_date, r.is_active,
               r.currency, r.category or r.source, _name(r.account)]


def _goal_rows(user):
    for g in SavingsGoal.objects.filter(user=user).order_by('target_date'):
        yield [g.name, g.target_amount, g.current_amount, g.target_date, g.is_completed, g.currency]


def _goal_contribution_rows(user):
    qs = GoalContribution.objects.select_related('goal', 'account').filter(goal__user=user, is_deleted=False)
    for c in qs.order_by('-date'):
        yield [c.date, c.goal.name, c.amount, c.goal.currency, _name(c.account), _currency(c.account)]


def _account_rows(user):
    accounts = list(Account.objects.filter(user=user).select_related('user').order_by('name'))
    balances = LedgerReadService.get_account_balances(accounts) if accounts else {}
    for a in accounts:
        yield [a.name, a.get_account_type_display(), a.currency, balances.get(a.id, a.balance), a.is_active,
               a.created_at.date() if a.created_at else '']


def _capital_event_rows(user):
    for c in CapitalEvent.objects.select_related('account').filter(user=user, is_deleted=False).order_by('-date'):
        yield [c.date, c.get_subtype_display(), c.note, c.amount, c.currency, c.exchange_rate, c.base_amount,
               c.category, _name(c.account), c.include_in_net_worth]


def _loan_rows(user):
    for loan in Loan.objects.filter(user=user).order_by('start_date'):
        yield [loan.name, loan.get_loan_type_display(), loan.initial_principal, loan.currency, loan.start_date,
               loan.duration_months, loan.is_active]


def _category_rows(user):
    from ..budget_cycle import current_budget_month
    from ..budget_resolver import effective_limits_by_name
    from ..budgets import has_limit, normalize_category
    limits = effective_limits_by_name(user, current_budget_month(user, timezone.localdate()))
    for c in Category.objects.filter(user=user).order_by('name'):
        limit = limits.get(normalize_category(c.name))
        yield [c.name, limit if has_limit(limit) else '']


def _budget_limit_rows(user):
    """One row per month and category from the first limit ever set to this month (plus any later
    month with an override or a scheduled change): the limit that applied, and why."""
    from ..budget_resolver import effective_limits_range_by_name, month_start
    from ..budgets import has_limit
    from ..models import BudgetLimitVersion, BudgetMonthOverride

    firsts = list(BudgetLimitVersion.objects.filter(user=user).order_by('effective_from')
                  .values_list('effective_from', flat=True)[:1])
    if not firsts:
        return
    this_month = month_start(timezone.localdate())
    overrides = {(n, m) for n, m in BudgetMonthOverride.objects.filter(user=user)
                 .values_list('category_name', 'month')}
    horizon = date(this_month.year + 2, this_month.month, 1)       # scheduled changes beyond 2 years are not expanded
    last = min(horizon, max([this_month] + [m for _, m in overrides]
                            + list(BudgetLimitVersion.objects.filter(user=user).values_list('effective_from', flat=True))))
    display = {c.name.strip().lower(): c.name for c in Category.objects.filter(user=user)}
    for month, limits in sorted(effective_limits_range_by_name(user, firsts[0], last).items()):
        for name in sorted(limits):
            limit = limits[name]
            if has_limit(limit):
                yield [month.strftime('%Y-%m'), display.get(name, name), limit,
                       'override' if (name, month) in overrides else 'default']


# entity name (the form's checkbox value) -> (file name, header, rows)
EXPORTS = {
    'expenses': ('expenses.csv', ['Date', 'Description', 'Amount', 'Currency', 'Exchange Rate', 'Base Amount',
                                  'Category', 'Payment Method', 'Account', 'Account Currency', 'Type'], _expense_rows),
    'incomes': ('incomes.csv', ['Date', 'Source', 'Amount', 'Currency', 'Exchange Rate', 'Base Amount', 'Description',
                                'Account', 'Account Currency', 'Source Type'], _income_rows),
    'transfers': ('transfers.csv', ['Date', 'From Account', 'From Account Currency', 'To Account',
                                    'To Account Currency', 'Amount', 'Exchange Rate', 'Converted Amount',
                                    'Description'], _transfer_rows),
    'loan_repayments': ('loan_repayments.csv', ['Date', 'Loan', 'Total Amount', 'Loan Currency', 'Exchange Rate',
                                                'Base Amount', 'Principal Portion', 'Interest Portion',
                                                'Paid From Account', 'Account Currency'], _loan_repayment_rows),
    'recurring': ('recurring_transactions.csv', ['Description', 'Amount', 'Type', 'Frequency', 'Next Due Date',
                                                 'End Date', 'Active', 'Currency', 'Category or Source',
                                                 'Account'], _recurring_rows),
    'goals': ('savings_goals.csv', ['Name', 'Target Amount', 'Current Amount', 'Target Date', 'Completed',
                                    'Currency'], _goal_rows),
    'goal_contributions': ('goal_contributions.csv', ['Date', 'Goal', 'Amount', 'Goal Currency', 'Account',
                                                      'Account Currency'], _goal_contribution_rows),
    'accounts': ('accounts.csv', ['Name', 'Type', 'Currency', 'Balance', 'Active', 'Created'], _account_rows),
    'capital_events': ('capital_events.csv', ['Date', 'Type', 'Note', 'Amount', 'Currency', 'Exchange Rate',
                                              'Base Amount', 'Category', 'Account', 'In Net Worth'],
                       _capital_event_rows),
    'loans': ('loans.csv', ['Name', 'Type', 'Principal', 'Currency', 'Start Date', 'Months', 'Active'], _loan_rows),
    'categories': ('categories.csv', ['Category', 'Monthly Budget'], _category_rows),
    'budget_limits': ('budget_limits.csv', ['Month', 'Category', 'Limit', 'Source'], _budget_limit_rows),
}


class DataExportView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/export_data.html'

    def post(self, request, *args, **kwargs):
        selected_entities = [e for e in request.POST.getlist('entities') if e in EXPORTS]
        if not selected_entities:
            messages.warning(request, _("Please select at least one data type to export."))
            return redirect('export-data')

        files_to_zip = {}
        for entity in selected_entities:
            filename, header, rows = EXPORTS[entity]
            files_to_zip[filename] = csv_text([_(h) for h in header], rows(request.user))

        # Handle output
        ph_capture(request.user, 'data_exported', {'entities': selected_entities, 'format': 'zip' if len(files_to_zip) > 1 else 'csv'})
        if len(files_to_zip) == 1:
            filename, content = list(files_to_zip.items())[0]
            response = HttpResponse(content.encode('utf-8'), content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for filename, content in files_to_zip.items():
                zip_file.writestr(filename, content.encode('utf-8'))

        response = HttpResponse(zip_buffer.getvalue(), content_type='application/x-zip-compressed')
        response['Content-Disposition'] = 'attachment; filename="financial_data_export.zip"'
        return response


@login_required
def export_expenses(request):
    """
    Improved export_expenses that respects active filters.
    Used by the 'Export' button on the Expense List page.
    """
    # Re-apply exactly the filters the list view applied (period, search, chips, sort).
    from ..filters import EXPENSE_FILTERS, apply_filter_config

    queryset, _applied = apply_filter_config(Expense.objects.filter(user=request.user), request, EXPENSE_FILTERS)

    # Legacy year/month parameters from older export links.
    years = request.GET.getlist('year')
    if years:
        queryset = queryset.filter(date__year__in=years)

    months = request.GET.getlist('month')
    if months:
        queryset = queryset.filter(date__month__in=months)

    search_query = request.GET.get('search')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    categories = request.GET.getlist('category')

    # Standard CSV Export
    queryset = queryset.filter(is_deleted=False)
    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="expenses_filtered_{timestamp}.csv"'

    header = [_(h) for h in ('Date', 'Description', 'Amount', 'Currency', 'Exchange Rate', 'Base Amount', 'Category',
                             'Payment Method', 'Account', 'Account Currency')]
    rows = (
        [e.date, e.description, e.amount, e.currency, e.exchange_rate, e.base_amount, e.category, e.payment_method,
         _name(e.account), _currency(e.account)]
        for e in queryset.select_related('account')
    )
    response.write(csv_text(header, rows))

    ph_capture(request.user, 'expense_list_exported', {'has_filters': bool(search_query or start_date or end_date or categories)})
    return response
