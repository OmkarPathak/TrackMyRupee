from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url

from ..forms import ExpenseForm
from ..models import Account, CapitalEvent, Category, Expense
from ..posthog_utils import ph_capture
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
    process_user_recurring_transactions,
)
from ..filters import EXPENSE_FILTERS, apply_filter_config
from ..filters.definitions import get_user_categories
from .utils import apply_date_filters, get_object_by_uuid_or_pk


class ExpenseListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Expense
    template_name = 'expenses/expense_list.html'
    htmx_template_name = 'expenses/partials/_expense_list.html'
    context_object_name = 'expenses'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        process_user_recurring_transactions(request.user, max_catchup=2)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        base_qs = Expense.objects.filter(user=self.request.user).select_related('account', 'category_fk')
        queryset, self.applied_state = apply_filter_config(base_qs, self.request, EXPENSE_FILTERS)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Calculate stats for the filtered queryset in a single DB query
        filtered_queryset = self.object_list
        stats = filtered_queryset.aggregate(count=Count('id'), total=Sum('base_amount'))
        context['filtered_count'] = stats['count']
        context['filtered_amount'] = stats['total'] or 0

        # Categories for bulk-edit modal
        context['categories'] = [c['value'] for c in get_user_categories(user=self.request.user)]
        
        # Filter system config & state
        context['filter_config'] = EXPENSE_FILTERS
        applied_state = getattr(self, 'applied_state', {})
        context['applied_state'] = applied_state

        time_period = applied_state.get('time_period', 'this_month')
        start_date = applied_state.get('start_date', '')
        end_date = applied_state.get('end_date', '')
        context['time_period'] = time_period
        context['start_date'] = start_date or ''
        context['end_date'] = end_date or ''
        
        selected_filters = applied_state.get('filters', {})
        selected_categories = selected_filters.get('category', [])
        selected_payment_methods = selected_filters.get('payment_method', [])
        selected_accounts = selected_filters.get('account', [])
        search_query = applied_state.get('search', '')

        sort_by = applied_state.get('sort', 'date_desc')
        context['sort_by'] = sort_by
        context['current_sort'] = sort_by
        context['selected_categories'] = selected_categories
        context['selected_payment_methods'] = selected_payment_methods
        context['selected_accounts'] = selected_accounts
        context['search_query'] = search_query
        context['payment_methods'] = Expense.PAYMENT_OPTIONS
        context['accounts'] = Account.objects.filter(user=self.request.user, is_active=True).order_by('name')

        active_filters = len(selected_filters)
        if search_query:
            active_filters += 1
        if time_period != 'this_month':
            active_filters += 1

        if sort_by and sort_by != 'date_desc':
            active_filters += 1
        context['active_filters_count'] = active_filters

        # Remove legacy month navigation
        context['prev_month_url'] = None
        context['next_month_url'] = None


        # Calculate days left in cycle
        now = datetime.now()
        is_current_month = False
        days_left = None
        
        if time_period == 'this_month':
            is_current_month = True
            from ..periods import resolve_period
            period = resolve_period(user=self.request.user, time_period='this_month', today=now.date())
            if period.end:
                days_left = max((period.end - now.date()).days, 0)
                
        context['is_current_month'] = is_current_month
        context['days_left'] = days_left

        return context

class ExpenseCreateView(LoginRequiredMixin, View):
    """Deep link (/expenses/add/, PWA shortcut, old bookmarks) to the New Expense composer.

    The composer itself lives in ``components/expense_composer.html`` and is mounted on every
    page; this page just asks it to open and to send the user back to ``next`` when closed.
    """
    template_name = 'expenses/expense_add.html'

    def get(self, request, *args, **kwargs):
        next_url = get_safe_redirect_url(request, request.GET.get('next', ''), reverse('expense-list'))
        return render(request, self.template_name, {'next_url': next_url})

class ExpenseUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'expenses/expense_form.html'
    success_url = reverse_lazy('expense-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            ph_capture(self.request.user, 'expense_updated', {
                'amount': str(self.object.amount),
                'currency': self.object.currency,
                'category': self.object.category or '',
            })
            messages.success(self.request, _("Expense updated successfully!"))
            return response
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to update expense because currency conversion failed or data is invalid."))
            return self.form_invalid(form)

    def get_queryset(self):
        return Expense.objects.filter(user=self.request.user).select_related('account', 'category_fk')

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, super().get_success_url())
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class ExpenseDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Expense
    template_name = 'expenses/expense_confirm_delete.html'
    success_url = reverse_lazy('expense-list')

    def get_queryset(self):
        return Expense.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Expense deleted successfully."))
        ph_capture(self.request.user, 'expense_deleted', {})
        return super().form_valid(form)

    def get_success_url(self):
        url = reverse('expense-list')
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, url)
        query_params = self.request.GET.urlencode()
        if query_params:
            return f"{url}?{query_params}"
        return url

