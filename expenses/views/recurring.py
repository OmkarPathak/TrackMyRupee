from datetime import date

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from expenses.views.utils import get_safe_redirect_url

from ..filters import RECURRING_FILTERS, apply_filter_config
from ..forms import RecurringTransactionForm
from ..models import RecurringTransaction
from ..posthog_utils import ph_capture
from ..recurring_utils import calculate_recurring_equivalents
from finance_tracker.plans import get_limit
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
    process_user_recurring_transactions,
)


class RecurringTransactionListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = RecurringTransaction
    template_name = 'expenses/recurring_transaction_list.html'
    htmx_template_name = 'expenses/partials/_recurring_transaction_list.html'
    context_object_name = 'recurring_transactions'
    filter_expenses_only = False

    def get_queryset(self):
        if not self.request.user.is_authenticated:
            return RecurringTransaction.objects.none()
        base_qs = RecurringTransaction.objects.filter(user=self.request.user).select_related(
            'account',
            'from_account',
            'to_account',
            'loan',
        )
        if self.filter_expenses_only:
            base_qs = base_qs.filter(transaction_type__in=['EXPENSE', 'TRANSFER', 'LOAN', 'CAPITAL'])
        base_qs = base_qs.order_by('-created_at')

        queryset, self.applied_state = apply_filter_config(base_qs, self.request, RECURRING_FILTERS)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_transactions = self.object_list
        today = date.today()
        
        # Split into Active and Cancelled
        # We sort active subs by creation date to determine which ones are locked
        active_subs = [t for t in all_transactions if t.is_active]
        active_subs.sort(key=lambda x: (x.created_at or today, x.id or 0)) # Fallback to ID if created_at is null
        
        profile = self.request.user.profile
        limit = get_limit(profile.active_tier, 'recurring_transactions')
        if limit == -1:
            for sub in active_subs:
                sub.is_locked = False
        else:
            for idx, sub in enumerate(active_subs):
                sub.is_locked = idx >= limit
            
        cancelled_subs = [t for t in all_transactions if not t.is_active]
        
        # Calculate Totals (Monthly & Yearly) - exclude transfers since they aren't costs
        total_monthly = 0
        total_yearly = 0
        
        for sub in active_subs:
            if sub.transaction_type in ('TRANSFER', 'INCOME'):
                continue
            total_monthly += sub.monthly_equivalent
            total_yearly += sub.yearly_equivalent

        # Identify "Renewing Soon" (This Month)
        renewing_soon = []
        renewals_count = 0
        
        for sub in active_subs:
            next_date = sub.next_due_date
            if next_date:
                sub.annotated_next_date = next_date
                sub.annotated_days_until = (next_date - today).days
            else:
                sub.annotated_next_date = None
                sub.annotated_days_until = 999999
            
            # Determine urgency
            is_renewing = False
            if sub.annotated_next_date and sub.transaction_type in ('EXPENSE', 'TRANSFER', 'CAPITAL'):
                if sub.annotated_days_until <= 30: # Show mostly anything coming up soon
                     is_renewing = True
            
            if is_renewing:
                renewing_soon.append(sub)
                renewals_count += 1
            
        renewing_soon.sort(key=lambda x: x.annotated_days_until)

        # Sort active and cancelled subs
        applied_state = getattr(self, 'applied_state', {})
        sort_by = applied_state.get('sort', 'next_date_asc')
        if sort_by == 'amount_desc':
            active_subs.sort(key=lambda x: x.base_amount or x.amount, reverse=True)
            cancelled_subs.sort(key=lambda x: x.base_amount or x.amount, reverse=True)
        elif sort_by == 'amount_asc':
            active_subs.sort(key=lambda x: x.base_amount or x.amount)
            cancelled_subs.sort(key=lambda x: x.base_amount or x.amount)
        elif sort_by == 'name_asc':
            active_subs.sort(key=lambda x: (x.description or '').lower())
            cancelled_subs.sort(key=lambda x: (x.description or '').lower())
        else: # next_date_asc
            active_subs.sort(key=lambda x: x.annotated_days_until)
            cancelled_subs.sort(key=lambda x: (x.next_due_date or date.max))

        applied_filters = applied_state.get('filters', {})
        selected_status = applied_filters.get('status', [None])[0] if 'status' in applied_filters else None

        context['filter_config'] = RECURRING_FILTERS
        context['applied_state'] = applied_state
        context['current_status'] = selected_status

        context.update({
            'active_subs': active_subs,
            'cancelled_subs': cancelled_subs,
            'renewing_soon': renewing_soon,
            'renewals_count': renewals_count,
            'total_monthly_cost': total_monthly,
            'total_yearly_cost': total_yearly,
            'total_daily_cost': total_yearly / 365 if total_yearly else 0,
        })
        
        # Nudge context for upgrade banner (use is_plus/is_pro to respect subscription expiry)
        active_count = len(active_subs)
        
        if limit != -1:
            if profile.active_tier == 'PLUS':
                upgrade_tier = 'PRO'
            else:
                upgrade_tier = 'PLUS'
            context['nudge_current'] = active_count
            context['nudge_limit'] = limit
            context['nudge_feature_name'] = 'recurring transactions'
            context['nudge_upgrade_tier'] = upgrade_tier
            context['nudge_at_limit'] = active_count >= limit
            # Free users: always show nudge (they have 0 limit)
            # Plus users: show when >= 60% of limit
            if limit == 0:
                context['show_nudge'] = True
            else:
                context['show_nudge'] = active_count >= max(1, int(limit * 0.6))
        
        context['is_limit_reached'] = not profile.can_add_recurring()
        context['current_limit'] = float('inf') if limit == -1 else limit
        
        return context



