from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit

from ..forms import LoanForm, LoanHistoryEntryForm, LoanInterestRateForm, LoanRepaymentForm
from ..loan_history import build_history, is_due_date, prefetch_statement_history
from ..models import (
    CapitalEvent,
    Loan,
    LoanHistoryEntry,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    UserProfile,
)
from ..posthog_utils import ph_capture
from ..services import LoanService
from ..services_recurring import RecurringService
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
        queryset = Loan.objects.filter(user=self.request.user).prefetch_related('repayments', 'interest_rates')
        return queryset.order_by('-start_date')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        status_filter = self.request.GET.get('status', 'active').lower()
        if status_filter not in ('active', 'inactive', 'all'):
            status_filter = 'active'

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
            r['linked_loan_id']: r['total_prepaid'] or Decimal('0.00')
            for r in capital_prepayment_totals
        }

        all_loans = list(self.object_list)
        prefetch_statement_history(all_loans)
        statement_results = {
            loan.pk: build_history(loan) for loan in all_loans if loan.statement_history
        }
        for loan in all_loans:
            r = repayment_map.get(loan.id, {})
            loan.paid_principal = r.get('total_principal') or Decimal('0.00')
            loan.capital_prepaid = capital_prepayment_map.get(loan.id) or Decimal('0.00')
            if loan.statement_history:
                loan.is_active = statement_results[loan.pk].is_active

        active_count = sum(1 for l in all_loans if l.is_active)
        inactive_count = sum(1 for l in all_loans if not l.is_active)
        all_count = len(all_loans)

        filtered_loans = [
            loan for loan in all_loans
            if status_filter == 'all' or loan.is_active == (status_filter == 'active')
        ]
        context['loans'] = filtered_loans
        context['object_list'] = filtered_loans

        loan_summaries = []
        for loan in filtered_loans:
            r = repayment_map.get(loan.id, {})
            principal_paid = r.get('total_principal') or Decimal('0.00')
            interest_paid = r.get('total_interest') or Decimal('0.00')
            total_paid = r.get('total_amount') or Decimal('0.00')
            capital_prepaid = capital_prepayment_map.get(loan.id) or Decimal('0.00')

            loan.paid_principal = principal_paid
            loan.capital_prepaid = capital_prepaid
            statement = statement_results.get(loan.pk)
            if statement is not None:
                principal_paid, interest_paid = statement.principal_paid, statement.interest_paid
                total_paid = principal_paid + interest_paid
                remaining_principal = statement.balance
                initial = statement.drawn
            else:
                remaining_principal = loan.remaining_principal
                initial = loan.initial_principal
            opening_paid = Decimal(str(loan.opening_paid_principal or 0))
            progress = min((Decimal('1') - remaining_principal / initial) * Decimal('100'), Decimal('100')) if initial > 0 else Decimal('0' if statement is not None else '100')
            summary = {
                'loan': loan,
                'principal_paid': float(principal_paid),
                'capital_prepaid': float(capital_prepaid),
                'opening_paid': float(opening_paid),
                'interest_paid': float(interest_paid),
                'total_paid': float(total_paid),
                'remaining_principal': float(remaining_principal),
                'progress': float(progress),
                'tracked_principal': initial,
            }
            loan_summaries.append(summary)

        # Portfolio numbers add loans together, so they are converted to the user's own currency
        # first (each loan card keeps showing its own currency).
        fx = {}
        def to_base(loan, value):
            if loan.currency not in fx:
                fx[loan.currency] = LoanService.base_rate(self.request.user, loan.currency)
            return float(Decimal(str(value)) * fx[loan.currency])

        # Total remaining debt across all active loans for top summary card
        total_debt = float(sum(
            to_base(l, statement_results[l.pk].balance if l.statement_history else l.remaining_principal)
            for l in all_loans if l.is_active
        ))

        # Portfolio Chart Data
        tot_principal_paid = sum(to_base(s['loan'], s['principal_paid'] + s['capital_prepaid'] + s['opening_paid']) for s in loan_summaries)
        tot_interest_paid = sum(to_base(s['loan'], s['interest_paid']) for s in loan_summaries)
        tot_remaining_debt = sum(to_base(s['loan'], s['remaining_principal']) for s in loan_summaries)
        tot_total_portfolio = tot_principal_paid + tot_remaining_debt

        paid_pct = round((tot_principal_paid / tot_total_portfolio) * 100, 1) if tot_total_portfolio > 0 else 0.0
        remaining_pct = round((tot_remaining_debt / tot_total_portfolio) * 100, 1) if tot_total_portfolio > 0 else 0.0

        portfolio_totals = {
            'tot_principal_paid': round(tot_principal_paid, 2),
            'tot_interest_paid': round(tot_interest_paid, 2),
            'tot_remaining_debt': round(tot_remaining_debt, 2),
            'paid_pct': paid_pct,
            'remaining_pct': remaining_pct,
        }

        portfolio_breakdown_chart = {
            'labels': [_('Principal Paid'), _('Interest Paid'), _('Remaining Debt')],
            'values': [round(tot_principal_paid, 2), round(tot_interest_paid, 2), round(tot_remaining_debt, 2)],
        }

        loan_comparison_chart = {
            'labels': [s['loan'].name for s in loan_summaries],
            'principal_paid': [round(s['principal_paid'] + s['capital_prepaid'] + s['opening_paid'], 2) for s in loan_summaries],
            'remaining_principal': [round(s['remaining_principal'], 2) for s in loan_summaries],
        }

        context['loan_summaries'] = loan_summaries
        context['total_debt'] = total_debt
        context['status_filter'] = status_filter
        context['active_count'] = active_count
        context['inactive_count'] = inactive_count
        context['all_count'] = all_count
        context['portfolio_totals'] = portfolio_totals
        context['portfolio_breakdown_chart'] = portfolio_breakdown_chart
        context['loan_comparison_chart'] = loan_comparison_chart
        return context

