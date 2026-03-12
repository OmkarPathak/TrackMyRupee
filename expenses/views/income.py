from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy, reverse
from django.contrib import messages
from django.utils.translation import gettext as _
from django.utils import timezone
from django.db.models import Sum

from ..models import Income
from ..forms import IncomeForm
from .mixins import RecurringTransactionMixin

class IncomeListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Income
    template_name = 'expenses/income_list.html'
    context_object_name = 'incomes'
    paginate_by = 20

    def get_queryset(self):
        queryset = Income.objects.filter(user=self.request.user).order_by('-date')
        
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
        if date_from or date_to:
            if date_from:
                queryset = queryset.filter(date__gte=date_from)
                self.date_from = date_from
            else:
                self.date_from = ''
            
            if date_to:
                queryset = queryset.filter(date__lte=date_to)
                self.date_to = date_to
            else:
                self.date_to = ''
        else:
            # If no explicit date range, check for year/month filters
            # If neither exist, default to current month/year for consistency with dashboard
            if not selected_years and not selected_months and not source:
                 selected_years = [str(timezone.now().year)]
                 selected_months = [str(timezone.now().month)]
            
            if selected_years:
                queryset = queryset.filter(date__year__in=selected_years)
            if selected_months:
                queryset = queryset.filter(date__month__in=selected_months)
            
            self.date_from = ''
            self.date_to = ''

        # Source Filter
        if source:
            queryset = queryset.filter(source__icontains=source)
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..models import CURRENCY_CHOICES
        context['currency_choices'] = CURRENCY_CHOICES
        
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
        return super().form_valid(form)

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

    def form_valid(self, form):
        from django.db import IntegrityError
        from django.contrib import messages
        from django.utils.translation import gettext as _
        try:
            return super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, _("This income entry already exists."))
            return self.form_invalid(form)

class IncomeDeleteView(LoginRequiredMixin, DeleteView):
    model = Income
    success_url = reverse_lazy('income-list')
    def get_queryset(self): return Income.objects.filter(user=self.request.user)