def _numeric_ids(raw_ids):
    """Keep only well-formed integer ids so tampered POSTs cannot crash the query."""
    return [int(value) for value in raw_ids if str(value).strip().isdigit()]


class ExpenseBulkDeleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        expense_ids = _numeric_ids(request.POST.getlist('expense_ids'))
        if not expense_ids:
            messages.error(request, _('No expenses selected for deletion.'))
            return redirect('expense-list')
            
        # Filter by IDs and ensuring they belong to the current user for security
        expenses_list = list(
            Expense.objects.filter(id__in=expense_ids, user=request.user).select_related('account')
        )
        deleted_count = len(expenses_list)
        
        if deleted_count > 0:
            with transaction.atomic():
                for expense in expenses_list:
                    # Call model delete to ensure account balances are restored.
                    expense.delete()
            ph_capture(request.user, 'expense_bulk_deleted', {'count': deleted_count})
            messages.success(request, _('%(count)d expenses deleted successfully.') % {'count': deleted_count})
        else:
            messages.warning(request, _('No valid expenses found to delete.'))
            
        return redirect(self.get_success_url())

    def get_success_url(self):
        url = reverse('expense-list')
        query_params = self.request.GET.urlencode()
        if query_params:
            return f"{url}?{query_params}"
        return url

class ExpenseBulkUpdateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        expense_ids = _numeric_ids(request.POST.getlist('expense_ids'))
        category = (request.POST.get('bulk_category') or '').strip()
        payment_method = request.POST.get('bulk_payment_method')

        if not expense_ids:
            messages.error(request, _('No expenses selected for update.'))
            return redirect('expense-list')
            
        update_data = {}
        if category:
            # Straight from the DB: the cached list used by the filters can be minutes stale.
            known = set(Category.objects.filter(user=request.user).values_list('name', flat=True))
            known |= set(Expense.objects.filter(user=request.user).values_list('category', flat=True).distinct())
            if category not in known:
                messages.error(request, _('Unknown category.'))
                return redirect('expense-list')
            update_data['category'] = category
        if payment_method:
            if payment_method not in dict(Expense.PAYMENT_OPTIONS):
                messages.error(request, _('Unknown payment method.'))
                return redirect('expense-list')
            update_data['payment_method'] = payment_method

        if not update_data:
            messages.warning(request, _('No fields selected to update.'))
            return redirect('expense-list')

        # Save row by row (not QuerySet.update) so the audit log, ledger and caches stay in sync.
        expenses_to_update = list(Expense.objects.filter(id__in=expense_ids, user=request.user))
        updated_count = 0
        if expenses_to_update:
            with transaction.atomic():
                for expense in expenses_to_update:
                    for field, value in update_data.items():
                        setattr(expense, field, value)
                    expense.save()
                    updated_count += 1

        if updated_count > 0:
            ph_capture(request.user, 'expense_bulk_updated', {'count': updated_count})
            messages.success(request, _('%(count)d expenses updated successfully.') % {'count': updated_count})
        else:
            messages.warning(request, _('No valid expenses found to update.'))

        return redirect('expense-list')

class ExpenseConvertToCapitalEventView(LoginRequiredMixin, View):
    """Convert an Expense into a CapitalEvent."""

    def post(self, request, pk):
        expense = get_object_by_uuid_or_pk(Expense, pk, user=request.user)
        if not expense.account_id:
            messages.error(request, _("Edit this expense and choose an account first, so the capital event is charged to an account."))
            return redirect('expense-list')
        with transaction.atomic():
            # Match the category to a CapitalEvent subtype if possible, otherwise use 'other'
            subtype = 'other'
            category_lower = expense.category.lower()
            for key, display in CapitalEvent.SUBTYPE_CHOICES:
                if key.replace('_', ' ') in category_lower or display.lower() in category_lower:
                    subtype = key
                    break
            
            event = CapitalEvent(
                user=request.user,
                date=expense.date,
                amount=expense.amount,
                currency=expense.currency,
                note=expense.description,
                subtype=subtype,
                account=expense.account,
            )
            event.save()
            expense.delete()
        ph_capture(request.user, 'expense_converted_to_capital_event', {})
        messages.success(request, _("Expense converted to a capital event."))
        
        next_url = request.GET.get('next') or request.POST.get('next')
        return redirect(get_safe_redirect_url(request, next_url, reverse('capital-event-list')))

