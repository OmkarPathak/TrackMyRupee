from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import chain

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit
from ..posthog_utils import ph_capture

from ..account_types import deposit_codes
from ..account_valuation import get_baseline, get_current, get_interest_summary
from ..forms import AccountForm, TransferForm
from ..ledger_read_service import LedgerReadService, _compute_deposit_value
from ..models import (
    Account,
    CapitalEvent,
    Expense,
    GoalContribution,
    Holding,
    Income,
    LoanRepayment,
    Transfer,
    _run_ledger_shadow,
)
from ..utils import get_exchange_rate
from .mixins import (
    HtmxPartialTemplateMixin,
    RecurringTransactionMixin,
    UUIDOrIntLookupMixin,
)
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed


class AccountListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    model = Account
    template_name = 'expenses/account_list.html'
    htmx_template_name = 'expenses/partials/_account_list.html'
    context_object_name = 'accounts'

    def get_queryset(self):
        from .mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.request.user)
        status = self.request.GET.get('status', 'active')
        is_active = status == 'active'
        
        # Order by created_at to ensure consistent locking of 'newer' accounts
        queryset = list(
            Account.objects.select_related('user')
            .filter(user=self.request.user, is_active=is_active)
            .order_by('created_at', 'id')
        )
        
        account_type = self.request.GET.get('type')
        if account_type:
            # Check if account_type is a group name or normalized group ID
            group_codes = []
            for g_name, choices in Account.ACCOUNT_TYPES:
                import re
                g_id = re.sub(r'[^A-Z0-9_]', '_', g_name.upper())
                if account_type == g_name or account_type == g_id:
                    group_codes = [c for c, _ in choices]
                    break
            
            if group_codes:
                queryset = [acc for acc in queryset if acc.account_type in group_codes]
            else:
                queryset = [acc for acc in queryset if acc.account_type == account_type]

        # Search by account name
        search_query = self.request.GET.get('search', '').strip()
        if search_query:
            queryset = [acc for acc in queryset if search_query.lower() in acc.name.lower()]
            
        # Annotate locked status
        if self.request.user.is_authenticated:
            limit = get_limit(self.request.user.profile.active_tier, 'accounts')
            for i, acc in enumerate(queryset):
                acc.is_locked = (limit != -1 and i >= limit)
        else:
            for acc in queryset:
                acc.is_locked = False

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accounts = context.get('accounts', [])
        user_currency = getattr(getattr(self.request.user, 'profile', None), 'currency', '₹')
        total_balance = Decimal('0.00')
        current_status = self.request.GET.get('status', 'active')
        display_balances = {}

        if current_status == 'active':
            try:
                display_balances = LedgerReadService.get_account_balances(accounts)
            except Exception:
                display_balances = {}

        now = timezone.now()

        for account in accounts:
            if current_status == 'active':
                account.display_balance = display_balances.get(account.id, account.balance)
            else:
                account.display_balance = account.balance

            # Compute accrued value for fixed-income / deposit accounts
            if account.account_type in deposit_codes():
                accrued = _compute_deposit_value(account, Decimal(str(account.display_balance)))
                deposit_principal = account.deposit_principal if account.deposit_principal is not None else Decimal(str(account.display_balance))
                if getattr(account, 'show_accrued_balance', True) and (accrued != deposit_principal or account.deposit_rate is not None):
                    account.accrued_value = accrued
                    account.effective_principal = deposit_principal
                    account.has_accrued_value = True
                else:
                    account.has_accrued_value = False

                if account.deposit_maturity_date and account.deposit_rate is not None:
                    from ..account_valuation import get_current_deposit
                    account.maturity_value = get_current_deposit(account, ledger_balance=Decimal(str(account.display_balance)), today=account.deposit_maturity_date)
                    account.has_maturity_value = True
                else:
                    account.has_maturity_value = False
            else:
                account.has_accrued_value = False
                account.has_maturity_value = False

            delta = now - account.updated_at
            account.days_since_update = delta.days

            try:
                rate = get_exchange_rate(account.currency, user_currency)
            except Exception:
                rate = Decimal('1.0')
            bal_val = account.accrued_value if getattr(account, 'has_accrued_value', False) else account.display_balance
            total_balance += Decimal(str(bal_val)) * Decimal(str(rate))

        # Group accounts using nested ACCOUNT_TYPES groups
        grouped_accounts = []
        flat_labels = {}
        for group_name, choices in Account.ACCOUNT_TYPES:
            for val, label in choices:
                flat_labels[val] = label

        seen_account_ids = set()
        import re
        for group_name, choices in Account.ACCOUNT_TYPES:
            group_codes = [val for val, _ in choices]
            group_accs = [a for a in accounts if a.account_type in group_codes and a.id not in seen_account_ids]
            if not group_accs:
                continue

            for a in group_accs:
                seen_account_ids.add(a.id)

            # Calculate group total balance in user currency
            group_total = Decimal('0.00')
            for acc in group_accs:
                try:
                    rate = get_exchange_rate(acc.currency, user_currency)
                except Exception:
                    rate = Decimal('1.0')
                bal_val = acc.accrued_value if getattr(acc, 'has_accrued_value', False) else acc.display_balance
                group_total += Decimal(str(bal_val)) * Decimal(str(rate))

            group_type_id = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())

            grouped_accounts.append({
                'type': group_type_id,
                'label': group_name,
                'accounts': group_accs,
                'count': len(group_accs),
                'total': group_total.quantize(Decimal('0.01')),
            })

        # Calculate category percentages, colors, icons, and short formatted totals for breakdown banner
        category_palette = [
            {'color': '#3b82f6', 'dot_class': 'bg-primary', 'icon': 'bi-bank', 'icon_bg': 'bg-primary-subtle text-primary'},
            {'color': '#d97706', 'dot_class': 'bg-warning', 'icon': 'bi-briefcase-fill', 'icon_bg': 'bg-warning-subtle text-warning-emphasis'},
            {'color': '#8b5cf6', 'dot_class': 'bg-purple', 'icon': 'bi-graph-up-arrow', 'icon_bg': 'bg-info-subtle text-info-emphasis'},
            {'color': '#ec4899', 'dot_class': 'bg-danger', 'icon': 'bi-credit-card', 'icon_bg': 'bg-danger-subtle text-danger'},
            {'color': '#ef4444', 'dot_class': 'bg-danger', 'icon': 'bi-cash-stack', 'icon_bg': 'bg-danger-subtle text-danger'},
        ]

        def _get_short_amount(amount):
            val = float(amount)
            if val >= 10000000:
                return f"{user_currency}{val/10000000:.2f}Cr"
            elif val >= 100000:
                return f"{user_currency}{val/100000:.2f}L"
            elif val >= 1000:
                return f"{user_currency}{val/1000:.1f}k"
            else:
                return f"{user_currency}{val:.0f}"

        tb_float = float(total_balance)
        for idx, group in enumerate(grouped_accounts):
            gt_float = float(group['total'])
            group['pct'] = round((gt_float / tb_float * 100), 1) if tb_float > 0 else 0
            group['pct_int'] = int(round(group['pct']))
            group['short_total'] = _get_short_amount(group['total'])

            gtype = group['type']
            if 'CASH' in gtype or 'BANK' in gtype:
                p = category_palette[0]
            elif 'DEPOSIT' in gtype or 'FIXED' in gtype:
                p = category_palette[1]
            elif 'INVEST' in gtype or 'MARKET' in gtype or 'EQUITY' in gtype:
                p = category_palette[2]
            elif 'CREDIT' in gtype:
                p = category_palette[3]
            elif 'LOAN' in gtype or 'LIABIL' in gtype:
                p = category_palette[4]
            else:
                p = category_palette[idx % len(category_palette)]

            group['color'] = p['color']
            group['dot_class'] = p['dot_class']
            group['icon'] = p['icon']
            group['icon_bg'] = p['icon_bg']

        # Apply sort to grouped accounts
        sort_by = self.request.GET.get('sort', 'balance_desc')
        for group in grouped_accounts:
            if sort_by == 'balance_asc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: float(a.accrued_value if getattr(a, 'has_accrued_value', False) else a.display_balance))
            elif sort_by == 'balance_desc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: float(a.accrued_value if getattr(a, 'has_accrued_value', False) else a.display_balance), reverse=True)
            elif sort_by == 'name_asc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: a.name.lower())
            elif sort_by == 'name_desc':
                group['accounts'] = sorted(group['accounts'], key=lambda a: a.name.lower(), reverse=True)

        context['grouped_accounts'] = grouped_accounts
        context['account_types'] = Account.ACCOUNT_TYPES
        context['account_count'] = len(accounts)
        selected_type = self.request.GET.get('type', '')
        selected_label = flat_labels.get(selected_type, '')
        if not selected_label and selected_type:
            for g_name, choices in Account.ACCOUNT_TYPES:
                import re
                g_id = re.sub(r'[^A-Z0-9_]', '_', g_name.upper())
                if selected_type == g_name or selected_type == g_id:
                    selected_label = g_name
                    break
        context['selected_type'] = selected_type
        context['selected_type_label'] = selected_label
        context['current_status'] = current_status
        context['total_balance'] = total_balance.quantize(Decimal('0.01'))
        context['total_balance_currency'] = user_currency

        # Build type_chips for all account categories so filter pills remain visible even when a type filter is active
        all_status_accounts = list(
            Account.objects.filter(user=self.request.user, is_active=(current_status == 'active'))
        )
        search_query = self.request.GET.get('search', '').strip()
        if search_query:
            all_status_accounts = [a for a in all_status_accounts if search_query.lower() in a.name.lower()]

        type_chips = []
        seen_chip_ids = set()
        import re
        for group_name, choices in Account.ACCOUNT_TYPES:
            group_codes = [val for val, _ in choices]
            g_accs = [a for a in all_status_accounts if a.account_type in group_codes and a.id not in seen_chip_ids]
            for a in g_accs:
                seen_chip_ids.add(a.id)
            group_type_id = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
            if len(g_accs) > 0:
                type_chips.append({
                    'type': group_type_id,
                    'label': group_name,
                    'count': len(g_accs),
                })

        context['type_chips'] = type_chips
        context['total_account_count'] = len(all_status_accounts)
        context['search_query'] = search_query
        context['sort_by'] = sort_by

        active_filters_count = 0
        if search_query:
            active_filters_count += 1
        if selected_type:
            active_filters_count += 1
        if current_status != 'active':
            active_filters_count += 1
        if sort_by != 'balance_desc':
            active_filters_count += 1
        context['active_filters_count'] = active_filters_count

        context['interest_summary'] = get_interest_summary(self.request.user)
        return context

