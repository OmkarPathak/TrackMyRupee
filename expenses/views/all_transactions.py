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
        
        # We need the filtered querysets to calculate individual counts
        # (This is slightly redundant with get_queryset but ensures accuracy)
        search_query = self.request.GET.get('search')
        selected_types = [t for t in self.request.GET.getlist('type') if t]
        time_period = self.request.GET.get('time_period', 'this_month')
        start_date = self.request.GET.get('start_date')
        end_date = self.request.GET.get('end_date')

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
            capital_events = capital_events.filter(Q(note__icontains=search_query) | Q(subtype__icontains=search_query))

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

        net_remaining = context['income_amount'] - context['expense_amount'] - context['capital_event_amount']
        context['net_remaining'] = net_remaining
        context['net_saved'] = net_remaining
        if context['income_amount'] > 0:
            context['savings_rate'] = round((net_remaining / context['income_amount']) * 100, 1)
        else:
            context['savings_rate'] = 0

        # Daily sparkline trend calculation
        from datetime import timedelta
        # Use DB aggregation instead of loading all dates into Python memory
        expense_range = expenses.aggregate(min=Min('date'), max=Max('date'))
        income_range  = incomes.aggregate(min=Min('date'),  max=Max('date'))
        candidate_dates = [d for d in [
            expense_range['min'], expense_range['max'],
            income_range['min'],  income_range['max'],
        ] if d]
        if candidate_dates:
            min_date = min(candidate_dates)
            max_date = max(candidate_dates)
        else:
            today = datetime.now()
            min_date = today.replace(day=1).date()
            last_day = calendar.monthrange(today.year, today.month)[1]
            max_date = today.replace(day=last_day).date()
            
        delta = max_date - min_date
        if delta.days >= 0 and delta.days <= 90:
            date_list = [min_date + timedelta(days=i) for i in range(delta.days + 1)]
            daily_expenses = {d: 0.0 for d in date_list}
            daily_incomes = {d: 0.0 for d in date_list}
            
            for item in expenses.values('date').annotate(total=Sum('base_amount')):
                d = item['date']
                if d in daily_expenses:
                    daily_expenses[d] = float(item['total'] or 0)
                    
            for item in incomes.values('date').annotate(total=Sum('base_amount')):
                d = item['date']
                if d in daily_incomes:
                    daily_incomes[d] = float(item['total'] or 0)
                    
            expense_trend = [daily_expenses[d] for d in date_list]
            income_trend = [daily_incomes[d] for d in date_list]
        else:
            # Fallback for large date ranges: take last 30 active dates
            expense_daily_totals = sorted([(item['date'], float(item['total'] or 0)) for item in expenses.values('date').annotate(total=Sum('base_amount'))], key=lambda x: x[0])
            income_daily_totals = sorted([(item['date'], float(item['total'] or 0)) for item in incomes.values('date').annotate(total=Sum('base_amount'))], key=lambda x: x[0])
            expense_trend = [val for _, val in expense_daily_totals[-30:]]
            income_trend = [val for _, val in income_daily_totals[-30:]]

        def generate_sparkline_paths(trend_data, width=100, height=30):
            if not trend_data or len(trend_data) == 0:
                return "", ""
            n = len(trend_data)
            if n == 1:
                return f"M 0 {height/2} L {width} {height/2}", f"M 0 {height/2} L {width} {height/2} L {width} {height} L 0 {height} Z"
            min_val = min(trend_data)
            max_val = max(trend_data)
            val_range = max_val - min_val
            if val_range == 0:
                val_range = 1
            points = []
            for idx, val in enumerate(trend_data):
                x = (idx / (n - 1)) * width
                y = height - 2 - ((val - min_val) / val_range) * (height - 4)
                points.append(f"{x:.1f},{y:.1f}")
            line_path = "M " + " L ".join(points)
            fill_path = f"{line_path} L {width:.1f},{height:.1f} L 0.0,{height:.1f} Z"
            return line_path, fill_path

        inc_line, inc_fill = generate_sparkline_paths(income_trend)
        exp_line, exp_fill = generate_sparkline_paths(expense_trend)
        context['income_sparkline_path'] = inc_line
        context['income_sparkline_fill'] = inc_fill
        context['expense_sparkline_path'] = exp_line
        context['expense_sparkline_fill'] = exp_fill

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
        if sort_by and sort_by != 'date_desc':
            active_filters += 1
        context['active_filters_count'] = active_filters


        # Prev/Next month logic removed since we are moving to relative pills

        return context
