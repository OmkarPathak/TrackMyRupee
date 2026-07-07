
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, ListView, View

from expenses.views.utils import get_safe_redirect_url

from ..forms import CapitalEventForm
from ..models import CapitalEvent, Expense, Loan
from .mixins import UUIDOrIntLookupMixin
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed


class CapitalEventListView(LoginRequiredMixin, ListView):
    model = CapitalEvent
    template_name = 'expenses/capital_event_list.html'
    context_object_name = 'events'
    paginate_by = 20

    def get_queryset(self):
        qs = CapitalEvent.objects.filter(user=self.request.user).select_related('account', 'linked_loan')
        subtype = self.request.GET.get('subtype')
        if subtype:
            qs = qs.filter(subtype=subtype)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['subtype_choices'] = CapitalEvent.SUBTYPE_CHOICES
        ctx['selected_subtype'] = self.request.GET.get('subtype', '')
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
