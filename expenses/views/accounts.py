from collections import defaultdict
from decimal import Decimal
from itertools import chain
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from ..forms import AccountForm, TransferForm
from ..models import Account, Expense, GoalContribution, Income, Transfer
from ..utils import get_exchange_rate
from .mixins import RecurringTransactionMixin


class AccountListView(LoginRequiredMixin, ListView):
    model = Account
    template_name = 'expenses/account_list.html'
    context_object_name = 'accounts'

    def get_queryset(self):
        from .mixins import process_user_recurring_transactions
        process_user_recurring_transactions(self.request.user)
        queryset = Account.objects.filter(user=self.request.user).order_by('name')
        
        account_type = self.request.GET.get('type')
        if account_type:
            queryset = queryset.filter(account_type=account_type)
            
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['account_types'] = Account.ACCOUNT_TYPES
        context['selected_type'] = self.request.GET.get('type', '')
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
            from finance_tracker.plans import get_limit
            limit = get_limit(self.request.user.profile.active_tier, 'accounts')
            messages.error(self.request, _("You have reached the limit of %(limit)s accounts for your current plan. Please upgrade to add more.") % {'limit': limit})
            return redirect('pricing')
        form.instance.user = self.request.user
        messages.success(self.request, _("Account created successfully!"))
        return super().form_valid(form)

class AccountUpdateView(LoginRequiredMixin, UpdateView):
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

    def form_valid(self, form):
        messages.success(self.request, _("Account updated successfully!"))
        return super().form_valid(form)

class AccountDeleteView(LoginRequiredMixin, DeleteView):
    model = Account
    template_name = 'expenses/account_delete_confirm.html'
    success_url = reverse_lazy('account-list')

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Account deleted successfully."))
        return super().form_valid(form)

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

    def dispatch(self, request, *args, **kwargs):
        if request.user.username == 'demo':
            messages.warning(request, _("Inter-account transfers are disabled in the demo to keep things simple. Please use Goal Contributions instead!"))
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.user = self.request.user
        messages.success(self.request, _("Transfer completed successfully!"))
        return super().form_valid(form)

class TransferListView(LoginRequiredMixin, RecurringTransactionMixin, ListView):
    model = Transfer
    template_name = 'expenses/transfer_list.html'
    context_object_name = 'transfers'

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user).order_by('-date')

class TransferUpdateView(LoginRequiredMixin, UpdateView):
    model = Transfer
    form_class = TransferForm
    template_name = 'expenses/transfer_form.html'
    success_url = reverse_lazy('transfer-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)

    def form_valid(self, form):
        messages.success(self.request, _("Transfer updated successfully!"))
        return super().form_valid(form)

class TransferDeleteView(LoginRequiredMixin, DeleteView):
    model = Transfer
    template_name = 'expenses/transfer_confirm_delete.html'
    success_url = reverse_lazy('transfer-list')

    def get_queryset(self):
        return Transfer.objects.filter(user=self.request.user)
    
    def delete(self, request, *args, **kwargs):
        messages.success(self.request, _("Transfer deleted successfully!"))
        return super().delete(request, *args, **kwargs)

    def get_success_url(self):
        next_url = self.request.GET.get('next') or self.request.POST.get('next')
        if next_url:
            return next_url
        return reverse_lazy('transfer-list')

class AccountDetailView(LoginRequiredMixin, View):
    template_name = 'expenses/account_detail.html'
    def get(self, request, pk):
        from .mixins import process_user_recurring_transactions
        if request.user.is_authenticated:
            process_user_recurring_transactions(request.user)
            
        account = get_object_or_404(Account, pk=pk, user=request.user)
        query = request.GET.get('q', '')
        
        # Get all expenses, incomes, and transfers for this account
        expenses = Expense.objects.filter(user=request.user, account=account)
        incomes = Income.objects.filter(user=request.user, account=account)
        transfers_from = Transfer.objects.filter(user=request.user, from_account=account)
        transfers_to = Transfer.objects.filter(user=request.user, to_account=account)
        contributions = GoalContribution.objects.filter(goal__user=request.user, account=account)

        if query:
            expenses = expenses.filter(Q(description__icontains=query) | Q(category__icontains=query))
            incomes = incomes.filter(Q(description__icontains=query) | Q(source__icontains=query))
            transfers_from = transfers_from.filter(Q(description__icontains=query))
            transfers_to = transfers_to.filter(Q(description__icontains=query))
            contributions = contributions.filter(Q(goal__name__icontains=query))

        expenses = expenses.order_by('-date')
        incomes = incomes.order_by('-date')
        
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
        
        filtered_net_total = inc_total + in_total - exp_total - out_total - sav_total

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

        ledger = sorted(
            chain(expenses, incomes, transfers_from, transfers_to, contributions),
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
                
            current_bal = account.balance
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
                
            current_bal = account.balance
            
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
        from django.db.models import F, Value, CharField, DecimalField
        from django.db.models.functions import Coalesce
        
        expenses = Expense.objects.filter(user=user, account=account)
        incomes = Income.objects.filter(user=user, account=account)
        transfers_out = Transfer.objects.filter(user=user, from_account=account)
        transfers_in = Transfer.objects.filter(user=user, to_account=account).select_related('from_account')
        contributions = GoalContribution.objects.filter(goal__user=user, account=account).select_related('goal')
        
        if start_date:
            expenses = expenses.filter(date__gte=start_date)
            incomes = incomes.filter(date__gte=start_date)
            transfers_out = transfers_out.filter(date__gte=start_date)
            transfers_in = transfers_in.filter(date__gte=start_date)
            contributions = contributions.filter(date__gte=start_date)
            
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
