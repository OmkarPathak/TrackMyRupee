import calendar
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import (
    BigIntegerField,
    Case,
    CharField,
    DecimalField,
    F,
    Max,
    Min,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.functions import Cast, Concat
from django.views.generic import ListView

from ..ledger_read_service import LedgerReadService
from ..models import Account, CapitalEvent, Expense, Income, LoanRepayment, Transfer
from ..utils import get_exchange_rate
from ..filters.definitions import ALL_TRANSACTIONS_FILTERS
from .mixins import HtmxPartialTemplateMixin
from .utils import apply_date_filters


class AllTransactionsListView(HtmxPartialTemplateMixin, LoginRequiredMixin, ListView):
    template_name = 'expenses/all_transactions.html'
    htmx_template_name = 'expenses/partials/_transaction_list.html'
    context_object_name = 'transactions'
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        
        # 1. Normalize Expenses
        expenses = Expense.objects.filter(user=user).annotate(
            uuid_str=Cast(F('uuid'), output_field=CharField()),
            type=Cast(Value('EXPENSE'), output_field=CharField()),
            cat=Cast(F('category'), output_field=CharField()),
            acc=Cast(F('account__name'), output_field=CharField()),
            unified_amount=Cast(F('base_amount'), output_field=DecimalField(max_digits=15, decimal_places=2)),
            tx_description=Cast(F('description'), output_field=CharField()),
            loan_pk=Cast(Value(None), output_field=CharField()),
            source_account_id=Cast(F('account_id'), output_field=BigIntegerField()),
            target_account_id=Cast(Value(None), output_field=BigIntegerField()),
        )

        # 2. Normalize Incomes
        incomes = Income.objects.filter(user=user).annotate(
            uuid_str=Cast(F('uuid'), output_field=CharField()),
            type=Cast(Value('INCOME'), output_field=CharField()),
            cat=Cast(F('source_type'), output_field=CharField()),
            acc=Cast(F('account__name'), output_field=CharField()),
            unified_amount=Cast(F('base_amount'), output_field=DecimalField(max_digits=15, decimal_places=2)),
            tx_description=Cast(F('description'), output_field=CharField()),
            loan_pk=Cast(Value(None), output_field=CharField()),
            source_account_id=Cast(F('account_id'), output_field=BigIntegerField()),
            target_account_id=Cast(Value(None), output_field=BigIntegerField()),
        )

        # 3. Normalize Transfers
        transfers = Transfer.objects.filter(user=user).annotate(
            uuid_str=Cast(F('uuid'), output_field=CharField()),
            type=Cast(Value('TRANSFER'), output_field=CharField()),
            cat=Cast(Value('Transfer'), output_field=CharField()),
            acc=Cast(Concat(F('from_account__name'), Value(' → '), F('to_account__name'), output_field=CharField()), output_field=CharField()),
            unified_amount=Cast(F('converted_amount'), output_field=DecimalField(max_digits=15, decimal_places=2)),
            tx_description=Cast(F('description'), output_field=CharField()),
            loan_pk=Cast(Value(None), output_field=CharField()),
            source_account_id=Cast(F('from_account_id'), output_field=BigIntegerField()),
            target_account_id=Cast(F('to_account_id'), output_field=BigIntegerField()),
        )

        # 4. Normalize Loan Repayments
        loan_repayments = LoanRepayment.objects.filter(loan__user=user).annotate(
            uuid_str=Cast(F('uuid'), output_field=CharField()),
            type=Cast(Value('LOAN'), output_field=CharField()),
            cat=Cast(F('loan__name'), output_field=CharField()),
            acc=Cast(F('from_account__name'), output_field=CharField()),
            unified_amount=Cast(F('base_amount'), output_field=DecimalField(max_digits=15, decimal_places=2)),
            tx_description=Cast(Concat(Value('Loan repayment - '), F('loan__name'), output_field=CharField()), output_field=CharField()),
            loan_pk=Cast(F('loan__uuid'), output_field=CharField()),
            source_account_id=Cast(F('from_account_id'), output_field=BigIntegerField()),
            target_account_id=Cast(Value(None), output_field=BigIntegerField()),
        )

        # 5. Normalize Capital Events
        capital_events = CapitalEvent.objects.filter(user=user).annotate(
            uuid_str=Cast(F('uuid'), output_field=CharField()),
            type=Cast(Value('CAPITAL_EVENT'), output_field=CharField()),
            cat=Cast(
                Case(
                    *[When(subtype=k, then=Value(str(v))) for k, v in CapitalEvent.SUBTYPE_CHOICES],
                    default=Value('Other'),
                    output_field=CharField()
                ),
                output_field=CharField()
            ),
            acc=Cast(F('account__name'), output_field=CharField()),
            unified_amount=Cast(F('base_amount'), output_field=DecimalField(max_digits=15, decimal_places=2)),
            tx_description=Cast(F('note'), output_field=CharField()),
            loan_pk=Cast(F('linked_loan__uuid'), output_field=CharField()),
            source_account_id=Cast(F('account_id'), output_field=BigIntegerField()),
            target_account_id=Cast(Value(None), output_field=BigIntegerField()),
        )

        # Handle filtering
        search_query = self.request.GET.get('search')
        selected_types = [t for t in self.request.GET.getlist('type') if t]
        selected_accounts = [a for a in self.request.GET.getlist('account') if a]
        if not selected_accounts and self.request.GET.get('account'):
            selected_accounts = [self.request.GET.get('account')]
        selected_amounts = [a for a in self.request.GET.getlist('amount_range') if a]
        if not selected_amounts and self.request.GET.get('amount_range'):
            selected_amounts = [self.request.GET.get('amount_range')]

        # Apply time period filters
        expenses = apply_date_filters(expenses, self.request)
        incomes = apply_date_filters(incomes, self.request)
        transfers = apply_date_filters(transfers, self.request)
        loan_repayments = apply_date_filters(loan_repayments, self.request)
        capital_events = apply_date_filters(capital_events, self.request)

        # Filter querysets individually before union if possible, or filter the union
        # Filtering individual querysets is more efficient
        if search_query:
            expenses = expenses.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))
            incomes = incomes.filter(Q(description__icontains=search_query) | Q(source__icontains=search_query) | Q(source_type__icontains=search_query))
            transfers = transfers.filter(description__icontains=search_query)
            loan_repayments = loan_repayments.filter(loan__name__icontains=search_query)
            capital_events = capital_events.filter(Q(note__icontains=search_query) | Q(subtype__icontains=search_query))

        if selected_accounts:
            expenses = expenses.filter(account_id__in=selected_accounts)
            incomes = incomes.filter(account_id__in=selected_accounts)
            transfers = transfers.filter(Q(from_account_id__in=selected_accounts) | Q(to_account_id__in=selected_accounts))
            loan_repayments = loan_repayments.filter(from_account_id__in=selected_accounts)
            capital_events = capital_events.filter(account_id__in=selected_accounts)

        if selected_amounts:
            from ..filters.definitions import filter_amount_range
            expenses = filter_amount_range(expenses, selected_amounts)
            incomes = filter_amount_range(incomes, selected_amounts)
            transfers = filter_amount_range(transfers, selected_amounts)
            loan_repayments = filter_amount_range(loan_repayments, selected_amounts)
            capital_events = filter_amount_range(capital_events, selected_amounts)

        # Filter by Transaction Type
        active_qs = []
        if not selected_types:
            active_qs = [expenses, incomes, transfers, loan_repayments, capital_events]
        else:
            if 'EXPENSE' in selected_types: active_qs.append(expenses)
            if 'INCOME' in selected_types: active_qs.append(incomes)
            if 'TRANSFER' in selected_types: active_qs.append(transfers)
            if 'LOAN' in selected_types: active_qs.append(loan_repayments)
            if 'CAPITAL_EVENT' in selected_types: active_qs.append(capital_events)

        if not active_qs:
            return Expense.objects.none()

        # Combine using Union
        # Django union() requires all querysets to have exactly the same fields in the same order.
        # SQLite disallows ORDER BY inside UNION subqueries, so clear ordering first.
        normalized_qs = [
            qs.values(
                'date', 'created_at', 'tx_description', 'type', 'cat', 'acc', 
                'unified_amount', 'loan_pk', 'source_account_id', 'target_account_id', 
                pk=F('uuid_str')
            ).order_by() 
            for qs in active_qs
        ]
        
        queryset = normalized_qs[0].union(*normalized_qs[1:])

        # Apply sorting
        sort_by = self.request.GET.get('sort', 'date_desc')
        if sort_by == 'date_asc':
            queryset = queryset.order_by('date', 'created_at')
        elif sort_by == 'amount_desc':
            queryset = queryset.order_by('-unified_amount')
        elif sort_by == 'amount_asc':
            queryset = queryset.order_by('unified_amount')
        else:
            queryset = queryset.order_by('-date', '-created_at')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        context['filter_config'] = ALL_TRANSACTIONS_FILTERS
        search_query = self.request.GET.get('search') or ''
        selected_types = [t for t in self.request.GET.getlist('type') if t]
        selected_accounts = [a for a in self.request.GET.getlist('account') if a]
        if not selected_accounts and self.request.GET.get('account'):
            selected_accounts = [self.request.GET.get('account')]
        selected_amounts = [a for a in self.request.GET.getlist('amount_range') if a]
        if not selected_amounts and self.request.GET.get('amount_range'):
            selected_amounts = [self.request.GET.get('amount_range')]

        time_period = self.request.GET.get('time_period', 'this_month')
        start_date = self.request.GET.get('start_date') or ''
        end_date = self.request.GET.get('end_date') or ''

        applied_filters = {}
        if selected_types:
            applied_filters['type'] = selected_types
        if selected_accounts:
            applied_filters['account'] = selected_accounts
        if selected_amounts:
            applied_filters['amount_range'] = selected_amounts

        context['applied_state'] = {
            'search': search_query,
            'time_period': time_period,
            'start_date': start_date,
            'end_date': end_date,
            'sort': self.request.GET.get('sort', 'date_desc'),
            'filters': applied_filters,
        }


        expenses = Expense.objects.filter(user=user)
        incomes = Income.objects.filter(user=user)
        transfers = Transfer.objects.filter(user=user)
        loan_repayments = LoanRepayment.objects.filter(loan__user=user)
        capital_events = CapitalEvent.objects.filter(user=user)

        expenses = apply_date_filters(expenses, self.request)
        incomes = apply_date_filters(incomes, self.request)
        transfers = apply_date_filters(transfers, self.request)
        loan_repayments = apply_date_filters(loan_repayments, self.request)
        capital_events = apply_date_filters(capital_events, self.request)

        if search_query:
            expenses = expenses.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))
            incomes = incomes.filter(Q(description__icontains=search_query) | Q(source__icontains=search_query))
            transfers = transfers.filter(description__icontains=search_query)
            loan_repayments = loan_repayments.filter(loan__name__icontains=search_query)

        if selected_accounts:
            expenses = expenses.filter(account_id__in=selected_accounts)
            incomes = incomes.filter(account_id__in=selected_accounts)
            transfers = transfers.filter(Q(from_account_id__in=selected_accounts) | Q(to_account_id__in=selected_accounts))
            loan_repayments = loan_repayments.filter(from_account_id__in=selected_accounts)
            capital_events = capital_events.filter(account_id__in=selected_accounts)

        if selected_amounts:
            from ..filters.definitions import filter_amount_range
            expenses = filter_amount_range(expenses, selected_amounts)
            incomes = filter_amount_range(incomes, selected_amounts)
            transfers = filter_amount_range(transfers, selected_amounts)
            loan_repayments = filter_amount_range(loan_repayments, selected_amounts)
            capital_events = filter_amount_range(capital_events, selected_amounts)
        from django.db.models import Count

        exp_stats = expenses.aggregate(cnt=Count('uuid'), total=Sum('base_amount'))
        inc_stats = incomes.aggregate(cnt=Count('uuid'), total=Sum('base_amount'))
        trf_stats = transfers.aggregate(cnt=Count('uuid'), total=Sum('converted_amount'))
        loan_stats = loan_repayments.aggregate(cnt=Count('uuid'), total=Sum('base_amount'))
        cap_stats = capital_events.aggregate(cnt=Count('uuid'), total=Sum('base_amount'))

        context['expense_count'] = exp_stats['cnt'] or 0
        context['income_count'] = inc_stats['cnt'] or 0
        context['transfer_count'] = trf_stats['cnt'] or 0
        context['loan_count'] = loan_stats['cnt'] or 0
        context['capital_event_count'] = cap_stats['cnt'] or 0
        context['filtered_count'] = context['expense_count'] + context['income_count'] + context['transfer_count'] + context['loan_count'] + context['capital_event_count']

        context['expense_amount'] = exp_stats['total'] or 0
        context['income_amount'] = inc_stats['total'] or 0
        context['transfer_amount'] = trf_stats['total'] or 0
        context['loan_amount'] = loan_stats['total'] or 0
        context['capital_event_amount'] = cap_stats['total'] or 0

        net_remaining = context['income_amount'] - context['expense_amount'] - context['capital_event_amount']
        context['net_remaining'] = net_remaining
        context['net_saved'] = net_remaining
        if context['income_amount'] > 0:
            context['savings_rate'] = round((net_remaining / context['income_amount']) * 100, 1)
        else:
            context['savings_rate'] = 0

        # Convert transactions to list and calculate CC running balance
        tx_list = list(context.get('transactions', []))
        
        # 1. Identify CREDIT_CARD accounts of this user
        cc_accounts = Account.objects.filter(user=user, account_type='CREDIT_CARD')
        cc_account_ids = set(cc_accounts.values_list('id', flat=True))
        cc_accounts_dict = {acc.id: acc for acc in cc_accounts}
        
        # 2. Check if there are any transfers to credit card accounts in the page's transactions
        target_cc_ids = set()
        for tx in tx_list:
            if tx.get('type') == 'TRANSFER' and tx.get('target_account_id') in cc_account_ids:
                target_cc_ids.add(tx.get('target_account_id'))
                
        # 3. Calculate running balances for those credit card accounts in bulk
        if target_cc_ids:
            cc_balances_map = {}
            target_cc_accounts = cc_accounts.filter(id__in=target_cc_ids)
            current_balances = LedgerReadService.get_account_balances(target_cc_accounts)
            
            # Fetch all transactions for target CCs in bulk
            acc_expenses = Expense.objects.filter(user=user, account_id__in=target_cc_ids).values('uuid', 'date', 'created_at', 'amount', 'currency', 'account_id')
            acc_incomes = Income.objects.filter(user=user, account_id__in=target_cc_ids).values('uuid', 'date', 'created_at', 'amount', 'currency', 'account_id')
            acc_transfers_out = Transfer.objects.filter(user=user, from_account_id__in=target_cc_ids).values('uuid', 'date', 'created_at', 'amount', 'from_account_id')
            acc_transfers_in = Transfer.objects.filter(user=user, to_account_id__in=target_cc_ids).select_related('from_account')
            acc_loan_repayments = LoanRepayment.objects.filter(loan__user=user, from_account_id__in=target_cc_ids).select_related('loan')
            acc_capital_events = CapitalEvent.objects.filter(user=user, account_id__in=target_cc_ids, include_in_net_worth=True).values('uuid', 'date', 'created_at', 'amount', 'currency', 'account_id')
            
            # Group transactions by CC account in memory
            tx_by_cc = {cc_id: [] for cc_id in target_cc_ids}
            
            for e in acc_expenses:
                cc_id = e['account_id']
                cc_account = cc_accounts_dict[cc_id]
                amt = e['amount']
                if e['currency'] != cc_account.currency:
                    rate = get_exchange_rate(e['currency'], cc_account.currency)
                    amt = (amt * rate).quantize(Decimal('0.01'))
                tx_by_cc[cc_id].append({
                    'pk': str(e['uuid']).replace('-', ''),
                    'type': 'EXPENSE',
                    'date': e['date'],
                    'created_at': e['created_at'],
                    'net_change': -amt
                })
                
            for i in acc_incomes:
                cc_id = i['account_id']
                cc_account = cc_accounts_dict[cc_id]
                amt = i['amount']
                if i['currency'] != cc_account.currency:
                    rate = get_exchange_rate(i['currency'], cc_account.currency)
                    amt = (amt * rate).quantize(Decimal('0.01'))
                tx_by_cc[cc_id].append({
                    'pk': str(i['uuid']).replace('-', ''),
                    'type': 'INCOME',
                    'date': i['date'],
                    'created_at': i['created_at'],
                    'net_change': amt
                })
                
            for t in acc_transfers_out:
                cc_id = t['from_account_id']
                tx_by_cc[cc_id].append({
                    'pk': str(t['uuid']).replace('-', ''),
                    'type': 'TRANSFER_OUT',
                    'date': t['date'],
                    'created_at': t['created_at'],
                    'net_change': -t['amount']
                })
                
            for t in acc_transfers_in:
                cc_id = t.to_account_id
                cc_account = cc_accounts_dict[cc_id]
                amt = t.amount
                if t.from_account.currency != cc_account.currency:
                    rate = get_exchange_rate(t.from_account.currency, cc_account.currency)
                    amt = (amt * rate).quantize(Decimal('0.01'))
                tx_by_cc[cc_id].append({
                    'pk': str(t.uuid).replace('-', ''),
                    'type': 'TRANSFER',
                    'date': t.date,
                    'created_at': t.created_at,
                    'net_change': amt
                })
                
            for lr in acc_loan_repayments:
                cc_id = lr.from_account_id
                cc_account = cc_accounts_dict[cc_id]
                amt = lr.amount
                if lr.loan.currency != cc_account.currency:
                    rate = get_exchange_rate(lr.loan.currency, cc_account.currency)
                    amt = (amt * rate).quantize(Decimal('0.01'))
                tx_by_cc[cc_id].append({
                    'pk': str(lr.uuid).replace('-', ''),
                    'type': 'LOAN',
                    'date': lr.date,
                    'created_at': lr.created_at,
                    'net_change': -amt
                })
                
            for ce in acc_capital_events:
                cc_id = ce['account_id']
                cc_account = cc_accounts_dict[cc_id]
                amt = ce['amount']
                if ce['currency'] != cc_account.currency:
                    rate = get_exchange_rate(ce['currency'], cc_account.currency)
                    amt = (amt * rate).quantize(Decimal('0.01'))
                tx_by_cc[cc_id].append({
                    'pk': str(ce['uuid']).replace('-', ''),
                    'type': 'CAPITAL_EVENT',
                    'date': ce['date'],
                    'created_at': ce['created_at'],
                    'net_change': -amt
                })
                
            # Calculate running balances chronologically backward for each account
            for cc_id in target_cc_ids:
                current_balance = current_balances.get(cc_id, Decimal('0.00'))
                all_cc_tx = tx_by_cc[cc_id]
                
                # Sort chronologically descending
                all_cc_tx.sort(key=lambda x: (x['date'], x['created_at'] or x['date'], x['pk']), reverse=True)
                
                running = current_balance
                for tx_item in all_cc_tx:
                    cc_balances_map[(cc_id, tx_item['type'], tx_item['pk'])] = running
                    running -= tx_item['net_change']
            
            # Enrich tx_list with running balances
            for tx in tx_list:
                if tx.get('type') == 'TRANSFER' and tx.get('target_account_id') in target_cc_ids:
                    cc_id = tx.get('target_account_id')
                    cc_account = cc_accounts_dict.get(cc_id)
                    if cc_account:
                        balance_key = (cc_id, 'TRANSFER', str(tx.get('pk')).replace('-', ''))
                        if balance_key in cc_balances_map:
                            tx['cc_balance_after_payment'] = cc_balances_map[balance_key]
                            tx['to_account_name'] = cc_account.name
                            tx['to_account_currency'] = cc_account.currency
                            
        context['transactions'] = tx_list

        # Total amount (Base Currency) - reuse pre-calculated section sums to save 5 DB queries
        context['filtered_amount'] = (
            context['expense_amount'] +
            context['income_amount'] +
            context['transfer_amount'] +
            context['loan_amount'] +
            context['capital_event_amount']
        )

        # Filter options
        cache_key = f'all_tx_years_{user.id}'
        cached_years = cache.get(cache_key)
        if cached_years is None:
            expense_years = {d.year for d in Expense.objects.filter(user=user).dates('date', 'year', order='DESC')}
            income_years = {d.year for d in Income.objects.filter(user=user).dates('date', 'year', order='DESC')}
            transfer_years = {d.year for d in Transfer.objects.filter(user=user).dates('date', 'year', order='DESC')}
            loan_years = {d.year for d in LoanRepayment.objects.filter(loan__user=user).dates('date', 'year', order='DESC')}
            capital_event_years = {d.year for d in CapitalEvent.objects.filter(user=user).dates('date', 'year', order='DESC')}
            all_years = expense_years.union(income_years).union(transfer_years).union(loan_years).union(capital_event_years)
            cached_years = sorted(list(all_years.union({datetime.now().year})), reverse=True)
            cache.set(cache_key, cached_years, 600)
        context['years'] = cached_years
        context['months_list'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        
        # Selected values
        context['selected_types'] = selected_types
        context['search_query'] = search_query or ''
        context['time_period'] = time_period
        context['start_date'] = start_date or ''
        context['end_date'] = end_date or ''
        sort_by = self.request.GET.get('sort', 'date_desc')
        context['sort_by'] = sort_by
        context['current_sort'] = sort_by

        # Calculate active filters count
        active_filters = 0
        if search_query:
            active_filters += 1
        if time_period != 'this_month':
            active_filters += 1
        if selected_types:
            active_filters += 1
        if selected_accounts:
            active_filters += 1
        if selected_amounts:
            active_filters += 1
        if sort_by and sort_by != 'date_desc':
            active_filters += 1
        context['active_filters_count'] = active_filters


        # Prev/Next month logic removed since we are moving to relative pills

        return context
