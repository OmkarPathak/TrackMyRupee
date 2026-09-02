import csv
import io
import zipfile
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from ..models import (
    Expense,
    Income,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
)
from ..posthog_utils import ph_capture


class DataExportView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/export_data.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_export'] = self.request.user.profile.can_export_csv
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.profile.can_export_csv:
            messages.error(request, _("Exporting is a paid feature. Please upgrade."))
            return redirect('pricing')

        selected_entities = request.POST.getlist('entities')
        if not selected_entities:
            messages.warning(request, _("Please select at least one data type to export."))
            return redirect('export-data')

        files_to_zip = {}

        # 1. Expenses
        if 'expenses' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                _('Date'),
                _('Description'),
                _('Amount'),
                _('Currency'),
                _('Exchange Rate'),
                _('Base Amount'),
                _('Category'),
                _('Payment Method'),
                _('Account'),
                _('Account Currency'),
                _('Type'),
            ])
            for e in Expense.objects.select_related('account').filter(user=request.user).order_by('-date'):
                writer.writerow([
                    e.date,
                    e.description,
                    e.amount,
                    e.currency,
                    e.exchange_rate,
                    e.base_amount,
                    e.category,
                    e.payment_method,
                    e.account.name if e.account else '',
                    e.account.currency if e.account else '',
                    _('Expense'),
                ])
            files_to_zip['expenses.csv'] = output.getvalue()

        # 2. Incomes
        if 'incomes' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                _('Date'),
                _('Source'),
                _('Amount'),
                _('Currency'),
                _('Exchange Rate'),
                _('Base Amount'),
                _('Description'),
                _('Account'),
                _('Account Currency'),
            ])
            for i in Income.objects.select_related('account').filter(user=request.user).order_by('-date'):
                writer.writerow([
                    i.date,
                    i.source,
                    i.amount,
                    i.currency,
                    i.exchange_rate,
                    i.base_amount,
                    i.description,
                    i.account.name if i.account else '',
                    i.account.currency if i.account else '',
                ])
            files_to_zip['incomes.csv'] = output.getvalue()

        # 3. Transfers
        if 'transfers' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                _('Date'),
                _('From Account'),
                _('From Account Currency'),
                _('To Account'),
                _('To Account Currency'),
                _('Amount'),
                _('Exchange Rate'),
                _('Converted Amount'),
                _('Description'),
            ])
            for t in Transfer.objects.select_related('from_account', 'to_account').filter(user=request.user).order_by('-date'):
                writer.writerow([
                    t.date,
                    t.from_account.name,
                    t.from_account.currency,
                    t.to_account.name,
                    t.to_account.currency,
                    t.amount,
                    t.exchange_rate,
                    t.converted_amount,
                    t.description,
                ])
            files_to_zip['transfers.csv'] = output.getvalue()

        # 4. Loan Repayments
        if 'loan_repayments' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([
                _('Date'),
                _('Loan'),
                _('Total Amount'),
                _('Loan Currency'),
                _('Exchange Rate'),
                _('Base Amount'),
                _('Principal Portion'),
                _('Interest Portion'),
                _('Paid From Account'),
                _('Account Currency'),
            ])
            for r in LoanRepayment.objects.select_related('loan', 'from_account').filter(loan__user=request.user).order_by('-date'):
                writer.writerow([
                    r.date,
                    r.loan.name,
                    r.amount,
                    r.loan.currency,
                    r.exchange_rate,
                    r.base_amount,
                    r.principal_portion,
                    r.interest_portion,
                    r.from_account.name if r.from_account else '',
                    r.from_account.currency if r.from_account else '',
                ])
            files_to_zip['loan_repayments.csv'] = output.getvalue()

        # 5. Recurring Transactions
        if 'recurring' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([_('Description'), _('Amount'), _('Type'), _('Frequency'), _('Next Due Date'), _('End Date'), _('Active')])
            for r in RecurringTransaction.objects.filter(user=request.user).order_by('start_date'):
                writer.writerow([r.description, r.amount, r.transaction_type, r.frequency, r.next_due_date, r.end_date, r.is_active])
            files_to_zip['recurring_transactions.csv'] = output.getvalue()

        # 6. Savings Goals
        if 'goals' in selected_entities:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow([_('Name'), _('Target Amount'), _('Current Amount'), _('Target Date'), _('Completed')])
            for g in SavingsGoal.objects.filter(user=request.user).order_by('target_date'):
                writer.writerow([g.name, g.target_amount, g.current_amount, g.target_date, g.is_completed])
            files_to_zip['savings_goals.csv'] = output.getvalue()

        # Handle output
        ph_capture(request.user, 'data_exported', {'entities': selected_entities, 'format': 'zip' if len(files_to_zip) > 1 else 'csv'})
        if len(files_to_zip) == 1:
            filename, content = list(files_to_zip.items())[0]
            response = HttpResponse(content, content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        else:
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for filename, content in files_to_zip.items():
                    zip_file.writestr(filename, content)
            
            response = HttpResponse(zip_buffer.getvalue(), content_type='application/x-zip-compressed')
            response['Content-Disposition'] = 'attachment; filename="financial_data_export.zip"'
            return response

@login_required
def export_expenses(request):
    """
    Improved export_expenses that respects active filters.
    Used by the 'Export' button on the Expense List page.
    """
    if not request.user.profile.can_export_csv:
        messages.error(request, _("Exporting is a paid feature. Please upgrade."))
        return redirect('pricing')

    # Re-apply filters from the list view
    queryset = Expense.objects.filter(user=request.user)

    search_query = request.GET.get('search')
    if search_query:
        queryset = queryset.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))

    start_date = request.GET.get('start_date')
    if start_date:
        queryset = queryset.filter(date__gte=start_date)

    end_date = request.GET.get('end_date')
    if end_date:
        queryset = queryset.filter(date__lte=end_date)

    categories = request.GET.getlist('category')
    if categories:
        queryset = queryset.filter(category__in=categories)

    years = request.GET.getlist('year')
    if years:
        queryset = queryset.filter(date__year__in=years)

    months = request.GET.getlist('month')
    if months:
        queryset = queryset.filter(date__month__in=months)

    # Standard CSV Export
    response = HttpResponse(content_type='text/csv')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    response['Content-Disposition'] = f'attachment; filename="expenses_filtered_{timestamp}.csv"'
    
    writer = csv.writer(response)
    writer.writerow([
        _('Date'),
        _('Description'),
        _('Amount'),
        _('Currency'),
        _('Exchange Rate'),
        _('Base Amount'),
        _('Category'),
        _('Payment Method'),
        _('Account'),
        _('Account Currency'),
    ])
    
    for e in queryset.select_related('account').order_by('-date'):
        writer.writerow([
            e.date,
            e.description,
            e.amount,
            e.currency,
            e.exchange_rate,
            e.base_amount,
            e.category,
            e.payment_method,
            e.account.name if e.account else '',
            e.account.currency if e.account else '',
        ])
    
    ph_capture(request.user, 'expense_list_exported', {'has_filters': bool(search_query or start_date or end_date or categories)})
    return response
