
import calendar
from datetime import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, ListView, View

from expenses.views.utils import get_safe_redirect_url

from ..forms import CapitalEventForm
from ..models import CapitalEvent, Expense, Loan
from .mixins import HtmxPartialTemplateMixin, UUIDOrIntLookupMixin
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed


class CapitalEventListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    model = CapitalEvent
    template_name = 'expenses/capital_event_list.html'
    htmx_template_name = 'expenses/partials/_capital_event_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def get_queryset(self):
        qs = CapitalEvent.objects.filter(user=self.request.user).select_related('account', 'linked_loan').order_by('-date')
        
        # Filtering
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_subtypes = self.request.GET.getlist('subtype')
        search_query = self.request.GET.get('search')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

        # Remove empty strings from lists
        selected_years = [y for y in selected_years if y]
        selected_months = [m for m in selected_months if m]
        selected_subtypes = [s for s in selected_subtypes if s]
        
        # Date Range Logic (Precedence over Year/Month)
        if start_date or end_date:
            if start_date:
                qs = qs.filter(date__gte=start_date)
            if end_date:
                qs = qs.filter(date__lte=end_date)
        else:
            has_active_filters = (
                selected_years or 
                selected_months or 
                search_query
            )
            
            if not has_active_filters:
                selected_years = [str(datetime.now().year)]
                selected_months = [str(datetime.now().month)]
            
            if selected_years:
                qs = qs.filter(date__year__in=selected_years)
            
            if selected_months:
                qs = qs.filter(date__month__in=selected_months)

        if selected_subtypes:
            qs = qs.filter(subtype__in=selected_subtypes)

        if search_query:
            qs = qs.filter(note__icontains=search_query)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        filtered_queryset = self.object_list
        ctx['filtered_count'] = filtered_queryset.count()
        ctx['filtered_amount'] = filtered_queryset.aggregate(Sum('base_amount'))['base_amount__sum'] or 0

        user_events = CapitalEvent.objects.filter(user=self.request.user)
        years_dates = user_events.dates('date', 'year', order='DESC')
        years = sorted(list(set([d.year for d in years_dates] + [datetime.now().year])), reverse=True)
        
        ctx['years'] = years
        ctx['months_list'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        ctx['start_date'] = start_date
        ctx['end_date'] = end_date
        
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_subtypes = self.request.GET.getlist('subtype')
        search_query = self.request.GET.get('search', '')

        selected_years = [y for y in selected_years if y]
        selected_months = [m for m in selected_months if m]
        selected_subtypes = [s for s in selected_subtypes if s]
        
        ctx['selected_years'] = selected_years
        ctx['selected_months'] = selected_months
        ctx['selected_subtypes'] = selected_subtypes
        ctx['search_query'] = search_query

        # Mirror default logic from get_queryset if NO date range is present
        if not (start_date or end_date):
            has_active_filters = (selected_years or selected_months or search_query)
            if not has_active_filters:
                ctx['selected_years'] = [str(datetime.now().year)]
                ctx['selected_months'] = [str(datetime.now().month)]

        ctx['subtype_choices'] = CapitalEvent.SUBTYPE_CHOICES
        return ctx


class CapitalEventCreateView(LoginRequiredMixin, View):
    template_name = 'expenses/capital_event_form.html'

    def _get_form(self, request, data=None):
        return CapitalEventForm(data, user=request.user)

    def get(self, request):
        # Support pre-fill from "convert expense" flow
        initial = {}
        expense_id = request.GET.get('from_expense')
        if expense_id:
            try:
                src = Expense.objects.get(pk=expense_id, user=request.user)
                initial = {
                    'amount': src.amount,
                    'date': src.date,
                    'currency': src.currency,
                    'account': src.account_id,
                    'note': src.description,
                    'subtype': 'other',
                }
            except Expense.DoesNotExist:
                pass
        
        if 'amount' in request.GET:
            initial['amount'] = request.GET.get('amount')
        if 'subtype' in request.GET:
            initial['subtype'] = request.GET.get('subtype')

        form = CapitalEventForm(initial=initial, user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'from_expense_id': expense_id,
            'user_loans': Loan.objects.filter(user=request.user, is_active=True),
        })

    def post(self, request):
        form = CapitalEventForm(request.POST, user=request.user)
        from_expense_id = request.POST.get('from_expense_id')
        if form.is_valid():
            with transaction.atomic():
                event = form.save(commit=False)
                event.user = request.user
                event.save()

                # Optionally delete the original expense after conversion
                if from_expense_id and request.POST.get('delete_source_expense') == '1':
                    try:
                        Expense.objects.get(pk=from_expense_id, user=request.user).delete()
                        messages.info(request, _("Original expense deleted after conversion."))
                    except Expense.DoesNotExist:
                        pass

            messages.success(request, _("Capital event recorded successfully."))
            return redirect('capital-event-list')

        return render(request, self.template_name, {
            'form': form,
            'from_expense_id': from_expense_id,
            'user_loans': Loan.objects.filter(user=request.user, is_active=True),
        })


class CapitalEventUpdateView(LoginRequiredMixin, View):
    template_name = 'expenses/capital_event_form.html'

    def get_object(self, pk, user):
        return get_object_by_uuid_or_pk(CapitalEvent, pk, user=user)

    def get(self, request, pk):
        event = self.get_object(pk, request.user)
        redirect_response = redirect_to_uuid_url_if_needed(request, event)
        if redirect_response:
            return redirect_response
        form = CapitalEventForm(instance=event, user=request.user)
        next_url = request.GET.get('next', '')
        return render(request, self.template_name, {
            'form': form,
            'event': event,
            'user_loans': Loan.objects.filter(user=request.user, is_active=True),
            'next_url': next_url,
        })

    def post(self, request, pk):
        event = self.get_object(pk, request.user)
        form = CapitalEventForm(request.POST, instance=event, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                form.save()
            messages.success(request, _("Capital event updated."))
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url:
                return redirect(next_url)
            return redirect('capital-event-list')

        next_url = request.POST.get('next') or request.GET.get('next') or ''
        return render(request, self.template_name, {
            'form': form,
            'event': event,
            'user_loans': Loan.objects.filter(user=request.user, is_active=True),
            'next_url': next_url,
        })


class CapitalEventDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = CapitalEvent
    template_name = 'expenses/capital_event_confirm_delete.html'
    success_url = reverse_lazy('capital-event-list')

    def get_queryset(self):
        return CapitalEvent.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(request, _("Capital event deleted."))
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('capital-event-list'))
        return reverse_lazy('capital-event-list')


class CapitalEventConvertToExpenseView(LoginRequiredMixin, View):
    """Convert a CapitalEvent back to a regular Expense (type conversion without data loss)."""

    def post(self, request, pk):
        event = get_object_by_uuid_or_pk(CapitalEvent, pk, user=request.user)
        with transaction.atomic():
            expense = Expense(
                user=request.user,
                date=event.date,
                amount=event.amount,
                currency=event.currency,
                description=event.note or event.get_subtype_display(),
                category=event.get_subtype_display(),
                account=event.account,
            )
            expense.save()
            event.delete()
        messages.success(request, _("Capital event converted to a regular expense."))
        return redirect('expense-list')


def capital_event_loans_ajax(request):
    """AJAX endpoint: return the user's active loans as JSON for dynamic loan dropdown."""
    if not request.user.is_authenticated:
        return JsonResponse({'loans': []})
    loans = Loan.objects.filter(user=request.user, is_active=True).values('id', 'name', 'loan_type')
    return JsonResponse({'loans': list(loans)})
