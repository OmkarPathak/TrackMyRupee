from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from ..forms import IncomeForm
from ..models import Income, RecurringTransaction
from .mixins import RecurringTransactionMixin


class IncomeListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Income
    template_name = 'expenses/income_list.html'
    context_object_name = 'incomes'
    paginate_by = 20

    def get_queryset(self):
        queryset = Income.objects.for_user(self.request.user).order_by('-date')
        
        # Date Filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        source = self.request.GET.get('source')

        # Remove empty strings from lists
        selected_years = [y for y in selected_years if y]
        selected_months = [m for m in selected_months if m]

        # Date Range Logic (Precedence)
        # Default to current year if no filters are provided
        now = timezone.now()
        default_from = f"{now.year}-01-01"
        default_to = f"{now.year}-12-31"

        if date_from or date_to:
            self.date_from = date_from or ''
            self.date_to = date_to or ''
            if date_from:
                queryset = queryset.filter(date__gte=date_from)
            if date_to:
                queryset = queryset.filter(date__lte=date_to)
        elif selected_years or selected_months:
            if selected_years:
                queryset = queryset.filter(date__year__in=selected_years)
            if selected_months:
                queryset = queryset.filter(date__month__in=selected_months)
            self.date_from = ''
            self.date_to = ''
        else:
            # No filters at all — default to current year
            if not source:
                queryset = queryset.filter(date__gte=default_from, date__lte=default_to)
            self.date_from = default_from
            self.date_to = default_to

        # Source Filter
        if source:
            queryset = queryset.filter(source__icontains=source)
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..models import CURRENCY_CHOICES, Account
        context['currency_choices'] = CURRENCY_CHOICES
        context['accounts'] = Account.objects.filter(user=self.request.user)
        
        # Get active recurring sources and their frequencies for this user
        recurring_data = {
            rt.source: rt.frequency 
            for rt in RecurringTransaction.objects.filter(
                user=self.request.user,
                transaction_type='INCOME',
                is_active=True
            )
        }
        context['recurring_data'] = recurring_data
        
        # Calculate stats for the filtered queryset
        filtered_queryset = self.object_list
        context['filtered_count'] = filtered_queryset.count()
        context['filtered_amount'] = filtered_queryset.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        
        context['filter_form'] = {
            'date_from': getattr(self, 'date_from', ''),
            'date_to': getattr(self, 'date_to', ''),
            'source': self.request.GET.get('source', ''),
        }
        return context

class IncomeCreateView(LoginRequiredMixin, CreateView):
    model = Income
    form_class = IncomeForm
    template_name = 'expenses/income_form.html'
    success_url = reverse_lazy('income-list')
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs
    def form_valid(self, form):
        form.instance.user = self.request.user
        if hasattr(self.request.user, 'profile') and self.request.user.profile.family:
            form.instance.family = self.request.user.profile.family
        
        response = super().form_valid(form)
        
        if form.cleaned_data.get('add_to_recurring'):
            existing_rt = RecurringTransaction.objects.filter(
                user=self.request.user,
                transaction_type='INCOME',
                source=form.instance.source,
                is_active=True
            ).exists()
            
            if not existing_rt:
                RecurringTransaction.objects.create(
                    user=self.request.user,
                    transaction_type='INCOME',
                    amount=form.instance.amount,
                    currency=form.instance.currency,
                    account=form.instance.account,
                    source=form.instance.source,
                    frequency=form.cleaned_data.get('frequency'),
                    start_date=form.instance.date,
                    last_processed_date=form.instance.date,
                    description=form.instance.description,
                    family=form.instance.family,
                    is_shared=form.instance.is_shared,
                    is_active=True
                )
                messages.info(self.request, _("A recurring income subscription has also been created."))
            else:
                messages.info(self.request, _("A recurring subscription for this source already exists."))
            
        return response

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class IncomeUpdateView(LoginRequiredMixin, UpdateView):
    model = Income
    form_class = IncomeForm
    template_name = 'expenses/income_form.html'
    success_url = reverse_lazy('income-list')
    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs(); kwargs['user'] = self.request.user
        return kwargs
    def get_queryset(self): return Income.objects.filter(user=self.request.user)

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def form_valid(self, form):
        from django.db import IntegrityError
        try:
            response = super().form_valid(form)
            if form.cleaned_data.get('add_to_recurring'):
                existing_rt = RecurringTransaction.objects.filter(
                    user=self.request.user,
                    transaction_type='INCOME',
                    source=form.instance.source,
                    is_active=True
                ).exists()
                
                if not existing_rt:
                    RecurringTransaction.objects.create(
                        user=self.request.user,
                        transaction_type='INCOME',
                        amount=form.instance.amount,
                        currency=form.instance.currency,
                        account=form.instance.account,
                        source=form.instance.source,
                        frequency=form.cleaned_data.get('frequency'),
                        start_date=form.instance.date,
                        last_processed_date=form.instance.date,
                        description=form.instance.description,
                        is_active=True
                    )
                    messages.info(self.request, _("A recurring income subscription has also been created."))
                else:
                    messages.info(self.request, _("A recurring subscription for this source already exists."))
            return response
        except IntegrityError:
            messages.error(self.request, _("This income entry already exists."))
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class IncomeDeleteView(LoginRequiredMixin, DeleteView):
    model = Income
    def get_queryset(self): return Income.objects.filter(user=self.request.user)
    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse_lazy('income-list')
