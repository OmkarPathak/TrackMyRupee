import calendar
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from expenses.views.utils import get_safe_redirect_url

from ..forms import IncomeForm
from ..models import Income, RecurringTransaction
from .mixins import RecurringTransactionMixin, UUIDOrIntLookupMixin


class IncomeListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Income
    template_name = 'expenses/income_list.html'
    context_object_name = 'incomes'
    paginate_by = 20

    def get_queryset(self):
        queryset = Income.objects.filter(user=self.request.user).select_related('account').order_by('-date')
        
        # Date Filter
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        source = self.request.GET.get('source')
        source_type = self.request.GET.get('source_type')
        income_group = self.request.GET.get('income_group')

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
            if not source and not source_type and not income_group:
                queryset = queryset.filter(date__gte=default_from, date__lte=default_to)
            self.date_from = default_from
            self.date_to = default_to

        # Source Filter
        if source:
            queryset = queryset.filter(source__icontains=source)
            
        # Source Type Filter
        if source_type:
            queryset = queryset.filter(source_type=source_type)
            
        # Income Group Filter
        if income_group:
            if income_group == 'EARNED':
                queryset = queryset.filter(source_type__in=['Salary', 'Freelance / Consulting', 'Business'])
            elif income_group == 'PASSIVE':
                queryset = queryset.filter(source_type__in=['Investment Returns', 'Rental Income'])
            elif income_group == 'ONE_OFF':
                queryset = queryset.filter(source_type__in=['Cashback & Rewards', 'Refund / Reimbursement', 'Other'])
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from ..models import CURRENCY_CHOICES, Account
        context['currency_choices'] = CURRENCY_CHOICES
        context['accounts'] = Account.objects.filter(user=self.request.user, is_active=True)
        
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
        
        # Calculate sums by group type
        context['earned_total'] = filtered_queryset.filter(
            source_type__in=['Salary', 'Freelance / Consulting', 'Business']
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0.00')
        
        context['passive_total'] = filtered_queryset.filter(
            source_type__in=['Investment Returns', 'Rental Income']
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0.00')
        
        context['one_off_total'] = filtered_queryset.filter(
            source_type__in=['Cashback & Rewards', 'Refund / Reimbursement', 'Other']
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0.00')

        # Calculate monthly earned income for the last 6 months (chronological)
        current_date = timezone.now().date()
        months = []
        y, m = current_date.year, current_date.month
        for _ in range(6):
            months.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        months.reverse()
        
        start_year, start_month = months[0]
        end_year, end_month = months[-1]
        start_dt = date(start_year, start_month, 1)
        end_dt = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])

        monthly_totals_qs = Income.objects.filter(
            user=self.request.user,
            source_type__in=['Salary', 'Freelance / Consulting', 'Business'],
            date__gte=start_dt,
            date__lte=end_dt
        ).annotate(
            month_trunc=TruncMonth('date')
        ).values('month_trunc').annotate(
            total=Sum('base_amount')
        )
        
        monthly_totals_map = {}
        for item in monthly_totals_qs:
            dt = item['month_trunc'].date() if hasattr(item['month_trunc'], 'date') else item['month_trunc']
            monthly_totals_map[(dt.year, dt.month)] = item['total'] or Decimal('0.00')

        sparkline_data = []
        for year, month in months:
            total = monthly_totals_map.get((year, month), Decimal('0.00'))
            month_name = calendar.month_name[month][:3]
            sparkline_data.append({
                'month_name': f"{month_name} '{str(year)[2:]}",
                'amount': float(total)
            })
            
        width = 120
        height = 30
        max_amount = max(item['amount'] for item in sparkline_data)
        min_amount = min(item['amount'] for item in sparkline_data)
        
        points = []
        for idx, item in enumerate(sparkline_data):
            x = idx * (width / 5.0)
            if max_amount == min_amount:
                y = height / 2.0
            else:
                y = height - ((item['amount'] - min_amount) / (max_amount - min_amount)) * (height - 4) - 2
            points.append((x, y))
            item['x'] = x
            item['y'] = y
            
        path_d = ""
        if points:
            path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in points)
            
        context['sparkline_path'] = path_d
        context['sparkline_data'] = sparkline_data

        context['filter_form'] = {
            'date_from': getattr(self, 'date_from', ''),
            'date_to': getattr(self, 'date_to', ''),
            'source': self.request.GET.get('source', ''),
            'source_type': self.request.GET.get('source_type', ''),
            'income_group': self.request.GET.get('income_group', ''),
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
        try:
            response = super().form_valid(form)
        except IntegrityError:
            messages.error(self.request, _("This income entry already exists for the same date, amount, currency, and source."))
            return self.form_invalid(form)
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to save income because currency conversion failed or data is invalid."))
            return self.form_invalid(form)

        messages.success(self.request, _("Income record added successfully!"))
        
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

    def get_success_url(self):
        next_url = self.request.POST.get('next') or self.request.GET.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, super().get_success_url())
        return super().get_success_url()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class IncomeUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
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
            return get_safe_redirect_url(self.request, next_url, super().get_success_url())
        return super().get_success_url()

    def form_valid(self, form):
        from django.db import IntegrityError
        try:
            response = super().form_valid(form)
            messages.success(self.request, _("Income record updated successfully!"))
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
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to update income because currency conversion failed or data is invalid."))
            return self.form_invalid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['next_url'] = self.request.POST.get('next') or self.request.GET.get('next') or ''
        return context

class IncomeDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Income
    def get_queryset(self): return Income.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Income record deleted successfully."))
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse_lazy('income-list')