class LoanCreateView(LoginRequiredMixin, LoanFeatureGateMixin, CreateView):
    model = Loan
    form_class = LoanForm
    template_name = 'expenses/loan_form.html'
    success_url = reverse_lazy('loan-list')

    def form_valid(self, form):
        statement_key = form.cleaned_data['statement_key'] if form.cleaned_data['statement_history'] else None
        try:
            with transaction.atomic():
                return self.create_loan(form, statement_key)
        except IntegrityError:
            if not statement_key:
                raise
            existing = Loan.objects.filter(user=self.request.user, uuid=statement_key, statement_history=True).first()
            if existing:
                return statement_history_redirect(existing)
            form.add_error(None, _("This form could not be saved. Reload the page and try again."))
            return self.form_invalid(form)

    def create_loan(self, form, statement_key):
        if statement_key:
            # Serialize plan-limit checks and retries from this user's Add Loan forms.
            UserProfile.objects.select_for_update().get(user=self.request.user)
            existing = Loan.objects.filter(user=self.request.user, uuid=statement_key, statement_history=True).first()
            if existing:
                return statement_history_redirect(existing)
        if not self.request.user.profile.can_add_loan():
            limit = get_limit(self.request.user.profile.active_tier, 'loans')
            messages.error(self.request, _("You have reached your current plan limit of %(limit)s tracked loans. Please upgrade to add more.") % {'limit': limit})
            return redirect('pricing')

        form.instance.user = self.request.user
        if statement_key:
            form.instance.uuid = statement_key
        self.object = form.save()
        LoanInterestRate.objects.create(
            loan=self.object,
            interest_rate=form.cleaned_data['interest_rate'],
            effective_date=self.object.start_date,
        )
        if self.object.statement_history and form.cleaned_data['scheduled_emi'] is not None:
            LoanHistoryEntry.objects.create(
                loan=self.object, kind=LoanHistoryEntry.Kind.EMI,
                date=self.object.start_date, amount=form.cleaned_data['scheduled_emi'],
            )
        messages.success(self.request, _("Loan created successfully!"))
        ph_capture(self.request.user, 'loan_created', {'loan_type': getattr(self.object, 'loan_type', ''), 'currency': self.object.currency, 'tenure_months': self.object.duration_months})
        if self.object.statement_history:
            return statement_history_redirect(self.object)
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
            if form.instance.statement_history:
                return self.save_statement_loan(form)
            self.object = form.save()
            # Update initial interest rate if it changed and is the only one
            rates = self.object.interest_rates.all()
            if rates.count() == 1:
                rate = rates.first()
                rate.interest_rate = form.cleaned_data['interest_rate']
                rate.effective_date = self.object.start_date   # the first rate always starts with the loan
                rate.save()
            # A new principal or term can reopen a closed loan or close a fully-paid one
            LoanService.sync_loan_active_status(self.object)
        messages.success(self.request, _("Loan updated successfully!"))
        ph_capture(self.request.user, 'loan_updated', {})
        return redirect(self.success_url)

    def save_statement_loan(self, form):
        loan = form.save(commit=False)
        current = Loan.objects.select_for_update().get(pk=loan.pk, user=self.request.user)
        entries = list(current.history_entries.all())
        if any(
            entry.date < loan.start_date
            or (entry.kind == LoanHistoryEntry.Kind.RATE and entry.date == loan.start_date)
            or (entry.installment_date and not is_due_date(loan, entry.installment_date))
            for entry in entries
        ):
            form.add_error(None, _("These terms conflict with existing history. Correct the affected entries first."))
        elif build_history(loan, entries=entries, initial_rate=form.cleaned_data['interest_rate']).invalid_entry:
            form.add_error(None, _("These terms leave a recorded payment with more principal than was owed."))
        if form.errors:
            return self.form_invalid(form)

        self.object = loan
        loan.save()
        LoanInterestRate.objects.filter(loan=loan, effective_date=current.start_date).update(
            interest_rate=form.cleaned_data['interest_rate'], effective_date=loan.start_date,
        )
        invalidate_statement_insights(loan)
        messages.success(self.request, _("Loan updated successfully!"))
        return statement_history_redirect(loan)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

