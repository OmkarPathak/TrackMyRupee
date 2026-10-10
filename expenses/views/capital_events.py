
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, ListView, View

from expenses.views.utils import get_safe_redirect_url

from ..filters.definitions import CAPITAL_EVENT_FILTERS
from ..filters.engine import apply_filter_config
from ..forms import CapitalEventForm
from ..models import CapitalEvent, Expense, Loan
from ..posthog_utils import ph_capture
from .mixins import HtmxPartialTemplateMixin, UUIDOrIntLookupMixin
from .utils import (
    apply_date_filters,
    get_object_by_uuid_or_pk,
    redirect_to_uuid_url_if_needed,
)


def _find_source_expense(user, raw_id):
    """The user's expense behind a "?from_expense=" link (numeric id or uuid), else None.

    Junk values from a hand-edited URL must not crash the page.
    """
    if not raw_id:
        return None
    try:
        return get_object_by_uuid_or_pk(Expense, raw_id, user=user)
    except (Http404, ValueError, ValidationError):
        return None


class CapitalEventListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    model = CapitalEvent
    template_name = 'expenses/capital_event_list.html'
    htmx_template_name = 'expenses/partials/_capital_event_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def get_queryset(self):
        base_qs = CapitalEvent.objects.filter(user=self.request.user).select_related('account', 'linked_loan')
        queryset, self.applied_state = apply_filter_config(base_qs, self.request, CAPITAL_EVENT_FILTERS)
        return queryset

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        
        filtered_queryset = self.object_list
        stats = filtered_queryset.aggregate(count=Count('id'), total=Sum('base_amount'))
        ctx['filtered_count'] = stats['count']
        ctx['filtered_amount'] = stats['total'] or 0

        applied_state = getattr(self, 'applied_state', {})
        ctx['filter_config'] = CAPITAL_EVENT_FILTERS
        ctx['applied_state'] = applied_state


        time_period = applied_state.get('time_period') or self.request.GET.get('time_period', 'this_month')
        start_date = applied_state.get('start_date') or self.request.GET.get('start_date', '')
        end_date = applied_state.get('end_date') or self.request.GET.get('end_date', '')
        search_query = applied_state.get('search') or self.request.GET.get('search', '')

        selected_filters = applied_state.get('filters', {})
        selected_subtypes = selected_filters.get('subtype')
        if selected_subtypes is None:
            raw_subtypes = self.request.GET.getlist('subtype')
            if not raw_subtypes and self.request.GET.get('subtype'):
                raw_subtypes = [self.request.GET.get('subtype')]
            selected_subtypes = [s for s in raw_subtypes if s]

        ctx['time_period'] = time_period
        ctx['start_date'] = start_date or ''
        ctx['end_date'] = end_date or ''
        ctx['search_query'] = search_query
        ctx['selected_subtypes'] = selected_subtypes
        ctx['selected_subtype'] = selected_subtypes[0] if selected_subtypes else ''
        ctx['subtype_choices'] = CapitalEvent.SUBTYPE_CHOICES

        active_filters = len(selected_filters)
        if search_query:
            active_filters += 1
        if time_period != 'this_month':
            active_filters += 1
        ctx['active_filters_count'] = active_filters

        return ctx


class CapitalEventCreateView(LoginRequiredMixin, View):
    template_name = 'expenses/capital_event_form.html'

    def _get_form(self, request, data=None):
        return CapitalEventForm(data, user=request.user)

    def get(self, request):
        # Support pre-fill from "convert expense" flow
        initial = {}
        expense_id = request.GET.get('from_expense')
        src = _find_source_expense(request.user, expense_id)
        if src:
            initial = {
                'amount': src.amount,
                'date': src.date,
                'currency': src.currency,
                'account': src.account_id,
                'note': src.description,
                'subtype': 'other',
            }
        
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
            try:
                with transaction.atomic():
                    event = form.save(commit=False)
                    event.user = request.user
                    event.save()

                    # Optionally delete the original expense after conversion
                    if from_expense_id and request.POST.get('delete_source_expense') == '1':
                        source_expense = _find_source_expense(request.user, from_expense_id)
                        if source_expense:
                            source_expense.delete()
                            messages.info(request, _("Original expense deleted after conversion."))

                messages.success(request, _("Capital event recorded successfully."))
                ph_capture(request.user, 'capital_event_created', {'subtype': getattr(event, 'subtype', ''), 'amount': str(event.amount)})
                return redirect('capital-event-list')
            except (RuntimeError, ValidationError):
                messages.error(request, _("Unable to save capital event because currency conversion failed or data is invalid."))
                return render(request, self.template_name, {
                    'form': form,
                    'from_expense_id': from_expense_id,
                    'user_loans': Loan.objects.filter(user=request.user, is_active=True),
                })

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
            try:
                with transaction.atomic():
                    form.save()
                messages.success(request, _("Capital event updated."))
                ph_capture(request.user, 'capital_event_updated', {})
                next_url = request.POST.get('next') or request.GET.get('next')
                return redirect(get_safe_redirect_url(request, next_url, reverse_lazy('capital-event-list')))
            except (RuntimeError, ValidationError):
                messages.error(request, _("Unable to update capital event because currency conversion failed or data is invalid."))
                next_url = request.POST.get('next') or request.GET.get('next') or ''
                return render(request, self.template_name, {
                    'form': form,
                    'event': event,
                    'user_loans': Loan.objects.filter(user=request.user, is_active=True),
                    'next_url': next_url,
                })

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

    def form_valid(self, form):
        # Django 4+ deletes through form_valid on POST; a custom delete() is never called.
        messages.success(self.request, _("Capital event deleted."))
        ph_capture(self.request.user, 'capital_event_deleted', {})
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('capital-event-list'))
        return reverse_lazy('capital-event-list')


class CapitalEventConvertToExpenseView(LoginRequiredMixin, View):
    """Convert a CapitalEvent back to a regular Expense (type conversion without data loss)."""

    def post(self, request, pk):
        event = get_object_by_uuid_or_pk(CapitalEvent, pk, user=request.user)
        if not (event.account_id and event.include_in_net_worth):
            # An expense must always be charged to an account; this event never moved one.
            messages.error(request, _("Choose an account and turn on 'Include in Cash Flow / Net Worth' for this event first, so the expense is charged to an account."))
            return redirect('capital-event-list')
        try:
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
            ph_capture(request.user, 'capital_event_converted_to_expense', {})
            return redirect('expense-list')
        except (RuntimeError, ValidationError):
            messages.error(request, _("Unable to convert capital event because currency conversion failed or data is invalid."))
            next_url = request.POST.get('next') or request.GET.get('next') or request.META.get('HTTP_REFERER')
            if next_url:
                return redirect(get_safe_redirect_url(request, next_url, reverse_lazy('capital-event-list')))
            return redirect('capital-event-list')


def capital_event_loans_ajax(request):
    """AJAX endpoint: return the user's active loans as JSON for dynamic loan dropdown."""
    if not request.user.is_authenticated:
        return JsonResponse({'loans': []})
    loans = Loan.objects.filter(user=request.user, is_active=True).values('id', 'name', 'loan_type')
    return JsonResponse({'loans': list(loans)})
