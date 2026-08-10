from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit

from ..forms import LoanForm, LoanInterestRateForm, LoanRepaymentForm
from ..models import (
    CapitalEvent,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
)
from ..services import LoanService
from .mixins import HtmxPartialTemplateMixin, UUIDOrIntLookupMixin
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed


class LoanFeatureGateMixin:
    def dispatch(self, request, *args, **kwargs):
        if get_limit(request.user.profile.active_tier, 'loans') == 0:
            messages.info(request, _("Loan tracking is available on Plus and Pro plans. Please upgrade to unlock this feature."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)


class LoanListView(HtmxPartialTemplateMixin, LoginRequiredMixin, LoanFeatureGateMixin, ListView):
    model = Loan
    template_name = 'expenses/loan_list.html'
    htmx_template_name = 'expenses/partials/_loan_list_partial.html'
    context_object_name = 'loans'

    def get_queryset(self):
        # Auto-sync active status for all loans belonging to this user
        all_user_loans = list(Loan.objects.filter(user=self.request.user).prefetch_related('repayments'))
        for loan in all_user_loans:
            LoanService.sync_loan_active_status(loan)

        status_filter = self.request.GET.get('status', 'active').lower()
        if status_filter == 'inactive':
            return Loan.objects.filter(user=self.request.user, is_active=False).prefetch_related('repayments').order_by('-start_date')
        elif status_filter == 'all':
            return Loan.objects.filter(user=self.request.user).prefetch_related('repayments').order_by('-start_date')
        else:
            return Loan.objects.filter(user=self.request.user, is_active=True).prefetch_related('repayments').order_by('-start_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_filter = self.request.GET.get('status', 'active').lower()
        if status_filter not in ('active', 'inactive', 'all'):
            status_filter = 'active'

        # Compute pill counts across ALL user loans
        all_loans = list(Loan.objects.filter(user=self.request.user))
        active_count = sum(1 for l in all_loans if l.is_active)
        inactive_count = sum(1 for l in all_loans if not l.is_active)
        all_count = len(all_loans)

        filtered_loans = self.object_list

        from django.db.models import Sum

        from ..models import CapitalEvent, LoanRepayment

        repayment_totals = (
            LoanRepayment.objects
            .filter(loan__user=self.request.user)
            .values('loan_id')
            .annotate(
                total_principal=Sum('principal_portion'),
                total_interest=Sum('interest_portion'),
                total_amount=Sum('amount'),
            )
        )
        repayment_map = {r['loan_id']: r for r in repayment_totals}

        capital_prepayment_totals = (
            CapitalEvent.objects
            .filter(linked_loan__user=self.request.user, subtype__in=['loan_down_payment', 'loan_prepayment'])
            .values('linked_loan_id')
            .annotate(total_prepaid=Sum('amount'))
        )
        capital_prepayment_map = {
            r['linked_loan_id']: float(r['total_prepaid'] or 0)
            for r in capital_prepayment_totals
        }

        loan_summaries = []
        for loan in filtered_loans:
            r = repayment_map.get(loan.id, {})
            principal_paid = float(r.get('total_principal') or 0)
            interest_paid = float(r.get('total_interest') or 0)
            total_paid = float(r.get('total_amount') or 0)
            capital_prepaid = capital_prepayment_map.get(loan.id, 0)
            remaining_principal = max(float(loan.initial_principal) - principal_paid - capital_prepaid, 0)
            initial = float(loan.initial_principal)
            progress = min((1 - remaining_principal / initial) * 100, 100) if initial > 0 else 100
            summary = {
                'loan': loan,
                'principal_paid': principal_paid,
                'capital_prepaid': capital_prepaid,
                'interest_paid': interest_paid,
                'total_paid': total_paid,
                'remaining_principal': remaining_principal,
                'progress': progress,
            }
            loan_summaries.append(summary)

        # Total remaining debt across all active loans for top summary card
        total_debt = sum(
            max(float(l.initial_principal) - float(repayment_map.get(l.id, {}).get('total_principal') or 0) - capital_prepayment_map.get(l.id, 0), 0)
            for l in all_loans if l.is_active
        )

        context['loan_summaries'] = loan_summaries
        context['total_debt'] = total_debt
        context['status_filter'] = status_filter
        context['active_count'] = active_count
        context['inactive_count'] = inactive_count
        context['all_count'] = all_count
        return context

class LoanCreateView(LoginRequiredMixin, LoanFeatureGateMixin, CreateView):
    model = Loan
    form_class = LoanForm
    template_name = 'expenses/loan_form.html'
    success_url = reverse_lazy('loan-list')

    def form_valid(self, form):
        if not self.request.user.profile.can_add_loan():
            limit = get_limit(self.request.user.profile.active_tier, 'loans')
            messages.error(self.request, _("You have reached your current plan limit of %(limit)s tracked loans. Please upgrade to add more.") % {'limit': limit})
            return redirect('pricing')

        with transaction.atomic():
            form.instance.user = self.request.user
            self.object = form.save()
            # Create initial interest rate
            LoanInterestRate.objects.create(
                loan=self.object,
                interest_rate=form.cleaned_data['interest_rate'],
                effective_date=self.object.start_date
            )
        messages.success(self.request, _("Loan created successfully!"))
        return redirect(self.success_url)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

class LoanUpdateView(LoginRequiredMixin, LoanFeatureGateMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Loan
    form_class = LoanForm
    template_name = 'expenses/loan_form.html'
    success_url = reverse_lazy('loan-list')

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def form_valid(self, form):
        with transaction.atomic():
            self.object = form.save()
            # Update initial interest rate if it changed and is the only one
            rates = self.object.interest_rates.all()
            if rates.count() == 1:
                rate = rates.first()
                rate.interest_rate = form.cleaned_data['interest_rate']
                rate.save()
        messages.success(self.request, _("Loan updated successfully!"))
        return redirect(self.success_url)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

class LoanDeleteView(LoginRequiredMixin, LoanFeatureGateMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Loan
    success_url = reverse_lazy('loan-list')

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _("Loan deleted successfully."))
        return super().delete(request, *args, **kwargs)

class LoanDetailView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    template_name = 'expenses/loan_detail.html'

    def get(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        redirect_response = redirect_to_uuid_url_if_needed(request, loan)
        if redirect_response:
            return redirect_response
        summary = LoanService.get_loan_summary(loan)
        schedule = LoanService.generate_amortization_schedule(loan)
        repayments = loan.repayments.select_related('from_account').order_by('-date')
        
        repayment_form = LoanRepaymentForm(user=request.user, loan=loan)
        rate_form = LoanInterestRateForm()
        extra_emi_savings = LoanService.calculate_extra_emi_savings(loan)

        # Capital events linked to this loan (down payments, prepayments, etc.)
        # Single query, no N+1
        linked_capital_events = CapitalEvent.objects.filter(
            linked_loan=loan
        ).select_related('account').order_by('date')
        linked_capital_total = sum(float(e.base_amount) for e in linked_capital_events)

        context = {
            'loan': loan,
            'summary': summary,
            'schedule': schedule,
            'repayments': repayments,
            'repayment_form': repayment_form,
            'rate_form': rate_form,
            'extra_emi_savings': extra_emi_savings,
            'linked_capital_events': linked_capital_events,
            'linked_capital_total': linked_capital_total,
        }
        return render(request, self.template_name, context)

class LoanRepaymentCreateView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    def post(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        form = LoanRepaymentForm(request.POST, user=request.user, loan=loan)
        if form.is_valid():
            try:
                repayment = form.save(commit=False)
                repayment.loan = loan
                repayment.save()

                if form.cleaned_data.get('add_to_recurring'):
                    recurring_defaults = {
                        'amount': repayment.amount,
                        'currency': loan.currency,
                        'account': repayment.from_account,
                        'loan': loan,
                        'frequency': form.cleaned_data.get('recurring_frequency') or 'MONTHLY',
                        'start_date': repayment.date,
                        'last_processed_date': repayment.date,
                        'description': _("Loan EMI: %(name)s") % {'name': loan.name},
                        'is_active': True,
                    }

                    rt, created = RecurringTransaction.objects.get_or_create(
                        user=request.user,
                        transaction_type='LOAN',
                        loan=loan,
                        is_active=True,
                        defaults=recurring_defaults,
                    )

                    if not created:
                        for key, value in recurring_defaults.items():
                            setattr(rt, key, value)
                        rt.save()
                        messages.info(request, _("Recurring loan repayment updated."))
                    else:
                        messages.info(request, _("Recurring loan repayment created."))

                messages.success(request, _("Repayment recorded successfully!"))
            except (RuntimeError, ValidationError):
                messages.error(request, _("Unable to record repayment because currency conversion failed or repayment data is invalid."))
        else:
            messages.error(request, _("Error recording repayment. Please check the form."))
        return redirect('loan-detail', pk=loan.uuid)

class LoanInterestRateCreateView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    def post(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        form = LoanInterestRateForm(request.POST)
        if form.is_valid():
            rate = form.save(commit=False)
            rate.loan = loan
            rate.save()
            messages.success(request, _("Interest rate updated successfully!"))
        else:
            messages.error(request, _("Error updating interest rate."))
        return redirect('loan-detail', pk=loan.uuid)
class LoanRepaymentDeleteView(LoginRequiredMixin, LoanFeatureGateMixin, UUIDOrIntLookupMixin, DeleteView):
    model = LoanRepayment

    def get_queryset(self):
        return LoanRepayment.objects.filter(loan__user=self.request.user)

    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _("Repayment deleted successfully."))
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        fallback = reverse_lazy('loan-detail', kwargs={'pk': self.object.loan.uuid})
        if next_url:
            return get_safe_redirect_url(self.request, next_url, fallback)
        return fallback