class LoanDeleteView(LoginRequiredMixin, LoanFeatureGateMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Loan
    success_url = reverse_lazy('loan-list')

    def get_queryset(self):
        return Loan.objects.filter(user=self.request.user)

    def get(self, request, *args, **kwargs):
        # There is no confirmation page: deleting is a confirmed POST from the loan screens.
        return redirect('loan-detail', pk=self.get_object().uuid)

    def form_valid(self, form):
        # Django 4+ deletes through form_valid on POST; a custom delete() is never called.
        response = super().form_valid(form)
        messages.success(self.request, _("Loan deleted successfully."))
        ph_capture(self.request.user, 'loan_deleted', {})
        return response

class LoanDetailView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    template_name = 'expenses/loan_detail.html'

    def get(self, request, pk):
        loan = get_object_by_uuid_or_pk(
            Loan.objects.prefetch_related('interest_rates', 'repayments'),
            pk,
            user=request.user
        )
        redirect_response = redirect_to_uuid_url_if_needed(request, loan)
        if redirect_response:
            return redirect_response
        if loan.statement_history:
            return render(request, 'expenses/loan_statement_detail.html', statement_context(loan))
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

        principal_paid_total = round(summary['principal_paid'] + summary['capital_prepaid'] + summary['opening_paid_principal'], 2)
        interest_paid_total = round(summary['interest_paid'], 2)
        remaining_principal_total = round(summary['remaining_principal'], 2)

        breakdown_chart_data = {
            'labels': [_('Principal Paid'), _('Interest Paid'), _('Remaining Principal')],
            'values': [principal_paid_total, interest_paid_total, remaining_principal_total],
        }

        # Build amortization chart data:
        # For active loans: prepend historical repayments/capital events, then append future schedule
        # For paid-off loans: show full historical trajectory only
        from collections import defaultdict

        def _build_historical_months(loan_repayments, capital_events, initial_principal):
            """Aggregate past repayments and capital events by month, return sorted monthly data."""
            all_events = []
            for r in loan_repayments.order_by('date'):
                all_events.append({
                    'date': r.date,
                    'principal': float(r.principal_portion or 0),
                    'interest': float(r.interest_portion or 0),
                    'type': 'emi',
                })
            for c in capital_events:
                all_events.append({
                    'date': c.date,
                    'principal': float(c.base_amount or 0),
                    'interest': 0.0,
                    'type': 'capital',
                })
            all_events.sort(key=lambda x: x['date'])

            monthly_map = defaultdict(lambda: {'principal': 0.0, 'interest': 0.0})
            for ev in all_events:
                m_key = ev['date'].strftime('%b %Y')
                monthly_map[m_key]['principal'] += ev['principal']
                monthly_map[m_key]['interest'] += ev['interest']

            h_labels, h_principals, h_interests, h_balances = [], [], [], []
            curr_balance = float(initial_principal)
            for m_key, vals in monthly_map.items():
                h_labels.append(m_key)
                h_principals.append(round(vals['principal'], 2))
                h_interests.append(round(vals['interest'], 2))
                curr_balance = max(0.0, curr_balance - vals['principal'])
                h_balances.append(round(curr_balance, 2))
            return h_labels, h_principals, h_interests, h_balances

        # Check if there's any prior payment history (EMIs or capital events)
        has_prior_history = repayments.exists() or linked_capital_events.exists()

        if schedule:
            hist_labels, hist_principals, hist_interests, hist_balances = [], [], [], []
            if has_prior_history:
                hist_labels, hist_principals, hist_interests, hist_balances = _build_historical_months(
                    loan.repayments.order_by('date'),
                    linked_capital_events,
                    loan.initial_principal,
                )
            amortization_chart_data = {
                'labels': hist_labels + [item['month'] for item in schedule],
                'principal': hist_principals + [item['principal'] for item in schedule],
                'interest': hist_interests + [item['interest'] for item in schedule],
                'balance': hist_balances + [item['balance'] for item in schedule],
                'history_count': len(hist_labels),   # how many data points are historical
                'is_historical': False,
            }
        elif has_prior_history:
            # Generate historical trajectory for paid-off or past loans
            all_events = []
            for r in loan.repayments.order_by('date'):
                all_events.append({
                    'date': r.date,
                    'principal': float(r.principal_portion or 0),
                    'interest': float(r.interest_portion or 0),
                })
            for c in linked_capital_events:
                all_events.append({
                    'date': c.date,
                    'principal': float(c.base_amount or 0),
                    'interest': 0.0,
                })
            all_events.sort(key=lambda x: x['date'])

            from collections import defaultdict
            monthly_map = defaultdict(lambda: {'principal': 0.0, 'interest': 0.0})
            for ev in all_events:
                m_key = ev['date'].strftime('%b %Y')
                monthly_map[m_key]['principal'] += ev['principal']
                monthly_map[m_key]['interest'] += ev['interest']

            labels = []
            principals = []
            interests = []
            balances = []
            curr_balance = float(loan.initial_principal)

            for m_key, vals in monthly_map.items():
                labels.append(m_key)
                principals.append(round(vals['principal'], 2))
                interests.append(round(vals['interest'], 2))
                curr_balance = max(0.0, curr_balance - vals['principal'])
                balances.append(round(curr_balance, 2))

            amortization_chart_data = {
                'labels': labels,
                'principal': principals,
                'interest': interests,
                'balance': balances,
                'is_historical': True,
            }
        # Build simplified trend chart data for Overview tab (last ~6 months balance trajectory)
        trend_labels = []
        trend_balances = []
        trend_summary_text = ""

        all_chart_labels = amortization_chart_data.get('labels', [])
        all_chart_balances = amortization_chart_data.get('balance', [])
        hist_count = amortization_chart_data.get('history_count', 0)

        if all_chart_labels:
            if hist_count > 0:
                start_idx = max(0, hist_count - 6)
                trend_labels = all_chart_labels[start_idx:hist_count]
                trend_balances = all_chart_balances[start_idx:hist_count]
            else:
                trend_labels = all_chart_labels[:min(6, len(all_chart_labels))]
                trend_balances = all_chart_balances[:min(6, len(all_chart_balances))]

        if len(trend_balances) >= 2:
            start_val = trend_balances[0]
            end_val = trend_balances[-1]
            months_count = len(trend_balances)
            trend_summary_text = _("%(currency)s%(start)s → %(currency)s%(end)s over the last %(count)s months") % {
                'currency': loan.currency,
                'start': f"{start_val:,.0f}",
                'end': f"{end_val:,.0f}",
                'count': months_count,
            }
        elif len(trend_balances) == 1:
            trend_summary_text = _("Current balance: %(currency)s%(val)s") % {
                'currency': loan.currency,
                'val': f"{trend_balances[0]:,.0f}",
            }

        trend_chart_data = {
            'labels': trend_labels,
            'balance': trend_balances,
        }

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
            'breakdown_chart_data': breakdown_chart_data,
            'amortization_chart_data': amortization_chart_data,
            'trend_chart_data': trend_chart_data,
            'trend_summary_text': trend_summary_text,
        }
        return render(request, self.template_name, context)


