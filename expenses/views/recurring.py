from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from ..forms import RecurringTransactionForm
from ..models import RecurringTransaction
from .mixins import RecurringTransactionMixin


class RecurringTransactionListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = RecurringTransaction
    template_name = 'expenses/recurring_transaction_list.html'
    context_object_name = 'recurring_transactions'
    filter_expenses_only = True

    def get_queryset(self):
        queryset = RecurringTransaction.objects.filter(user=self.request.user)
        if self.filter_expenses_only:
            queryset = queryset.filter(transaction_type__in=['EXPENSE', 'TRANSFER'])
        queryset = queryset.order_by('-created_at')
        
        # Filter by Category
        categories = self.request.GET.getlist('category')
        if categories:
            queryset = queryset.filter(category__in=categories)
            
        return queryset
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_transactions = self.object_list
        today = date.today()
        
        # Categories for filter
        user_transactions = RecurringTransaction.objects.filter(user=self.request.user)
        categories = user_transactions.values_list('category', flat=True).distinct().order_by('category')
        # Filter out None/Empty if any
        categories = [c for c in categories if c]
        
        context['categories'] = categories
        context['selected_categories'] = self.request.GET.getlist('category')
        
        # Split into Active and Cancelled
        # We sort active subs by creation date to determine which ones are locked
        active_subs = [t for t in all_transactions if t.is_active]
        active_subs.sort(key=lambda x: x.created_at or x.id) # Fallback to ID if created_at is null
        
        profile = self.request.user.profile
        for sub in active_subs:
            sub.is_locked = profile.is_recurring_locked(sub)
            
        cancelled_subs = [t for t in all_transactions if not t.is_active]
        
        # Calculate Totals (Monthly & Yearly) - exclude transfers since they aren't costs
        total_monthly = 0
        total_yearly = 0
        
        for sub in active_subs:
            if sub.transaction_type == 'TRANSFER':
                continue
            amount = sub.base_amount
            if sub.frequency == 'DAILY':
                total_monthly += amount * 30
                total_yearly += amount * 365
            elif sub.frequency == 'WEEKLY':
                total_monthly += amount * 4
                total_yearly += amount * 52
            elif sub.frequency == 'MONTHLY':
                total_monthly += amount
                total_yearly += amount * 12
            elif sub.frequency == 'YEARLY':
                total_monthly += amount / 12
                total_yearly += amount

        # Identify "Renewing Soon" (This Month)
        renewing_soon = []
        renewals_count = 0
        
        # Helper to find next date relative to today
        for sub in active_subs:
            # Calculate next occurrence
            next_date = sub.start_date
            
            # For simpler logic, we reset the year/month to current to check basic interval
            # But for accurate "days until", we need better logic:
            
            if sub.frequency == 'DAILY':
                next_date = today + timedelta(days=1)
            elif sub.frequency == 'WEEKLY':
                # Find days ahead
                days_ahead = (sub.start_date.weekday() - today.weekday()) % 7
                if days_ahead == 0 and today > sub.start_date: # if today is the day, but older start
                     days_ahead = 7
                elif days_ahead == 0 and today == sub.start_date: # exact match today
                     days_ahead = 0
                else: 
                     # If start_date was future, we wait. If past, we find next.
                     # Simplified: just next occurrence of that weekday
                     if days_ahead <= 0: days_ahead += 7
                
                # Correction: Standard logic to find next matching weekday
                days_ahead = (sub.start_date.weekday() - today.weekday()) 
                if days_ahead <= 0: # Target day already happened this week or is today
                    days_ahead += 7
                next_date = today + timedelta(days=days_ahead)
                
            elif sub.frequency == 'MONTHLY':
                # Occurs on sub.start_date.day every month
                # If today.day > start_date.day, it's next month.
                # If today.day <= start_date.day, it's this month.
                try:
                    if today.day > sub.start_date.day:
                        # Next month
                        month = today.month + 1
                        year = today.year
                        if month > 12:
                            month = 1
                            year += 1
                        next_date = date(year, month, sub.start_date.day)
                    else:
                        # This month
                        next_date = date(today.year, today.month, sub.start_date.day)
                except ValueError: 
                    # Handle end of month issues (e.g. 31st) - simplified to 1st of next-next month
                    next_date = (today.replace(day=1) + timedelta(days=32)).replace(day=1)

            elif sub.frequency == 'YEARLY':
                try:
                    this_year_date = date(today.year, sub.start_date.month, sub.start_date.day)
                    if today > this_year_date:
                        next_date = date(today.year + 1, sub.start_date.month, sub.start_date.day)
                    else:
                        next_date = this_year_date
                except ValueError:
                    next_date = date(today.year, 2, 28)

            # Annotate object
            sub.annotated_next_date = next_date
            sub.annotated_days_until = (next_date - today).days
            
            # Determine urgency
            is_renewing = False
            if sub.transaction_type in ('EXPENSE', 'TRANSFER'):
                if sub.annotated_days_until <= 30: # Show mostly anything coming up soon
                     is_renewing = True
            
            if is_renewing:
                renewing_soon.append(sub)
                renewals_count += 1
            
            # Sort renewing soon by days until
            renewing_soon.sort(key=lambda x: x.annotated_days_until)

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
        profile = self.request.user.profile
        active_count = RecurringTransaction.objects.filter(user=self.request.user, is_active=True).count()
        
        from finance_tracker.plans import get_limit
        limit = get_limit(profile.active_tier, 'recurring_transactions')
        
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

class RecurringTransactionManageView(RecurringTransactionListView):
    template_name = 'expenses/recurring_transaction_manage.html'
    filter_expenses_only = False

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
        if not request.user.profile.can_add_recurring():
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
            is_active=True,
        ).exists()

        if dup:
            messages.warning(self.request, _("A recurring transaction with the same details already exists."))
            return self.form_invalid(form)

        messages.success(self.request, _("Recurring transaction created successfully!"))
        return super().form_valid(form)
    
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class RecurringTransactionUpdateView(LoginRequiredMixin, UpdateView):
    model = RecurringTransaction
    form_class = RecurringTransactionForm
    template_name = 'expenses/recurring_transaction_form.html'
    success_url = reverse_lazy('recurring-list')
    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if request.user.profile.is_recurring_locked(obj):
            messages.error(request, _("This subscription is locked."))
            return redirect('recurring-list')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, _("Recurring transaction updated successfully!"))
        return super().form_valid(form)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

    def get_queryset(self):
        # We need to import RecurringTransaction if not already in scope, but it's in models.
        # This view already defines model=RecurringTransaction, so it's in scope.
        return super().get_queryset().filter(user=self.request.user)

class RecurringTransactionDeleteView(LoginRequiredMixin, DeleteView):
    model = RecurringTransaction
    success_url = reverse_lazy('recurring-list')
    def get_queryset(self): return RecurringTransaction.objects.filter(user=self.request.user)

    def form_valid(self, form):
        # Calculate savings
        from django.contrib import messages
        from django.utils.translation import gettext as _
        obj = self.object
        amount = obj.amount
        if obj.frequency == 'DAILY':
            yearly_saving = amount * 365
        elif obj.frequency == 'WEEKLY':
            yearly_saving = amount * 52
        elif obj.frequency == 'MONTHLY':
            yearly_saving = amount * 12
        else: # YEARLY
            yearly_saving = amount
            
        currency = '₹'
        if hasattr(self.request.user, 'profile'):
            currency = self.request.user.profile.currency
            
        messages.success(self.request, _("You just saved %(currency)s%(amount)s/year 🎉") % {'currency': currency, 'amount': f"{yearly_saving:,.0f}"})
        return super().form_valid(form)
