from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from itertools import chain

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from expenses.views.utils import get_safe_redirect_url
from finance_tracker.plans import get_limit

from ..forms import AccountForm, TransferForm
from ..ledger_read_service import LedgerReadService
from ..models import (
    Account,
    CapitalEvent,
    Expense,
    GoalContribution,
    Income,
    LoanRepayment,
    Transfer,
    _run_ledger_shadow,
)
from ..utils import get_exchange_rate
from .mixins import RecurringTransactionMixin, UUIDOrIntLookupMixin
from .utils import get_object_by_uuid_or_pk, redirect_to_uuid_url_if_needed


class AccountListView(LoginRequiredMixin, ListView):
    model = Account
    template_name = 'expenses/account_list.html'
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
            queryset = [acc for acc in queryset if acc.account_type == account_type]
            
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

            delta = now - account.updated_at
            account.days_since_update = delta.days

            try:
                rate = get_exchange_rate(account.currency, user_currency)
            except Exception:
                rate = Decimal('1.0')
            total_balance += Decimal(account.display_balance) * Decimal(str(rate))

        # Group accounts
        group_order = ['BANK', 'CASH', 'INVESTMENT', 'FIXED_DEPOSIT', 'CREDIT_CARD', 'OTHER']
        group_labels = dict(Account.ACCOUNT_TYPES)
        grouped_accounts = []
        for g_type in group_order:
            group_accs = [a for a in accounts if a.account_type == g_type]
            if not group_accs:
                continue
            
            # Calculate group total balance in user currency
            group_total = Decimal('0.00')
            for acc in group_accs:
                try:
                    rate = get_exchange_rate(acc.currency, user_currency)
                except Exception:
                    rate = Decimal('1.0')
                group_total += Decimal(acc.display_balance) * Decimal(str(rate))
                
            grouped_accounts.append({
                'type': g_type,
                'label': group_labels.get(g_type, g_type),
                'accounts': group_accs,
                'count': len(group_accs),
                'total': group_total.quantize(Decimal('0.01')),
            })

        context['grouped_accounts'] = grouped_accounts
        context['account_types'] = Account.ACCOUNT_TYPES
        context['selected_type'] = self.request.GET.get('type', '')
        context['selected_type_label'] = dict(Account.ACCOUNT_TYPES).get(context['selected_type'], '')
        context['current_status'] = current_status
        context['total_balance'] = total_balance.quantize(Decimal('0.01'))
        context['total_balance_currency'] = user_currency
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
        return super().form_valid(form)

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
            return response
        except (RuntimeError, ValidationError):
            messages.error(self.request, _("Unable to complete transfer because currency conversion failed or transfer data is invalid."))
            return self.form_invalid(form)

class TransferListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Transfer
    template_name = 'expenses/transfer_list.html'
    context_object_name = 'transfers'

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
        return super().form_valid(form)

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

        query = request.GET.get('q', '')
        
        # Get all expenses, incomes, and transfers for this account
        expenses = Expense.objects.filter(user=request.user, account=account).select_related('category_fk')
        incomes = Income.objects.filter(user=request.user, account=account).select_related('source_fk')
        transfers_from = Transfer.objects.filter(user=request.user, from_account=account).select_related('to_account')
        transfers_to = Transfer.objects.filter(user=request.user, to_account=account).select_related('from_account')
        contributions = GoalContribution.objects.filter(goal__user=request.user, account=account).select_related('goal')
        loan_repayments = LoanRepayment.objects.filter(loan__user=request.user, from_account=account).select_related('loan')
        capital_events = CapitalEvent.objects.filter(user=request.user, account=account).select_related('linked_loan')

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
            'filtered_net_total': filtered_net_total,
            'trend_data': self.get_trend_data(account, request.user),
        }
        return render(request, self.template_name, context)

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