def statement_history_redirect(loan):
    return HttpResponseRedirect(reverse('loan-detail', kwargs={'pk': loan.uuid}) + '#statement-history')


def invalidate_statement_insights(loan):
    UserProfile.objects.filter(user_id=loan.user_id).update(insights_stale=True)


def statement_context(loan):
    result = build_history(loan, project=True)
    today = timezone.localdate()
    return {
        'loan': loan,
        'statement': result,
        'history': [row for row in result.rows if row.date <= today],
        'projection': [row for row in result.rows if row.status == 'projected'],
        'upcoming_terms': [
            row for row in result.rows
            if row.date > today and row.status in (LoanHistoryEntry.Kind.RATE, LoanHistoryEntry.Kind.EMI)
        ],
        'current_rate': LoanService.current_rate(loan),
        'projection_remaining': result.projected_balance + result.projected_unpaid_interest,
    }


class LoanHistoryEntryView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    template_name = 'expenses/loan_history_entry_form.html'

    def get_loan(self, request, pk, *, lock=False):
        loans = Loan.objects.filter(statement_history=True)
        if lock:
            loans = loans.select_for_update()
        return get_object_by_uuid_or_pk(loans, pk, user=request.user)

    def get_form(self, request, loan, entry=None):
        if entry is not None:
            kind = entry.kind
        else:
            kind = request.POST.get('kind') if request.method == 'POST' else request.GET.get('kind')
        if kind not in LoanHistoryEntry.Kind.values:
            raise Http404
        initial = {}
        if kind == LoanHistoryEntry.Kind.DISBURSEMENT and not loan.history_entries.filter(
            kind=LoanHistoryEntry.Kind.DISBURSEMENT,
        ).exists():
            initial['date'] = loan.start_date
        if request.method == 'GET' and request.GET.get('due'):
            try:
                day = parse_date(request.GET['due'])
            except ValueError:
                day = None
            if day is not None:
                initial['date'] = day
                if kind == LoanHistoryEntry.Kind.PAYMENT:
                    initial['installment_date'] = day
        return LoanHistoryEntryForm(
            request.POST if request.method == 'POST' else None,
            loan=loan, kind=kind, instance=entry, initial=initial,
        )

    def render_form(self, request, loan, form, entry=None):
        return render(request, self.template_name, {
            'loan': loan, 'form': form, 'entry': entry,
            'entry_type': LoanHistoryEntry.Kind(form.kind).label,
        })

    def get(self, request, pk, entry_uuid=None):
        loan = self.get_loan(request, pk)
        entry = get_object_or_404(loan.history_entries, uuid=entry_uuid) if entry_uuid else None
        return self.render_form(request, loan, self.get_form(request, loan, entry), entry)

    @transaction.atomic
    def post(self, request, pk, entry_uuid=None):
        loan = self.get_loan(request, pk, lock=True)
        entries = list(loan.history_entries.all())
        entry = next((row for row in entries if row.uuid == entry_uuid), None)
        if entry_uuid and entry is None:
            if request.POST.get('action') == 'delete':
                return statement_history_redirect(loan)
            raise Http404
        remaining = [row for row in entries if row is not entry]

        if entry is not None and request.POST.get('action') == 'delete':
            if build_history(loan, entries=remaining).invalid_entry:
                messages.error(request, _("This entry is needed by a recorded payment. Correct that payment first."))
            else:
                entry.delete()
                invalidate_statement_insights(loan)
            return statement_history_redirect(loan)

        form = self.get_form(request, loan, entry)
        if not form.is_valid():
            return self.render_form(request, loan, form, entry)
        candidate = form.save(commit=False)
        if entry is None and any(row.uuid == candidate.uuid for row in entries):
            return statement_history_redirect(loan)
        if candidate.kind in (LoanHistoryEntry.Kind.RATE, LoanHistoryEntry.Kind.EMI) and any(
            row.kind == candidate.kind and row.date == candidate.date for row in remaining
        ):
            form.add_error('date', _("Edit the existing change for this effective date."))
        if candidate.installment_date and any(row.installment_date == candidate.installment_date for row in remaining):
            form.add_error(None, _("Edit or delete the existing correction for this installment."))
        if not form.errors and build_history(loan, entries=[*remaining, candidate]).invalid_entry:
            form.add_error(None, _("A recorded payment would have more principal than was owed on its effective date."))
        if form.errors:
            return self.render_form(request, loan, form, entry)

        try:
            with transaction.atomic():
                candidate.save()
        except IntegrityError:
            form.add_error(None, _("This entry could not be saved. Reload the history and try again."))
            return self.render_form(request, loan, form, entry)
        invalidate_statement_insights(loan)
        messages.success(request, _("Loan history updated."))
        return statement_history_redirect(loan)


