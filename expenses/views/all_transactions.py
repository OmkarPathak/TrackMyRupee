import calendar
from datetime import datetime
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import (
    BigIntegerField,
    Case,
    CharField,
    DecimalField,
    F,
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


class AllTransactionsListView(LoginRequiredMixin, ListView):
    template_name = 'expenses/all_transactions.html'
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
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_types = self.request.GET.getlist('type')

        # Filter querysets individually before union if possible, or filter the union
        # Filtering individual querysets is more efficient
        if search_query:
            expenses = expenses.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))
            incomes = incomes.filter(Q(description__icontains=search_query) | Q(source__icontains=search_query) | Q(source_type__icontains=search_query))
            transfers = transfers.filter(description__icontains=search_query)
            loan_repayments = loan_repayments.filter(loan__name__icontains=search_query)
            capital_events = capital_events.filter(Q(note__icontains=search_query) | Q(subtype__icontains=search_query))

        if start_date:
            expenses = expenses.filter(date__gte=start_date)
            incomes = incomes.filter(date__gte=start_date)
            transfers = transfers.filter(date__gte=start_date)
            loan_repayments = loan_repayments.filter(date__gte=start_date)
            capital_events = capital_events.filter(date__gte=start_date)
        if end_date:
            expenses = expenses.filter(date__lte=end_date)
            incomes = incomes.filter(date__lte=end_date)
            transfers = transfers.filter(date__lte=end_date)
            loan_repayments = loan_repayments.filter(date__lte=end_date)
            capital_events = capital_events.filter(date__lte=end_date)

        if not (start_date or end_date):
            if not (selected_years or selected_months or search_query):
                selected_years = [str(datetime.now().year)]
                selected_months = [str(datetime.now().month)]
            
            if selected_years:
                expenses = expenses.filter(date__year__in=selected_years)
                incomes = incomes.filter(date__year__in=selected_years)
                transfers = transfers.filter(date__year__in=selected_years)
                loan_repayments = loan_repayments.filter(date__year__in=selected_years)
                capital_events = capital_events.filter(date__year__in=selected_years)
            if selected_months:
                expenses = expenses.filter(date__month__in=selected_months)
                incomes = incomes.filter(date__month__in=selected_months)
                transfers = transfers.filter(date__month__in=selected_months)
                loan_repayments = loan_repayments.filter(date__month__in=selected_months)
                capital_events = capital_events.filter(date__month__in=selected_months)

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
                'date', 'tx_description', 'type', 'cat', 'acc', 
                'unified_amount', 'loan_pk', 'source_account_id', 'target_account_id', 
                pk=F('uuid_str')
            ).order_by() 
            for qs in active_qs
        ]
        
        queryset = normalized_qs[0].union(*normalized_qs[1:])

        # Apply sorting
        sort_by = self.request.GET.get('sort', '')
        if sort_by == 'amount_asc':
            queryset = queryset.order_by('unified_amount')
        elif sort_by == 'amount_desc':
            queryset = queryset.order_by('-unified_amount')
        else:
            queryset = queryset.order_by('-date')

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # We need the filtered querysets to calculate individual counts
        # (This is slightly redundant with get_queryset but ensures accuracy)
        search_query = self.request.GET.get('search')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')
        selected_years = self.request.GET.getlist('year')
        selected_months = self.request.GET.getlist('month')
        selected_types = self.request.GET.getlist('type')

        expenses = Expense.objects.filter(user=user)
        incomes = Income.objects.filter(user=user)
        transfers = Transfer.objects.filter(user=user)
        loan_repayments = LoanRepayment.objects.filter(loan__user=user)
        capital_events = CapitalEvent.objects.filter(user=user)

        if search_query:
            expenses = expenses.filter(Q(description__icontains=search_query) | Q(category__icontains=search_query))
            incomes = incomes.filter(Q(description__icontains=search_query) | Q(source__icontains=search_query))
            transfers = transfers.filter(description__icontains=search_query)
            loan_repayments = loan_repayments.filter(loan__name__icontains=search_query)
            capital_events = capital_events.filter(Q(note__icontains=search_query) | Q(subtype__icontains=search_query))

        if start_date:
            expenses = expenses.filter(date__gte=start_date)
            incomes = incomes.filter(date__gte=start_date)
            transfers = transfers.filter(date__gte=start_date)
            loan_repayments = loan_repayments.filter(date__gte=start_date)
            capital_events = capital_events.filter(date__gte=start_date)
        if end_date:
            expenses = expenses.filter(date__lte=end_date)
            incomes = incomes.filter(date__lte=end_date)
            transfers = transfers.filter(date__lte=end_date)
            loan_repayments = loan_repayments.filter(date__lte=end_date)
            capital_events = capital_events.filter(date__lte=end_date)

        if not (start_date or end_date):
            if not (selected_years or selected_months or search_query):
                selected_years = [str(datetime.now().year)]
                selected_months = [str(datetime.now().month)]
            
            if selected_years:
                expenses = expenses.filter(date__year__in=selected_years)
                incomes = incomes.filter(date__year__in=selected_years)
                transfers = transfers.filter(date__year__in=selected_years)
                loan_repayments = loan_repayments.filter(date__year__in=selected_years)
                capital_events = capital_events.filter(date__year__in=selected_years)
            if selected_months:
                expenses = expenses.filter(date__month__in=selected_months)
                incomes = incomes.filter(date__month__in=selected_months)
                transfers = transfers.filter(date__month__in=selected_months)
                loan_repayments = loan_repayments.filter(date__month__in=selected_months)
                capital_events = capital_events.filter(date__month__in=selected_months)

        context['expense_count'] = expenses.count()
        context['income_count'] = incomes.count()
        context['transfer_count'] = transfers.count()
        context['loan_count'] = loan_repayments.count()
        context['capital_event_count'] = capital_events.count()
        context['filtered_count'] = context['expense_count'] + context['income_count'] + context['transfer_count'] + context['loan_count'] + context['capital_event_count']

        context['expense_amount'] = expenses.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        context['income_amount'] = incomes.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        context['transfer_amount'] = transfers.aggregate(Sum('converted_amount'))['converted_amount__sum'] or 0
        context['loan_amount'] = loan_repayments.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        context['capital_event_amount'] = capital_events.aggregate(Sum('base_amount'))['base_amount__sum'] or 0

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

        # Total amount (Base Currency)
        context['filtered_amount'] = (
            (expenses.aggregate(Sum('base_amount'))['base_amount__sum'] or 0) +
            (incomes.aggregate(Sum('base_amount'))['base_amount__sum'] or 0) +
            (transfers.aggregate(Sum('converted_amount'))['converted_amount__sum'] or 0) +
            (loan_repayments.aggregate(Sum('base_amount'))['base_amount__sum'] or 0) +
            (capital_events.aggregate(Sum('base_amount'))['base_amount__sum'] or 0)
        )

        # Filter options
        expense_years = {d.year for d in Expense.objects.filter(user=user).dates('date', 'year', order='DESC')}
        income_years = {d.year for d in Income.objects.filter(user=user).dates('date', 'year', order='DESC')}
        transfer_years = {d.year for d in Transfer.objects.filter(user=user).dates('date', 'year', order='DESC')}
        loan_years = {d.year for d in LoanRepayment.objects.filter(loan__user=user).dates('date', 'year', order='DESC')}
        capital_event_years = {d.year for d in CapitalEvent.objects.filter(user=user).dates('date', 'year', order='DESC')}
        all_years = expense_years.union(income_years).union(transfer_years).union(loan_years).union(capital_event_years)
        context['years'] = sorted(list(all_years.union({datetime.now().year})), reverse=True)
        context['months_list'] = [(i, calendar.month_name[i]) for i in range(1, 13)]
        
        # Selected values
        context['selected_years'] = selected_years
        context['selected_months'] = selected_months
        context['selected_types'] = selected_types
        context['search_query'] = search_query or ''
        context['start_date'] = start_date or ''
        context['end_date'] = end_date or ''
        context['current_sort'] = self.request.GET.get('sort', '')

        # Month Navigation Logic
        display_year = None
        display_month = None
        
        if len(selected_years) == 1:
            display_year = selected_years[0]
            
        if len(selected_months) == 1:
            try:
                m_idx = int(selected_months[0])
                display_month = calendar.month_name[m_idx]
            except (ValueError, IndexError):
                pass
                
        context['display_year'] = display_year
        context['display_month'] = display_month

        if len(selected_years) == 1 and len(selected_months) == 1:
            try:
                curr_year = int(selected_years[0])
                curr_month = int(selected_months[0])
                
                pm = 12 if curr_month == 1 else curr_month - 1
                py = curr_year - 1 if curr_month == 1 else curr_year
                
                nm = 1 if curr_month == 12 else curr_month + 1
                ny = curr_year + 1 if curr_month == 12 else curr_year

                from django.urls import reverse
                base_url = reverse('all-transactions')
                
                # Keep other filters (types, search)
                query_params = []
                for t in selected_types:
                    query_params.append(f'type={t}')
                if search_query:
                    query_params.append(f'search={search_query}')
                
                sort_by = self.request.GET.get('sort')
                if sort_by:
                    query_params.append(f'sort={sort_by}')
                
                qp_prev = query_params + [f'year={py}', f'month={pm}']
                qp_next = query_params + [f'year={ny}', f'month={nm}']
                
                context['prev_month_url'] = f"{base_url}?{'&'.join(qp_prev)}"
                context['next_month_url'] = f"{base_url}?{'&'.join(qp_next)}"
            except ValueError:
                pass

        return context
