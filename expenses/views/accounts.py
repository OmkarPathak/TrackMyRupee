from decimal import Decimal
from itertools import chain

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, ListView, UpdateView, View

from ..forms import AccountForm, TransferForm
from ..models import Account, Expense, Income, Transfer
from ..utils import get_exchange_rate
from .mixins import RecurringTransactionMixin


class AccountListView(LoginRequiredMixin, ListView):
    model = Account
    template_name = 'expenses/account_list.html'
    context_object_name = 'accounts'

    def get_queryset(self):
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
        account = get_object_or_404(Account, pk=pk, user=request.user)
        
        # Get all expenses, incomes, and transfers for this account
        expenses = Expense.objects.filter(user=request.user, account=account).order_by('-date')
        incomes = Income.objects.filter(user=request.user, account=account).order_by('-date')
        
        # Transfers where this account is either FROM or TO
        transfers_from = Transfer.objects.filter(user=request.user, from_account=account)
        transfers_to = Transfer.objects.filter(user=request.user, to_account=account)
        
        base_currency = request.user.profile.currency if hasattr(request.user, 'profile') else '₹'

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

        ledger = sorted(
            chain(expenses, incomes, transfers_from, transfers_to),
            key=lambda x: x.date,
            reverse=True
        )

        context = {
            'account': account,
            'ledger': ledger,
            'currency_symbol': base_currency,
        }
        return render(request, self.template_name, context)