class AccountCreateView(LoginRequiredMixin, CreateView):
    model = Account
    form_class = AccountForm
    template_name = 'expenses/account_form.html'
    success_url = reverse_lazy('account-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        if not self.request.user.profile.can_add_account():
            limit = get_limit(self.request.user.profile.active_tier, 'accounts')
            messages.error(self.request, _("You have reached the limit of %(limit)s accounts for your current plan. Please upgrade to add more.") % {'limit': limit})
            return redirect('pricing')
        form.instance.user = self.request.user
        messages.success(self.request, _("Account created successfully!"))
        response = super().form_valid(form)
        if self.object:
            ph_capture(self.request.user, 'account_created', {'account_type': self.object.account_type, 'currency': self.object.currency, 'has_credit_limit': bool(getattr(self.object, 'credit_limit', None))})
        return response

class AccountUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Account
    form_class = AccountForm
    template_name = 'expenses/account_form.html'
    success_url = reverse_lazy('account-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        account = self.get_object()
        if request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to modify it."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        old_state = self.get_object()
        old_balance = old_state.balance
        messages.success(self.request, _("Account updated successfully!"))
        response = super().form_valid(form)
        ph_capture(self.request.user, 'account_updated', {'account_type': self.object.account_type})

        # Manual balance edits should treat the entered balance as source of truth.
        # Offset any existing drift by targeting the current ledger-derived balance.
        if getattr(settings, 'LEDGER_WRITE_ENABLED', False):
            from ..ledger_service import LedgerPostingService

            try:
                ledger_balance_before_adjustment = LedgerReadService.get_account_ledger_delta(self.object)
            except Exception:
                ledger_balance_before_adjustment = old_balance

            target_balance = self.object.balance.quantize(Decimal('0.01'))
            balance_delta = (target_balance - ledger_balance_before_adjustment).quantize(Decimal('0.01'))

            if balance_delta == Decimal('0.00'):
                return response

            version_token = f"ACCOUNT_BALANCE_EDIT-{int(self.object.updated_at.timestamp() * 1000000)}"

            def _post_shadow_entry():
                LedgerPostingService.post_account_balance_adjustment(
                    account=self.object,
                    delta=balance_delta,
                    version_token=version_token,
                    metadata={
                        'kind': 'MANUAL_BALANCE_EDIT',
                        'actor_user_id': self.request.user.id,
                        'old_balance': str(old_balance),
                        'new_balance': str(self.object.balance),
                        'ledger_balance_before_adjustment': str(ledger_balance_before_adjustment),
                    },
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='ADJUSTMENT',
                source_id=self.object.id,
                action='ACCOUNT_BALANCE_EDIT',
                payload={
                    'handler': 'account_balance_edit',
                    'version_token': version_token,
                    'account': {
                        'account_id': self.object.id,
                        'user_id': self.object.user_id,
                        'currency': self.object.currency,
                        'old_balance': str(old_balance),
                        'new_balance': str(self.object.balance),
                        'ledger_balance_before_adjustment': str(ledger_balance_before_adjustment),
                        'delta': str(balance_delta),
                    },
                },
            )

        return response

class AccountDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Account
    template_name = 'expenses/account_delete_confirm.html'
    success_url = reverse_lazy('account-list')

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user, is_active=True)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        account = self.get_object()
        if request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to delete it."))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        success_url = self.get_success_url()
        self.object.is_active = False
        self.object.save()
        messages.success(self.request, _("Account deleted successfully."))
        ph_capture(self.request.user, 'account_deleted', {})
        return redirect(success_url)


class AccountRestoreView(LoginRequiredMixin, View):
    def get(self, request, pk):
        account = get_object_by_uuid_or_pk(Account, pk, user=request.user, is_active=False)
        redirect_response = redirect_to_uuid_url_if_needed(request, account)
        if redirect_response:
            return redirect_response
        if not request.user.profile.can_add_account():
            limit = get_limit(request.user.profile.active_tier, 'accounts')
            messages.error(request, _("You have reached the limit of %(limit)s accounts for your current plan. Please upgrade to restore this account.") % {'limit': limit})
            return redirect('pricing')
            
        account.is_active = True
        account.save()
        messages.success(request, _("Account restored successfully!"))
        ph_capture(request.user, 'account_restored', {})
        return redirect('account-list')


class AccountQuickCreateView(LoginRequiredMixin, View):
    """AJAX endpoint for creating an account from a modal and returning JSON."""

    def post(self, request):
        if not request.user.profile.can_add_account():
            return JsonResponse({
                'success': False, 
                'errors': {'__all__': [_("Account limit reached. Please upgrade to add more.")]}
            }, status=403)
            
        form = AccountForm(request.POST, user=request.user)
        if form.is_valid():
            account = form.save(commit=False)
            account.user = request.user
            account.save()
            ph_capture(request.user, 'account_quick_created', {'account_type': account.account_type})
            return JsonResponse({
                'success': True,
                'id': account.pk,
                'name': str(account),
            })
        return JsonResponse({'success': False, 'errors': form.errors}, status=400)


class TransferCreateView(LoginRequiredMixin, CreateView):
    model = Transfer
    form_class = TransferForm
    template_name = 'expenses/transfer_form.html'
    success_url = reverse_lazy('transfer-list')

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def dispatch(self, request, *args, **kwargs):
        if request.user.username == 'demo':
            messages.warning(request, _("Inter-account transfers are disabled in the demo to keep things simple. Please use Goal Contributions instead!"))
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        from_account_val = self.request.GET.get('from_account')
        if from_account_val:
            import uuid
            try:
                # Try to parse as UUID first
                uuid.UUID(from_account_val)
                account = Account.objects.get(uuid=from_account_val, user=self.request.user)
                initial['from_account'] = account.pk
            except (ValueError, TypeError, Account.DoesNotExist):
                # Fallback to pk just in case
                try:
                    account = Account.objects.get(pk=int(from_account_val), user=self.request.user)
                    initial['from_account'] = account.pk
                except (ValueError, TypeError, Account.DoesNotExist):
                    pass
        return initial

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user

        try:
            response = super().form_valid(form)
            messages.success(self.request, _("Transfer completed successfully!"))
            ph_capture(self.request.user, 'transfer_created', {'amount': str(self.object.amount), 'currency': getattr(self.object, 'currency', ''), 'is_cross_currency': bool(getattr(self.object, 'exchange_rate', None) and self.object.exchange_rate != 1)})
            return response
        except IntegrityError:
            messages.error(self.request, _("Duplicate transfer found! This transfer has already been completed."))
            return self.form_invalid(form)
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to complete transfer because currency conversion failed or transfer data is invalid."))
            return self.form_invalid(form)

class TransferListView(HtmxPartialTemplateMixin, LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Transfer
    template_name = 'expenses/transfer_list.html'
    htmx_template_name = 'expenses/partials/_transfer_list.html'
    context_object_name = 'transfers'
    paginate_by = 10

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user).select_related('from_account', 'to_account').order_by('-date')

class TransferUpdateView(LoginRequiredMixin, UUIDOrIntLookupMixin, UpdateView):
    model = Transfer
    form_class = TransferForm
    template_name = 'expenses/transfer_form.html'
    success_url = reverse_lazy('transfer-list')

    def get_success_url(self):
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url
        return super().get_success_url()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)

    def form_valid(self, form):
        try:
            response = super().form_valid(form)
            messages.success(self.request, _("Transfer updated successfully!"))
            ph_capture(self.request.user, 'transfer_updated', {})
            return response
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to update transfer because currency conversion failed or transfer data is invalid."))
            return self.form_invalid(form)

