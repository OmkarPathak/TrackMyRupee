import csv
import calendar
from datetime import datetime, date
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required
from django.views.generic import ListView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy, reverse
from django.db.models import Sum, Q, Count
from django.contrib import messages
from django.utils.translation import gettext as _
from django.forms import modelformset_factory

from ..models import Expense, Category, CURRENCY_CHOICES
from ..forms import ExpenseForm
from .mixins import RecurringTransactionMixin

from .mixins import process_user_recurring_transactions

class ExpenseListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Expense
    template_name = 'expenses/expense_list.html'
    context_object_name = 'expenses'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        process_user_recurring_transactions(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = Expense.objects.filter(user=self.request.user).order_by('-date')
        
        # Filtering
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_categories = self.request.GET.getlist('category')
        search_query = self.request.GET.get('search')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

        # Remove empty strings from lists
        selected_years = [y for y in selected_years if y]
        selected_months = [m for m in selected_months if m]
        selected_categories = [c for c in selected_categories if c]
        
        # Date Range Logic (Precedence over Year/Month)
        if start_date or end_date:
            if start_date:
                queryset = queryset.filter(date__gte=start_date)
            if end_date:
                queryset = queryset.filter(date__lte=end_date)
        else:
            # Check if any specific filter is active
            has_active_filters = (
                selected_years or 
                selected_months or 
                search_query  # Don't check categories as we might want defaults even if cat is selected? No, usually filters are additive.
            )
            
            # If no year/month/search filters, default to current month/year
            # (ignoring category here might be debated, but typically if I just filter 'Food', I might want all time or current month? 
            #  The dashboard logic defaults to current month if no year/month. Let's stick to that.)
            if not has_active_filters:
                selected_years = [str(datetime.now().year)]
                selected_months = [str(datetime.now().month)]
            
            if selected_years:
                queryset = queryset.filter(date__year__in=selected_years)
            
            if selected_months:
                queryset = queryset.filter(date__month__in=selected_months)

        if selected_categories:
            queryset = queryset.filter(category__in=selected_categories)
        
        # Filter by Payment Method
        payment_method = self.request.GET.get('payment_method')
        if payment_method:
            queryset = queryset.filter(payment_method=payment_method)

        # Filter by Investment Status
        is_investment = self.request.GET.get('is_investment')
        if is_investment:
            is_inv_bool = is_investment.lower() == 'true'
            investment_categories = Category.objects.filter(user=self.request.user, is_investment=True).values_list('name', flat=True)
            if is_inv_bool:
                queryset = queryset.filter(category__in=investment_categories)
            else:
                queryset = queryset.exclude(category__in=investment_categories)

        if search_query:
            queryset = queryset.filter(description__icontains=search_query)
            
        # Sorting
        sort_by = self.request.GET.get('sort')
        if sort_by == 'amount_asc':
            queryset = queryset.order_by('amount')
        elif sort_by == 'amount_desc':
            queryset = queryset.order_by('-amount')
        # Default is already '-date' from line 961, so valid fallback.
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Calculate stats for the filtered queryset
        filtered_queryset = self.object_list
        context['filtered_count'] = filtered_queryset.count()
        context['filtered_amount'] = filtered_queryset.aggregate(Sum('base_amount'))['base_amount__sum'] or 0

        # Get unique years and categories for validation
        user_expenses = Expense.objects.filter(user=self.request.user)
        years_dates = user_expenses.dates('date', 'year', order='DESC')
        years = sorted(list(set([d.year for d in years_dates] + [datetime.now().year])), reverse=True)
        # Python-side deduplication to handle whitespace variants (e.g. "Goa" vs "Goa ")
        raw_used_categories = user_expenses.values_list('category', flat=True)
        raw_defined_categories = Category.objects.filter(user=self.request.user).values_list('name', flat=True)
        all_cats = set([c.strip() for c in raw_used_categories if c and c.strip()]) | set([c.strip() for c in raw_defined_categories if c and c.strip()])
        categories = sorted(list(all_cats), key=str.lower)
        
        context['years'] = years
        context['categories'] = categories
        context['months_list'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        
        # Determine selected filters for UI
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        context['start_date'] = start_date
        context['end_date'] = end_date
        
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_categories = self.request.GET.getlist('category')
        search_query = self.request.GET.get('search', '')

        # Remove empty strings
        selected_years = [y for y in selected_years if y]
        selected_months = [m for m in selected_months if m]
        selected_categories = [c for c in selected_categories if c]
        
        context['selected_years'] = selected_years
        context['selected_months'] = selected_months
        context['selected_categories'] = selected_categories
        context['search_query'] = search_query

        # Mirror default logic from get_queryset if NO date range is present
        if not (start_date or end_date):
            has_active_filters = (selected_years or selected_months or search_query)
            if not has_active_filters:
                context['selected_years'] = [str(datetime.now().year)]
                context['selected_months'] = [str(datetime.now().month)]
            
        return context

class ExpenseCreateView(LoginRequiredMixin, View):
    template_name = 'expenses/expense_form.html'

    def get(self, request, *args, **kwargs):
        # We need to wrap the formset to pass 'user' to the form constructor
        ExpenseFormSet = modelformset_factory(Expense, form=ExpenseForm, extra=1, can_delete=True)
        # Pass user to form kwargs using formset_factory's form_kwargs (requires Django 4.0+)
        # For older Django or modelformset, we might need a custom formset or curry the form.
        # Simpler approach: Use a lambda or partial, but modelformset_factory creates a class.
        
        # Actually, best way for modelformset with custom init args is to override BaseFormSet or manually iterate.
        # But simpler hack: Set the widget choices in the view by iterating forms? No, new forms need it.
        
        # Let's use form_kwargs in the formset initialization if supported.
        # Django 1.9+ supports form_kwargs in formset constructor.
        
        initial_data = [{'date': datetime.now().date(), 'currency': request.user.profile.currency} for _ in range(1)]
        formset = ExpenseFormSet(queryset=Expense.objects.none(), initial=initial_data, form_kwargs={'user': request.user})
        next_url = request.GET.get('next', '')
        
        # Get top 5 frequent categories for this user
        frequent_categories = Expense.objects.filter(user=request.user).values('category').annotate(count=Count('category')).order_by('-count')[:5]
        frequent_category_names = [item['category'] for item in frequent_categories]

        return render(request, self.template_name, {
            'formset': formset, 
            'next_url': next_url,
            'frequent_categories': frequent_category_names
        })

    def post(self, request, *args, **kwargs):
        ExpenseFormSet = modelformset_factory(Expense, form=ExpenseForm, extra=1, can_delete=True)
        formset = ExpenseFormSet(request.POST, form_kwargs={'user': request.user})
        if formset.is_valid():
            try:
                instances = formset.save(commit=False)
                for instance in instances:
                    instance.user = request.user
                    instance.save()
                
                next_url = request.POST.get('next') or request.GET.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('expense-list')
            except IntegrityError as e:
                messages.error(request, _("Duplicate record found! You already have this expense recorded for this date."))
                return render(request, self.template_name, {'formset': formset})
        return render(request, self.template_name, {'formset': formset})

class ExpenseUpdateView(LoginRequiredMixin, UpdateView):
    model = Expense
    form_class = ExpenseForm
    template_name = 'expenses/expense_form.html'
    success_url = reverse_lazy('expense-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, _("Expense updated successfully!"))
        return super().form_valid(form)

    def get_queryset(self):
        return Expense.objects.filter(user=self.request.user)

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        
        # Get top 5 frequent categories for this user
        frequent_categories = Expense.objects.filter(user=self.request.user).values('category').annotate(count=Count('category')).order_by('-count')[:5]
        context['frequent_categories'] = [item['category'] for item in frequent_categories]
        
        return context

class ExpenseDeleteView(LoginRequiredMixin, DeleteView):
    model = Expense
    template_name = 'expenses/expense_confirm_delete.html'
    success_url = reverse_lazy('expense-list')

    def get_queryset(self):
        return Expense.objects.filter(user=self.request.user)

class ExpenseBulkDeleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        expense_ids = request.POST.getlist('expense_ids')
        if not expense_ids:
            messages.error(request, 'No expenses selected for deletion.')
            return redirect('expense-list')
            
        # Filter by IDs and ensuring they belong to the current user for security
        expenses_to_delete = Expense.objects.filter(id__in=expense_ids, user=request.user)
        deleted_count = expenses_to_delete.count()
        
        if deleted_count > 0:
            expenses_to_delete.delete()
            messages.success(request, f'{deleted_count} expenses deleted successfully.')
        else:
            messages.warning(request, 'No valid expenses found to delete.')
            
        return redirect('expense-list')

class ExpenseBulkUpdateView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        expense_ids = request.POST.getlist('expense_ids')
        category = request.POST.get('bulk_category')
        payment_method = request.POST.get('bulk_payment_method')
        
        if not expense_ids:
            messages.error(request, _('No expenses selected for update.'))
            return redirect('expense-list')
            
        update_data = {}
        if category:
            update_data['category'] = category
        if payment_method:
            update_data['payment_method'] = payment_method
            
        if not update_data:
            messages.warning(request, _('No fields selected to update.'))
            return redirect('expense-list')
            
        # Filter by IDs and ensure they belong to the current user
        expenses_to_update = Expense.objects.filter(id__in=expense_ids, user=request.user)
        updated_count = expenses_to_update.count()
        
        if updated_count > 0:
            expenses_to_update.update(**update_data)
            messages.success(request, _(f'{updated_count} expenses updated successfully.'))
        else:
            messages.warning(request, _('No valid expenses found to update.'))
            
        return redirect('expense-list')
