import calendar
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Case, Count, DecimalField, F, Sum, Value, When
from django.db.models.functions import TruncMonth
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from expenses.views.utils import get_safe_redirect_url

from ..forms import IncomeForm
from ..models import INCOME_GROUP_TYPES, Income, RecurringTransaction
from ..posthog_utils import ph_capture
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
)
from ..filters import INCOME_FILTERS, apply_filter_config
from .utils import apply_date_filters


class IncomeListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Income
    template_name = 'expenses/income_list.html'
    htmx_template_name = 'expenses/partials/_income_list.html'
    context_object_name = 'incomes'
    paginate_by = 20

    def get_queryset(self):
        base_qs = Income.objects.filter(user=self.request.user).select_related('account')
        queryset, self.applied_state = apply_filter_config(base_qs, self.request, INCOME_FILTERS)
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_config'] = INCOME_FILTERS
        context['applied_state'] = getattr(self, 'applied_state', {})

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
        
        # Calculate stats and totals by group type for the filtered queryset in a single DB query
        filtered_queryset = self.object_list
        earned_types = INCOME_GROUP_TYPES['EARNED']
        passive_types = INCOME_GROUP_TYPES['PASSIVE']
        one_off_types = INCOME_GROUP_TYPES['ONE_OFF']

        stats = filtered_queryset.aggregate(
            count=Count('id'),
            total=Sum('base_amount'),
            earned=Sum(Case(When(source_type__in=earned_types, then=F('base_amount')), default=Value(Decimal('0.00')), output_field=DecimalField())),
            passive=Sum(Case(When(source_type__in=passive_types, then=F('base_amount')), default=Value(Decimal('0.00')), output_field=DecimalField())),
            one_off=Sum(Case(When(source_type__in=one_off_types, then=F('base_amount')), default=Value(Decimal('0.00')), output_field=DecimalField())),
        )
        context['filtered_count'] = stats['count']
        context['filtered_amount'] = stats['total'] or Decimal('0.00')
        context['earned_total'] = stats['earned'] or Decimal('0.00')
        context['passive_total'] = stats['passive'] or Decimal('0.00')
        context['one_off_total'] = stats['one_off'] or Decimal('0.00')

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
            source_type__in=INCOME_GROUP_TYPES['EARNED'],
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
            
        # Set filter context values
        context['source_types'] = self.request.GET.getlist('source_type')
        context['income_groups'] = self.request.GET.getlist('income_group')
        context['time_period'] = self.request.GET.get('time_period', 'this_month')
        context['start_date'] = self.request.GET.get('start_date', '')
        context['end_date'] = self.request.GET.get('end_date', '')
        context['search_query'] = self.request.GET.get('search', '')
        
        sort_by = self.request.GET.get('sort', 'date_desc')
        context['sort_by'] = sort_by
        context['current_sort'] = sort_by
        
        # Calculate active filters count
        active_filters = 0
        if context['search_query']:
            active_filters += 1
        if context['time_period'] != 'this_month':
            active_filters += 1
        if context['source_types']:
            active_filters += 1
        if context['income_groups']:
            active_filters += 1
        if sort_by and sort_by != 'date_desc':
            active_filters += 1
        context['active_filters_count'] = active_filters
            
        context['sparkline_path'] = path_d
        context['sparkline_data'] = sparkline_data

        context['filter_form'] = {
            'date_from': getattr(self, 'date_from', ''),
            'date_to': getattr(self, 'date_to', ''),
            'source_types': self.request.GET.getlist('source_type'),
            'income_groups': self.request.GET.getlist('income_group'),
        }
        return context

def _create_recurring_from_income(request, form):
    """
    Helper to create an active recurring transaction from an income form if requested.
    Emits appropriate info message whether newly created or already existing.
    """
    if not form.cleaned_data.get('add_to_recurring'):
        return None

    existing_rt = RecurringTransaction.objects.filter(
        user=request.user,
        transaction_type='INCOME',
        source=form.instance.source,
        is_active=True
    ).exists()

    if not existing_rt:
        rt = RecurringTransaction.objects.create(
            user=request.user,
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
        messages.info(request, _("A recurring income subscription has also been created."))
        return rt
    else:
        messages.info(request, _("A recurring subscription for this source already exists."))
        return None


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

        ph_capture(self.request.user, 'income_created', {
            'amount': str(self.object.amount),
            'currency': self.object.currency,
            'source_type': self.object.source_type or '',
            'has_account': bool(self.object.account_id),
        })
        messages.success(self.request, _("Income record added successfully!"))
        
        _create_recurring_from_income(self.request, form)
            
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
        try:
            response = super().form_valid(form)
            ph_capture(self.request.user, 'income_updated', {
                'amount': str(self.object.amount),
                'currency': self.object.currency,
            })
            messages.success(self.request, _("Income record updated successfully!"))
            _create_recurring_from_income(self.request, form)
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
        ph_capture(self.request.user, 'income_deleted', {})
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse_lazy('income-list')