class TransferDeleteView(LoginRequiredMixin, UUIDOrIntLookupMixin, DeleteView):
    model = Transfer
    template_name = 'expenses/transfer_confirm_delete.html'
    success_url = reverse_lazy('transfer-list')

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Transfer deleted successfully!"))
        response = super().form_valid(form)
        ph_capture(self.request.user, 'transfer_deleted', {})
        return response

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return get_safe_redirect_url(self.request, next_url, reverse_lazy('transfer-list'))
        return reverse_lazy('transfer-list')

class AccountDetailView(LoginRequiredMixin, View):
    template_name = 'expenses/account_detail.html'
    def get(self, request, pk):
        from .mixins import process_user_recurring_transactions
        if request.user.is_authenticated:
            process_user_recurring_transactions(request.user)
            
        account = get_object_by_uuid_or_pk(Account, pk, user=request.user)
        redirect_response = redirect_to_uuid_url_if_needed(request, account)
        if redirect_response:
            return redirect_response
        if request.user.is_authenticated and request.user.profile.is_account_locked(account):
            messages.error(request, _("This account is locked. Please upgrade your plan to view its history."))
            return redirect('pricing')

        if account.is_active:
            try:
                account.display_balance = LedgerReadService.get_account_balance(account)
            except Exception:
                account.display_balance = account.balance
        else:
            account.display_balance = account.balance

        if account.account_type in deposit_codes():
            accrued = _compute_deposit_value(account, Decimal(str(account.display_balance)))
            deposit_principal = account.deposit_principal if account.deposit_principal is not None else Decimal(str(account.display_balance))
            if getattr(account, 'show_accrued_balance', True) and (accrued != deposit_principal or account.deposit_rate is not None):
                account.accrued_value = accrued
                account.effective_principal = deposit_principal
                account.has_accrued_value = True
            else:
                account.has_accrued_value = False

            if account.deposit_maturity_date and account.deposit_rate is not None:
                from ..account_valuation import get_current_deposit
                account.maturity_value = get_current_deposit(account, ledger_balance=Decimal(str(account.display_balance)), today=account.deposit_maturity_date)
                account.has_maturity_value = True
            else:
                account.has_maturity_value = False
        else:
            account.has_accrued_value = False
            account.has_maturity_value = False

        query = request.GET.get('q', '')
        selected_tx_type = request.GET.get('tx_type', '')
        
        # Get all expenses, incomes, and transfers for this account
        expenses = Expense.objects.filter(user=request.user, account=account).select_related('category_fk')
        incomes = Income.objects.filter(user=request.user, account=account).select_related('source_fk')
        transfers_from = Transfer.objects.filter(user=request.user, from_account=account).select_related('to_account')
        transfers_to = Transfer.objects.filter(user=request.user, to_account=account).select_related('from_account')
        contributions = GoalContribution.objects.filter(goal__user=request.user, account=account).select_related('goal')
        loan_repayments = LoanRepayment.objects.filter(loan__user=request.user, from_account=account).select_related('loan')
        capital_events = CapitalEvent.objects.filter(user=request.user, account=account).select_related('linked_loan')

        if selected_tx_type == 'EXPENSE':
            incomes = Income.objects.none()
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            contributions = GoalContribution.objects.none()
            loan_repayments = LoanRepayment.objects.none()
            capital_events = CapitalEvent.objects.none()
        elif selected_tx_type == 'INCOME':
            expenses = Expense.objects.none()
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            contributions = GoalContribution.objects.none()
            loan_repayments = LoanRepayment.objects.none()
            capital_events = CapitalEvent.objects.none()
        elif selected_tx_type == 'TRANSFER':
            expenses = Expense.objects.none()
            incomes = Income.objects.none()
            contributions = GoalContribution.objects.none()
            loan_repayments = LoanRepayment.objects.none()
            capital_events = CapitalEvent.objects.none()
        elif selected_tx_type == 'SAVINGS':
            expenses = Expense.objects.none()
            incomes = Income.objects.none()
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            loan_repayments = LoanRepayment.objects.none()
            capital_events = CapitalEvent.objects.none()
        elif selected_tx_type == 'LOAN':
            expenses = Expense.objects.none()
            incomes = Income.objects.none()
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            contributions = GoalContribution.objects.none()
            capital_events = CapitalEvent.objects.none()
        elif selected_tx_type == 'CAPITAL':
            expenses = Expense.objects.none()
            incomes = Income.objects.none()
            transfers_from = Transfer.objects.none()
            transfers_to = Transfer.objects.none()
            contributions = GoalContribution.objects.none()
            loan_repayments = LoanRepayment.objects.none()

        if query:
            expenses = expenses.filter(Q(description__icontains=query) | Q(category__icontains=query))
            incomes = incomes.filter(Q(description__icontains=query) | Q(source__icontains=query))
            transfers_from = transfers_from.filter(Q(description__icontains=query))
            transfers_to = transfers_to.filter(Q(description__icontains=query))
            contributions = contributions.filter(Q(goal__name__icontains=query))
            loan_repayments = loan_repayments.filter(Q(loan__name__icontains=query))
            capital_events = capital_events.filter(Q(note__icontains=query) | Q(subtype__icontains=query))

        expenses = expenses.order_by('-date')
        incomes = incomes.order_by('-date')
        capital_events = capital_events.order_by('-date')
        
        base_currency = request.user.profile.currency if hasattr(request.user, 'profile') else '₹'
        
        # Calculate Net Total for Filtered Items (In Account's Currency)
        # Handle expenses
        exp_total = Decimal('0.00')
        for e in expenses:
            if e.currency != account.currency:
                rate = get_exchange_rate(e.currency, account.currency)
                exp_total += (e.amount * rate).quantize(Decimal('0.01'))
            else:
                exp_total += e.amount

        # Handle incomes
        inc_total = Decimal('0.00')
        for i in incomes:
            if i.currency != account.currency:
                rate = get_exchange_rate(i.currency, account.currency)
                inc_total += (i.amount * rate).quantize(Decimal('0.01'))
            else:
                inc_total += i.amount
        
        # Transfers are in the currency of the from_account
        out_total = sum(t.amount for t in transfers_from) # transfers_from were from THIS account
                
        in_total = Decimal('0.00')
        for t in transfers_to:
            if t.from_account.currency != account.currency:
                rate = get_exchange_rate(t.from_account.currency, account.currency)
                in_total += (t.amount * rate).quantize(Decimal('0.01'))
            else:
                in_total += t.amount
        
        # Goal contributions are in the goal's currency
        sav_total = Decimal('0.00')
        for c in contributions:
            if c.goal.currency != account.currency:
                rate = get_exchange_rate(c.goal.currency, account.currency)
                sav_total += (c.amount * rate).quantize(Decimal('0.01'))
            else:
                sav_total += c.amount
        
        # Loan repayments are in the loan's currency
        loan_total = Decimal('0.00')
        for lr in loan_repayments:
            if lr.loan.currency != account.currency:
                rate = get_exchange_rate(lr.loan.currency, account.currency)
                loan_total += (lr.amount * rate).quantize(Decimal('0.01'))
            else:
                loan_total += lr.amount

        # Capital events are in the event's currency
        cap_total = Decimal('0.00')
        for ce in capital_events:
            if ce.include_in_net_worth:
                if ce.currency != account.currency:
                    rate = get_exchange_rate(ce.currency, account.currency)
                    cap_total += (ce.amount * rate).quantize(Decimal('0.01'))
                else:
                    cap_total += ce.amount

        filtered_net_total = inc_total + in_total - exp_total - out_total - sav_total - loan_total - cap_total

        # Combine everything and sort by date descending
        # We'll add 'transaction_type', 'display_currency', and 'base_amount_display' to each for the template
        for e in expenses:
            e.transaction_type = 'EXPENSE'
            e.display_currency = e.currency
            # If the expense is in a different currency than the account, show the base currency equivalent (e.g. ₹ equivalent if looking at a USD account)
            e.base_amount_display = e.base_amount if e.currency != account.currency else None
            
        for i in incomes:
            i.transaction_type = 'INCOME'
            i.display_currency = i.currency
            i.base_amount_display = i.base_amount if i.currency != account.currency else None

        for t in transfers_from:
            t.transaction_type = 'TRANSFER_OUT'
            t.display_amount = -t.amount
            t.display_currency = account.currency # Always the current account's currency for simplicity
            t.base_amount_display = None # Transfers from account are always in account's currency
            
        for t in transfers_to:
            t.transaction_type = 'TRANSFER_IN'
            t.display_amount = t.amount
            t.display_currency = t.from_account.currency
            if t.from_account.currency != account.currency:
                rate = get_exchange_rate(t.from_account.currency, account.currency)
                t.base_amount_display = (t.amount * rate).quantize(Decimal('0.01'))
            else:
                t.base_amount_display = None

        for c in contributions:
            c.transaction_type = 'SAVINGS'
            c.display_currency = c.goal.currency
            if c.goal.currency != account.currency:
                rate = get_exchange_rate(c.goal.currency, account.currency)
                c.base_amount_display = (c.amount * rate).quantize(Decimal('0.01'))
            else:
                c.base_amount_display = None
            c.description = _("Savings: %(goal)s") % {'goal': c.goal.name}

        for lr in loan_repayments:
            lr.transaction_type = 'LOAN_REPAYMENT'
            lr.display_currency = lr.loan.currency
            if lr.loan.currency != account.currency:
                rate = get_exchange_rate(lr.loan.currency, account.currency)
                lr.base_amount_display = (lr.amount * rate).quantize(Decimal('0.01'))
            else:
                lr.base_amount_display = None
            lr.description = _("Loan Repayment: %(loan)s") % {'loan': lr.loan.name}

        for ce in capital_events:
            ce.transaction_type = 'CAPITAL_EVENT'
            ce.display_currency = ce.currency
            ce.base_amount_display = ce.base_amount if ce.currency != account.currency else None
            ce.description = ce.note if ce.note else ce.get_subtype_display()

        ledger = sorted(
            chain(expenses, incomes, transfers_from, transfers_to, contributions, loan_repayments, capital_events),
            key=lambda x: x.date,
            reverse=True
        )

        # Pagination
        
        paginator = Paginator(ledger, 20)
        page_number = request.GET.get('page')
        page_obj = paginator.get_page(page_number)

        context = {
            'account': account,
            'ledger': page_obj,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
            'currency_symbol': account.currency,
            'base_currency_symbol': base_currency,
            'search_query': query,
            'selected_tx_type': selected_tx_type,
            'filtered_net_total': filtered_net_total,
            'trend_data': self.get_trend_data(account, request.user),
        }
        from django.utils.cache import patch_vary_headers
        template_name = 'expenses/partials/_account_detail.html' if request.headers.get('HX-Request') == 'true' else self.template_name
        response = render(request, template_name, context)
        patch_vary_headers(response, ['HX-Request'])
        return response

    def get_trend_data(self, account, user):
        
        today = date.today()
        
        # Determine range and frequency
        first_tx = self.get_all_transactions(account, user).order_by('date').first()
        earliest_date = first_tx['date'] if first_tx else today
        
        use_monthly = (today - earliest_date).days > 90
        
        if use_monthly:
            # Monthly for last 12 months
            labels = []
            values = []
            
            # Get all transactions for last 13 months (to get starting balance of 12th month)
            start_date = (today.replace(day=1) - timedelta(days=365)).replace(day=1)
            transactions = self.get_all_transactions(account, user, start_date)
            
            # Group by month
            monthly_diffs = defaultdict(Decimal)
            for tx in transactions:
                # Month key: '2026-04'
                key = tx['date'].strftime('%Y-%m')
                monthly_diffs[key] += tx['net_amount']
                
            current_bal = LedgerReadService.get_account_balance(account)
            check_date = today
            
            for i in range(13): # 12 months + start point
                labels.append(check_date.strftime('%b %y'))
                values.append(float(current_bal))
                
                key = check_date.strftime('%Y-%m')
                current_bal -= monthly_diffs[key]
                
                # Previous month
                check_date = (check_date.replace(day=1) - timedelta(days=1))
                
            labels.reverse()
            values.reverse()
            return {'labels': labels, 'values': values, 'type': 'monthly'}
        else:
            # Daily for last 30 days
            labels = []
            values = []
            
            start_date = today - timedelta(days=30)
            transactions = self.get_all_transactions(account, user, start_date)
            
            # Group by date
            daily_diffs = defaultdict(Decimal)
            for tx in transactions:
                daily_diffs[tx['date']] += tx['net_amount']
                
            current_bal = LedgerReadService.get_account_balance(account)
            
            for i in range(31): # 30 days + start point
                d = today - timedelta(days=i)
                labels.append(d.strftime('%d %b'))
                values.append(float(current_bal))
                
                current_bal -= daily_diffs.get(d, Decimal('0.00'))
                
            labels.reverse()
            values.reverse()
            return {'labels': labels, 'values': values, 'type': 'daily'}

    def get_all_transactions(self, account, user, start_date=None):
        """Returns a combined queryset-like of all transactions affecting account balance."""
        
        expenses = Expense.objects.filter(user=user, account=account).select_related('category_fk')
        incomes = Income.objects.filter(user=user, account=account).select_related('source_fk')
        transfers_out = Transfer.objects.filter(user=user, from_account=account)
        transfers_in = Transfer.objects.filter(user=user, to_account=account).select_related('from_account')
        contributions = GoalContribution.objects.filter(goal__user=user, account=account).select_related('goal')
        loan_repayments = LoanRepayment.objects.filter(loan__user=user, from_account=account).select_related('loan')
        capital_events = CapitalEvent.objects.filter(user=user, account=account, include_in_net_worth=True)
        
        if start_date:
            expenses = expenses.filter(date__gte=start_date)
            incomes = incomes.filter(date__gte=start_date)
            transfers_out = transfers_out.filter(date__gte=start_date)
            transfers_in = transfers_in.filter(date__gte=start_date)
            contributions = contributions.filter(date__gte=start_date)
            loan_repayments = loan_repayments.filter(date__gte=start_date)
            capital_events = capital_events.filter(date__gte=start_date)
            
        # For simplicity, we'll manually handle the currency conversion logic in Python 
        # because doing it in SQL with exchange rates is complex and might be slow for few records.
        # But we'll collect them all.
        
        all_tx = []
        for e in expenses.values('date', 'amount', 'currency'):
            amt = e['amount']
            if e['currency'] != account.currency:
                rate = get_exchange_rate(e['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': e['date'], 'net_amount': -amt})
            
        for i in incomes.values('date', 'amount', 'currency'):
            amt = i['amount']
            if i['currency'] != account.currency:
                rate = get_exchange_rate(i['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': i['date'], 'net_amount': amt})
            
        for t in transfers_out.values('date', 'amount'):
            all_tx.append({'date': t['date'], 'net_amount': -t['amount']})
            
        for t in transfers_in:
            amt = t.amount
            if t.from_account.currency != account.currency:
                rate = get_exchange_rate(t.from_account.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': t.date, 'net_amount': amt})
            
        for c in contributions:
            amt = c.amount
            if c.goal.currency != account.currency:
                rate = get_exchange_rate(c.goal.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': c.date, 'net_amount': -amt})

        for lr in loan_repayments:
            amt = lr.amount
            if lr.loan.currency != account.currency:
                rate = get_exchange_rate(lr.loan.currency, account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': lr.date, 'net_amount': -amt})

        for ce in capital_events.values('date', 'amount', 'currency'):
            amt = ce['amount']
            if ce['currency'] != account.currency:
                rate = get_exchange_rate(ce['currency'], account.currency)
                amt = (amt * rate).quantize(Decimal('0.01'))
            all_tx.append({'date': ce['date'], 'net_amount': -amt})
            
        # Return as a simple list of dicts
        # Sort it if needed, but the grouping logic handles it
        
        # Wrap in a way that allows .order_by('date').first() if no start_date
        class PseudoQS(list):
            def order_by(self, field):
                rev = field.startswith('-')
                f = field.lstrip('-')
                return PseudoQS(sorted(self, key=lambda x: x[f], reverse=rev))
            def first(self):
                return self[0] if self else None
                
        return PseudoQS(all_tx)


class RecordMaturityIncomeView(LoginRequiredMixin, UUIDOrIntLookupMixin, View):
    """
    Action view to record accrued interest earned on an FD/RD account as an
    Income entry under 'Investment Returns'.
    """
    def post(self, request, *args, **kwargs):
        account = get_object_by_uuid_or_pk(Account, kwargs.get('pk') or kwargs.get('uuid'), user=request.user)

        today_val = account.deposit_closed_date or account.deposit_maturity_date or date.today()
        current_val = get_current(account, today=today_val)
        baseline_val = get_baseline(account, today=today_val) or Decimal('0.00')
        interest_earned = (current_val - baseline_val).quantize(Decimal('0.01'))

        if interest_earned <= Decimal('0.00'):
            messages.warning(request, _("No accrued interest earned to record for %(name)s.") % {'name': account.name})
        else:
            Income.objects.create(
                user=request.user,
                date=today_val,
                amount=interest_earned,
                currency=account.currency,
                source_type='Investment Returns',
                source=f"Interest from {account.name}",
                account=account,
                description=f"Accrued interest earned on deposit {account.name}",
            )
            messages.success(
                request,
                _("Recorded %(currency)s%(amount)s interest for %(name)s as Income under 'Investment Returns'.") % {
                    'currency': account.currency,
                    'amount': interest_earned,
                    'name': account.name,
                }
            )
            ph_capture(request.user, 'maturity_income_recorded', {})

        redirect_url = request.META.get('HTTP_REFERER') or reverse_lazy('account-list')
        return redirect(redirect_url)


def search_amfi_schemes(request):
    """
    Search endpoint over local AMFIScheme search mirror with live MFapi fallback (SPEC §3a).
    Returns top 15 matching scheme names/codes.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'results': []}, status=401)
    
    q = request.GET.get('q', '').strip()
    if not q or len(q) < 2:
        return JsonResponse({'results': []})
    
    from ..models import AMFIScheme
    results = []
    
    # 1. Try local AMFIScheme table first
    schemes = AMFIScheme.objects.filter(
        Q(scheme_name__icontains=q) | Q(scheme_code__icontains=q)
    )[:15]
    
    if schemes.exists():
        results = [
            {'scheme_code': s.scheme_code, 'scheme_name': s.scheme_name, 'isin': s.isin}
            for s in schemes
        ]
    else:
        # 2. Live fallback to MFapi.in search API
        try:
            import urllib.parse

            import requests
            encoded_q = urllib.parse.quote(q)
            url = f"https://api.mfapi.in/mf/search?q={encoded_q}"
            resp = requests.get(url, headers={'User-Agent': 'TrackMyRupee/1.0'}, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                for item in data[:15]:
                    code = str(item.get('schemeCode'))
                    name = item.get('schemeName')
                    if code and name:
                        results.append({'scheme_code': code, 'scheme_name': name, 'isin': None})
        except Exception:
            pass
            
    return JsonResponse({'results': results})


def refresh_holding_nav(request, pk):
    """
    Manual "Refresh Now" action on a holding (SPEC §3a point 3).
    Bypasses circuit-breaker backoff and forces immediate fetch.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
    
    from django.shortcuts import get_object_or_404

    from ..models import Holding
    from ..nav_provider import NAVFetchService

    holding = get_object_or_404(Holding, pk=pk, account__user=request.user)
    if not holding.scheme_code:
        messages.warning(request, _("No scheme code configured for this holding."))
    else:
        service = NAVFetchService()
        cache, success = service.fetch_scheme(holding.scheme_code, force=True)
        if success:
            messages.success(
                request,
                _("Successfully updated NAV for %(name)s (latest NAV: %(nav)s).") % {
                    'name': holding.instrument_name,
                    'nav': cache.latest_nav if cache else '',
                }
            )
            ph_capture(request.user, 'holding_nav_refreshed', {})
        else:
            messages.warning(
                request,
                _("Failed to fetch fresh NAV for %(name)s. Using cached valuation.") % {
                    'name': holding.instrument_name,
                }
            )
            
    return redirect('account-detail', pk=holding.account.pk)


def holding_create_view(request, pk=None):
    """
    Creates a new Holding under an account.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
        
    from decimal import InvalidOperation

    from ..models import AMFIScheme, Holding
    from ..nav_provider import NAVFetchService

    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or reverse_lazy('holding-list')
    account_id = pk or request.POST.get('account_id')
    
    if not account_id:
        messages.error(request, _("Please select an investment account."))
        return redirect(next_url)

    account = get_object_by_uuid_or_pk(Account, account_id, user=request.user)
    if request.method == 'POST':
        name = request.POST.get('instrument_name', '').strip()
        scheme_code = request.POST.get('scheme_code', '').strip()
        isin = request.POST.get('isin', '').strip()
        units_str = request.POST.get('units', '0')
        avg_cost_str = request.POST.get('avg_cost', '0')
        
        if not name:
            messages.error(request, _("Holding name is required."))
            return redirect(next_url)

        # Smart Auto-Resolution: if scheme_code is blank, search by fund name
        if not scheme_code and name:
            local_match = AMFIScheme.objects.filter(scheme_name__icontains=name).first()
            if local_match:
                scheme_code = local_match.scheme_code
            else:
                try:
                    import urllib.parse

                    import requests
                    encoded_q = urllib.parse.quote(name)
                    url = f"https://api.mfapi.in/mf/search?q={encoded_q}"
                    resp = requests.get(url, headers={'User-Agent': 'TrackMyRupee/1.0'}, timeout=5)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data:
                            scheme_code = str(data[0].get('schemeCode'))
                except Exception:
                    pass

        try:
            units = Decimal(units_str)
            avg_cost = Decimal(avg_cost_str)
        except (ValueError, InvalidOperation):
            messages.error(request, _("Invalid units or purchase price format."))
            return redirect(next_url)
            
        if units <= Decimal('0') or avg_cost <= Decimal('0'):
            messages.error(request, _("Units and average price must be greater than zero."))
            return redirect(next_url)
            
        holding = Holding.objects.create(
            account=account,
            instrument_name=name,
            instrument_type='MF' if account.account_type in ('MUTUAL_FUND', 'INVESTMENT') else 'OTHER',
            units=units,
            avg_cost=avg_cost,
            currency=account.currency,
            scheme_code=scheme_code or None,
            isin=isin or None,
            is_active=True,
        )
        
        if scheme_code:
            service = NAVFetchService()
            service.fetch_scheme(scheme_code, force=True)
            
        messages.success(request, _("Holding '%(name)s' added successfully.") % {'name': name})
        ph_capture(request.user, 'holding_created', {'has_scheme_code': bool(holding.scheme_code if hasattr(holding, 'scheme_code') else False)})
    return redirect(next_url)


def holding_delete_view(request, pk):
    """
    Deactivates a Holding from an account.
    """
    if not request.user.is_authenticated:
        return redirect('account_login')
        
    from django.shortcuts import get_object_or_404

    from ..models import Holding

    holding = get_object_or_404(Holding, pk=pk, account__user=request.user)
    holding.is_active = False
    holding.save()
    messages.success(request, _("Holding '%(name)s' removed.") % {'name': holding.instrument_name})
    ph_capture(request.user, 'holding_deleted', {})
    
    next_url = request.GET.get('next') or request.META.get('HTTP_REFERER') or reverse_lazy('holding-list')
    return redirect(next_url)


class HoldingsListView(LoginRequiredMixin, ListView):
    """
    Dedicated Holdings & Investment Portfolio page for all active user holdings.
    """
    model = Holding
    template_name = 'expenses/holding_list.html'
    context_object_name = 'holdings'

    def get_queryset(self):
        return Holding.objects.filter(
            account__user=self.request.user,
            is_active=True,
        ).select_related('account').prefetch_related('valuations')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        holdings = context['holdings']
        
        total_valuation = Decimal('0.00')
        total_cost = Decimal('0.00')
        
        for h in holdings:
            val = h.valuations.first()
            if val:
                total_valuation += val.value
            else:
                total_valuation += h.cost_basis
            total_cost += h.cost_basis
            
        unrealized_gain = total_valuation - total_cost
        gain_pct = ((unrealized_gain / total_cost) * Decimal('100')).quantize(Decimal('0.1')) if total_cost > 0 else Decimal('0.0')
        
        context['total_valuation'] = total_valuation
        context['total_cost'] = total_cost
        context['unrealized_gain'] = unrealized_gain
        context['gain_pct'] = gain_pct
        context['user_currency'] = self.request.user.profile.currency
        context['user_accounts'] = Account.objects.filter(
            user=self.request.user,
            is_active=True,
            account_type__in=['MUTUAL_FUND', 'INVESTMENT', 'STOCKS', 'OTHER_ASSETS', 'NPS', 'PF', 'GOLD', 'SAVINGS']
        )
        return context
