import calendar
import json
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.forms import modelformset_factory
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url

from ..forms import ExpenseForm
from ..models import Account, CapitalEvent, Category, Expense
from ..parser import parse_expense_nl
from ..posthog_utils import ph_capture
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
    process_user_recurring_transactions,
)
from .utils import apply_date_filters, get_object_by_uuid_or_pk


class ExpenseListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Expense
    template_name = 'expenses/expense_list.html'
    htmx_template_name = 'expenses/partials/_expense_list.html'
    context_object_name = 'expenses'
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        process_user_recurring_transactions(request.user)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = Expense.objects.filter(user=self.request.user).select_related('account', 'category_fk').order_by('-date')
        
        # Filtering
        selected_years = self.request.GET.getlist('year')
        selected_categories = self.request.GET.getlist('category')
        selected_payment_methods = self.request.GET.getlist('payment_method')
        selected_accounts = self.request.GET.getlist('account')
        search_query = self.request.GET.get('search')
        
        queryset = apply_date_filters(queryset, self.request)

        # Remove empty strings from lists
        selected_categories = [c for c in selected_categories if c]
        selected_payment_methods = [pm for pm in selected_payment_methods if pm]
        selected_accounts = [acc for acc in selected_accounts if acc]

        if selected_categories:
            queryset = queryset.filter(category__in=selected_categories)
        
        if selected_payment_methods:
            queryset = queryset.filter(payment_method__in=selected_payment_methods)

        if selected_accounts:
            queryset = queryset.filter(account_id__in=selected_accounts)


        if search_query:
            queryset = queryset.filter(description__icontains=search_query)
            
        # Sorting
        sort_by = self.request.GET.get('sort', 'date_desc')
        if sort_by == 'date_asc':
            queryset = queryset.order_by('date', 'created_at', 'id')
        elif sort_by == 'amount_desc':
            queryset = queryset.order_by('-base_amount', '-id')
        elif sort_by == 'amount_asc':
            queryset = queryset.order_by('base_amount', 'id')
        else:
            queryset = queryset.order_by('-date', '-created_at', '-id')
            
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
        raw_used_categories = user_expenses.values_list('category', flat=True).distinct()
        raw_defined_categories = Category.objects.filter(user=self.request.user).values_list('name', flat=True)
        # Use a set for final deduplication and strip only the distinct results
        all_cats = {c.strip() for c in raw_used_categories if c} | {c.strip() for c in raw_defined_categories if c}
        categories = sorted(list(all_cats), key=str.lower)
        
        context['years'] = years
        context['categories'] = categories
        context['months_list'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        
        # Determine selected filters for UI
        time_period = self.request.GET.get('time_period', 'this_month')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        context['time_period'] = time_period
        context['start_date'] = start_date or ''
        context['end_date'] = end_date or ''
        
        selected_categories = self.request.GET.getlist('category')
        selected_payment_methods = self.request.GET.getlist('payment_method')
        selected_accounts = self.request.GET.getlist('account')
        search_query = self.request.GET.get('search', '')

        # Remove empty strings
        selected_categories = [c for c in selected_categories if c]
        selected_payment_methods = [pm for pm in selected_payment_methods if pm]
        selected_accounts = [acc for acc in selected_accounts if acc]
        
        sort_by = self.request.GET.get('sort', 'date_desc')
        context['sort_by'] = sort_by
        context['current_sort'] = sort_by
        context['selected_categories'] = selected_categories
        context['selected_payment_methods'] = selected_payment_methods
        context['selected_accounts'] = selected_accounts
        context['search_query'] = search_query
        context['payment_methods'] = Expense.PAYMENT_OPTIONS
        context['accounts'] = Account.objects.filter(user=self.request.user, is_active=True).order_by('name')

        active_filters = 0
        if search_query:
            active_filters += 1
        if time_period != 'this_month':
            active_filters += 1
        if selected_categories:
            active_filters += 1
        if selected_payment_methods:
            active_filters += 1
        if selected_accounts:
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
            last_day = calendar.monthrange(now.year, now.month)[1]
            days_left = last_day - now.day
                
        context['is_current_month'] = is_current_month
        context['days_left'] = days_left

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
            instances = formset.save(commit=False)
            
            # Check monthly limit for FREE tier
            from finance_tracker.plans import get_limit
            limit = get_limit(request.user.profile.active_tier, 'expenses_per_month')
            
            if limit != -1:
                now = datetime.now()
                # Count expenses already in DB for this month
                existing_count = Expense.objects.filter(
                    user=request.user, 
                    date__year=now.year, 
                    date__month=now.month
                ).count()
                
                # Count how many NEW expenses are being added for the CURRENT month
                # (Ignoring deletions for simplicity in limit enforcement)
                new_count = len([
                    inst for inst in instances 
                    if inst.date and inst.date.year == now.year and inst.date.month == now.month and not inst.pk
                ])
                
                if existing_count + new_count > limit:
                    messages.error(request, _("You have reached the monthly limit of %(limit)s expenses for your current plan. Please upgrade to add more.") % {'limit': limit})
                    return redirect('pricing')

            try:
                for instance in instances:
                    instance.user = request.user
                    instance.save()
                    ph_capture(request.user, 'expense_created', {
                        'amount': str(instance.amount),
                        'currency': instance.currency,
                        'category': instance.category or '',
                        'payment_method': instance.payment_method or '',
                        'has_account': bool(instance.account_id),
                    })
                
                # Handle deletions from formset
                for obj in formset.deleted_objects:
                    obj.delete()

                messages.success(request, _("Expenses added successfully!"))
                next_url = request.POST.get('next') or request.GET.get('next')

                if next_url:
                    return redirect(next_url)
                return redirect('expense-list')

            except IntegrityError:
                messages.error(request, _("Duplicate record found! You already have this expense recorded for this date."))
                return render(request, self.template_name, {'formset': formset})
            except (RuntimeError, ValidationError):
                messages.error(request, _("Unable to save expense right now because currency conversion failed or data is invalid."))
                return render(request, self.template_name, {'formset': formset})
        return render(request, self.template_name, {'formset': formset})

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
        
        # Get top 5 frequent categories for this user
        frequent_categories = Expense.objects.filter(user=self.request.user).values('category').annotate(count=Count('category')).order_by('-count')[:5]
        context['frequent_categories'] = [item['category'] for item in frequent_categories]
        
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
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        url = reverse('expense-list')
        query_params = self.request.GET.urlencode()
        if query_params:
            return f"{url}?{query_params}"
        return url

class ExpenseBulkDeleteView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        expense_ids = request.POST.getlist('expense_ids')
        if not expense_ids:
            messages.error(request, 'No expenses selected for deletion.')
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
            ph_capture(request.user, 'expense_bulk_updated', {'count': updated_count})
            messages.success(request, _('%(count)d expenses updated successfully.') % {'count': updated_count})
        else:
            messages.warning(request, _('No valid expenses found to update.'))
            
        return redirect('expense-list')

class ExpenseConvertToCapitalEventView(LoginRequiredMixin, View):
    """Convert an Expense into a CapitalEvent."""

    def post(self, request, pk):
        expense = get_object_by_uuid_or_pk(Expense, pk, user=request.user)
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
        if next_url:
            return redirect(next_url)
        return redirect('capital-event-list')

@require_POST
@login_required
def parse_expense_view(request):
    """
    API endpoint for natural language expense parsing.
    """
    try:
        data = json.loads(request.body)
        text = data.get('text', '')
        
        # Get user's categories for better matching
        user_categories = list(Category.objects.filter(user=request.user).values_list('name', flat=True))
        
        # Also get most frequent category names from expenses
        frequent_categories = list(Expense.objects.filter(user=request.user).values_list('category', flat=True).distinct()[:10])
        combined_categories = list(set(user_categories + frequent_categories))
        
        # Get last used account and payment method as defaults
        last_expense = Expense.objects.filter(user=request.user).order_by('-date', '-created_at').first()
        default_account = last_expense.account.name if last_expense and last_expense.account else None
        default_payment_method = last_expense.payment_method if last_expense else 'Cash' # sensible default
        
        # Get user's accounts for matching
        user_accounts = list(Account.objects.filter(user=request.user, is_active=True).values_list('name', flat=True))
        
        result = parse_expense_nl(text, user_categories=combined_categories, user_accounts=user_accounts, user=request.user)
        if result:
            predicted_description = result.get('description')
            predicted_category = result.get('category')
            cat_account = None
            cat_payment_method = None
            
            if predicted_category:
                from django.db.models import Q
                
                last_cat_expense = None
                # 1. Try to find the last expense with the same description and category
                if predicted_description:
                    last_cat_expense = Expense.objects.filter(
                        Q(category__iexact=predicted_category) | Q(category_fk__name__iexact=predicted_category),
                        user=request.user,
                        description__icontains=predicted_description
                    ).select_related('account').order_by('-date', '-created_at').first()
                
                # 2. If no description match, fall back to just the category
                if not last_cat_expense:
                    last_cat_expense = Expense.objects.filter(
                        Q(category__iexact=predicted_category) | Q(category_fk__name__iexact=predicted_category),
                        user=request.user
                    ).select_related('account').order_by('-date', '-created_at').first()
                
                if last_cat_expense:
                    cat_account = last_cat_expense.account.name if last_cat_expense.account else None
                    cat_payment_method = last_cat_expense.payment_method

            # Apply defaults if not parsed (prioritize category-specific over global)
            if not result.get('account'):
                result['account'] = cat_account or default_account
            
            result['payment_method'] = cat_payment_method or default_payment_method
            
            return JsonResponse({'success': True, 'data': result})
        return JsonResponse({'success': False, 'error': 'No input text provided.'})
    except Exception:
        return JsonResponse({'success': False, 'error': _('Unable to parse expense right now.')}, status=400)