class RecurringTransactionCreateView(LoginRequiredMixin, CreateView):
    model = RecurringTransaction
    form_class = RecurringTransactionForm
    template_name = 'expenses/recurring_transaction_form.html'
    success_url = reverse_lazy('recurring-list')
    
    def get_initial(self):
        initial = super().get_initial()
        description = self.request.GET.get('description')
        amount = self.request.GET.get('amount')
        if description:
            initial['description'] = description
        if amount:
            initial['amount'] = amount
        return initial

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.profile.can_add_recurring():
            messages.error(request, _("Subscription limit reached. Please upgrade."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)
    
    def form_valid(self, form):
        form.instance.user = self.request.user
        # Prevent exact duplicate recurring transactions
        dup = RecurringTransaction.objects.filter(
            user=self.request.user,
            transaction_type=form.instance.transaction_type,
            amount=form.instance.amount,
            currency=form.instance.currency,
            description=form.instance.description,
            frequency=form.instance.frequency,
            start_date=form.instance.start_date,
            account=form.instance.account,
            from_account=form.instance.from_account,
            to_account=form.instance.to_account,
            category=form.instance.category,
            source=form.instance.source,
            loan=form.instance.loan,
            is_active=True,
        ).exists()

        if dup:
            messages.warning(self.request, _("A recurring transaction with the same details already exists."))
            return self.form_invalid(form)

        messages.success(self.request, _("Recurring transaction created successfully!"))
        response = super().form_valid(form)
        process_user_recurring_transactions(self.request.user, force=True)
        ph_capture(self.request.user, 'recurring_created', {'transaction_type': self.object.transaction_type, 'frequency': self.object.frequency, 'amount': str(self.object.amount)})
        return response
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, super().get_success_url())
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class RecurringTransactionUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = RecurringTransaction
    form_class = RecurringTransactionForm
    template_name = 'expenses/recurring_transaction_form.html'
    success_url = reverse_lazy('recurring-list')
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        obj = self.get_object()
        if request.user.profile.is_recurring_locked(obj):
            messages.error(request, _("This subscription is locked."))
            return redirect('recurring-list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, _("Recurring transaction updated successfully!"))
        response = super().form_valid(form)
        process_user_recurring_transactions(self.request.user, force=True)
        ph_capture(self.request.user, 'recurring_updated', {'frequency': self.object.frequency})
        return response

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, super().get_success_url())
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

    def get_queryset(self):
        # We need to import RecurringTransaction if not already in scope, but it's in models.
        # This view already defines model=RecurringTransaction, so it's in scope.
        if not self.request.user.is_authenticated:
            return super().get_queryset().none()
        return super().get_queryset().filter(user=self.request.user)

class RecurringTransactionDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = RecurringTransaction
    success_url = reverse_lazy('recurring-list')
    def get_queryset(self): 
        if not self.request.user.is_authenticated:
            return RecurringTransaction.objects.none()
        return RecurringTransaction.objects.filter(user=self.request.user)

    def form_valid(self, form):
        # Calculate savings in base currency
        obj = self.object
        yearly_saving = obj.yearly_equivalent
            
        currency = '₹'
        if hasattr(self.request.user, 'profile'):
            currency = self.request.user.profile.currency
            
        messages.success(self.request, _("You just saved %(currency)s%(amount)s/year 🎉") % {'currency': currency, 'amount': f"{yearly_saving:,.0f}"})
        ph_capture(self.request.user, 'recurring_deleted', {})
        return super().form_valid(form)
