from decimal import Decimal
from itertools import chain

from django.contrib import messages
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
        return Account.objects.filter(user=self.request.user).order_by('name')

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
        
        # Calculate Net Total for Filtered Items (In Base Currency)
        # Note: expenses and incomes already have `base_amount`. 
        # For transfers, we'll calculate based on the current rates or what's stored.
        exp_total = sum(e.base_amount for e in expenses)
        inc_total = sum(i.base_amount for i in incomes)
        
        out_total = Decimal('0.00')
        for t in transfers_from:
            if t.from_account.currency != base_currency:
                rate = get_exchange_rate(t.from_account.currency, base_currency)
                out_total += (t.amount * rate).quantize(Decimal('0.01'))
            else:
                out_total += t.amount
                
        in_total = Decimal('0.00')
        for t in transfers_to:
            if t.to_account.currency != base_currency:
                rate = get_exchange_rate(t.to_account.currency, base_currency)
                in_total += (t.amount * rate).quantize(Decimal('0.01'))
            else:
                in_total += t.amount
        
        sav_total = Decimal('0.00')
        for c in contributions:
            if account.currency != base_currency:
                rate = get_exchange_rate(account.currency, base_currency)
                sav_total += (c.amount * rate).quantize(Decimal('0.01'))
            else:
                sav_total += c.amount
        
        filtered_net_total = inc_total + in_total - exp_total - out_total - sav_total

        # Combine everything and sort by date descending
        # We'll add 'transaction_type', 'display_currency', and 'base_amount_display' to each for the template
        for e in expenses:
            e.transaction_type = 'EXPENSE'
            e.display_currency = e.currency
            e.base_amount_display = e.base_amount if e.currency != base_currency else None
        for i in incomes:
            i.transaction_type = 'INCOME'
            i.display_currency = i.currency
            i.base_amount_display = i.base_amount if i.currency != base_currency else None
        for t in transfers_from:
            t.transaction_type = 'TRANSFER_OUT'
            t.display_amount = -t.amount
            t.display_currency = t.to_account.currency
            if t.to_account.currency != base_currency:
                rate = get_exchange_rate(t.to_account.currency, base_currency)
                t.base_amount_display = (t.amount * rate).quantize(Decimal('0.01'))
            else:
                t.base_amount_display = None
        for t in transfers_to:
            t.transaction_type = 'TRANSFER_IN'
            t.display_amount = t.amount
            t.display_currency = t.from_account.currency
            if t.from_account.currency != base_currency:
                rate = get_exchange_rate(t.from_account.currency, base_currency)
                t.base_amount_display = (t.amount * rate).quantize(Decimal('0.01'))
            else:
                t.base_amount_display = None

        for c in contributions:
            c.transaction_type = 'SAVINGS'
            c.display_currency = account.currency
            if account.currency != base_currency:
                rate = get_exchange_rate(account.currency, base_currency)
                c.base_amount_display = (c.amount * rate).quantize(Decimal('0.01'))
            else:
                c.base_amount_display = None
            c.description = _("Savings: %(goal)s") % {'goal': c.goal.name}

        ledger = sorted(
            chain(expenses, incomes, transfers_from, transfers_to, contributions),
            key=lambda x: x.date,
            reverse=True
        )

        context = {
            'account': account,
            'ledger': ledger,
            'currency_symbol': base_currency,
            'search_query': query,
            'filtered_net_total': filtered_net_total,
        }
        return render(request, self.template_name, context)