class LoanScheduleTabView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    template_name = 'expenses/partials/_loan_schedule_tab.html'

    def get(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        if loan.statement_history:
            return render(request, 'expenses/partials/_loan_statement_projection.html', statement_context(loan))
        schedule = LoanService.generate_amortization_schedule(loan)
        return render(request, self.template_name, {'loan': loan, 'schedule': schedule})


class LoanHistoryTabView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    template_name = 'expenses/partials/_loan_history_tab.html'

    def get(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        if loan.statement_history:
            return render(request, 'expenses/partials/_loan_statement_history.html', statement_context(loan))
        repayments = loan.repayments.select_related('from_account').order_by('-date')
        return render(request, self.template_name, {'loan': loan, 'repayments': repayments})

class LoanRepaymentCreateView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    def post(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        if loan.statement_history:
            raise Http404
        form = LoanRepaymentForm(request.POST, user=request.user, loan=loan)
        if form.is_valid():
            try:
                with transaction.atomic():
                    repayment = form.save(commit=False)
                    repayment.loan = loan
                    repayment.save()

                    if form.cleaned_data.get('add_to_recurring'):
                        try:
                            with transaction.atomic():
                                RecurringService.make_recurring(
                                    request.user,
                                    'LOAN',
                                    repayment.amount,
                                    loan.currency,
                                    repayment.from_account,
                                    _("Loan EMI: %(name)s") % {'name': loan.name},
                                    form.cleaned_data.get('recurring_frequency') or 'MONTHLY',
                                    repayment.date,
                                    loan=loan,
                                )
                            messages.info(request, _("Recurring loan repayment created."))
                        except IntegrityError:
                            # An identical active schedule exists (the DB rule ignores the account)
                            messages.warning(request, _("The repayment was recorded, but an identical recurring repayment already exists."))

                messages.success(request, _("Repayment recorded successfully!"))
                ph_capture(request.user, 'loan_repayment_added', {})
            except (RuntimeError, ValidationError):
                messages.error(request, _("Unable to record repayment because currency conversion failed or repayment data is invalid."))
        else:
            errors_list = [f"{err}" for field, errs in form.errors.items() for err in errs]
            err_msg = " ".join(errors_list) if errors_list else _("Please check the form.")
            messages.error(request, _("Error recording repayment: %(err)s") % {'err': err_msg})
        return redirect('loan-detail', pk=loan.uuid)

class LoanInterestRateCreateView(LoginRequiredMixin, LoanFeatureGateMixin, View):
    def post(self, request, pk):
        loan = get_object_by_uuid_or_pk(Loan, pk, user=request.user)
        if loan.statement_history:
            raise Http404
        form = LoanInterestRateForm(request.POST)
        if form.is_valid():
            rate = form.save(commit=False)
            rate.loan = loan
            rate.save()
            messages.success(request, _("Interest rate updated successfully!"))
            ph_capture(request.user, 'loan_interest_rate_updated', {})
        else:
            messages.error(request, _("Error updating interest rate."))
        return redirect('loan-detail', pk=loan.uuid)
class LoanRepaymentDeleteView(LoginRequiredMixin, LoanFeatureGateMixin, UUIDOrIntLookupMixin, DeleteView):
    model = LoanRepayment

    def get_queryset(self):
        return LoanRepayment.objects.filter(loan__user=self.request.user)

    def get(self, request, *args, **kwargs):
        return redirect('loan-detail', pk=self.get_object().loan.uuid)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Repayment deleted successfully."))
        ph_capture(self.request.user, 'loan_repayment_deleted', {})
        return response

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        fallback = reverse_lazy('loan-detail', kwargs={'pk': self.object.loan.uuid})
        if next_url:
            return get_safe_redirect_url(self.request, next_url, fallback)
        return fallback
