import calendar
import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from ..models import Account, Expense, Income, Transfer
from ..utils import get_exchange_rate


@login_required
def mom_analysis_view(request):
    """
    View for Month-on-Month analysis of Net Worth, Expenses, and Savings.
    """
    user = request.user
    currency_symbol = user.profile.currency if hasattr(user, 'profile') else '₹'
    
    # Get history limit
    history_limit = user.profile.net_worth_history_limit
    is_limited = (history_limit != -1)
    
    # If -1, we show up to 12 months as a reasonable visual default for "unlimited"
    num_months = history_limit if is_limited else 12

    # 1. Get last num_months months (including current)
    months_data = []
    curr_date = timezone.now().date()
    
    for i in range(num_months):
        year = curr_date.year
        month = curr_date.month - i
        while month < 1:
            month += 12
            year -= 1
        
        m_start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        m_end = date(year, month, last_day)
        
        label = m_start.strftime('%b %Y')
        months_data.append({
            'label': label,
            'start': m_start,
            'end': m_end,
            'year': year,
            'month': month
        })
    
    months_data.sort(key=lambda x: x['start']) # Oldest to newest

    # 2. Net Worth Calculation (Backwards reconstruction)
    accounts = Account.objects.filter(user=user)
    current_net_worth = Decimal('0.00')
    for acc in accounts:
        if acc.currency == currency_symbol:
            current_net_worth += acc.balance
        else:
            rate = get_exchange_rate(acc.currency, currency_symbol)
            current_net_worth += (acc.balance * rate).quantize(Decimal('0.01'))

    # Helper: get net cashflow for a date range
    def get_net_cashflow(start_date, end_date):
        inc = Income.objects.filter(user=user, date__range=[start_date, end_date]).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        exp = Expense.objects.filter(user=user, date__range=[start_date, end_date]).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0')
        return inc - exp

    # We want Net Worth at the END of each selected month.
    nw_data = []
    running_nw = current_net_worth
    
    # NW at end of current month (today)
    nw_data.append(float(running_nw))
    
    # Current month start
    curr_month_start = date.today().replace(day=1)
    
    if num_months > 1:
        # Subtract current month's cashflow to get NW at end of previous month
        running_nw -= get_net_cashflow(curr_month_start, date.today())
        nw_data.append(float(running_nw))
        
        # Subtract previous months' cashflows
        temp_date = curr_month_start
        for i in range(num_months - 2):
            p_end = temp_date - timedelta(days=1)
            p_start = p_end.replace(day=1)
            
            running_nw -= get_net_cashflow(p_start, p_end)
            nw_data.append(float(running_nw))
            temp_date = p_start
        
    nw_data.reverse() # Oldest to Newest

    # 3. Income, Expense, Savings for each month
    labels = []
    exp_data = []
    sav_data = []
    inc_data = []
    inv_data = []
    burn_data = []
    today = timezone.now().date()
    
    for m in months_data:
        m_inc_agg = Income.objects.filter(user=user, date__range=[m['start'], m['end']]).aggregate(Sum('base_amount'))['base_amount__sum']
        m_exp_agg = Expense.objects.filter(user=user, date__range=[m['start'], m['end']]).aggregate(Sum('base_amount'))['base_amount__sum']
        
        m_inc = m_inc_agg or Decimal('0')
        m_exp = m_exp_agg or Decimal('0')
        
        # Calculate Investments (Transfers to Investment/FD accounts)
        m_inv_agg = Transfer.objects.filter(
            user=user, 
            date__range=[m['start'], m['end']], 
            to_account__account_type__in=['INVESTMENT', 'FIXED_DEPOSIT']
        ).aggregate(Sum('converted_amount'))['converted_amount__sum']
        m_inv = m_inv_agg or Decimal('0')
        
        labels.append(m['label'])
        
        # Determine number of days to divide by for Burn Rate
        if m['year'] == today.year and m['month'] == today.month:
            days = today.day
        else:
            days = calendar.monthrange(m['year'], m['month'])[1]
        
        # If both are None, it's likely missing data
        if m_inc_agg is None and m_exp_agg is None:
            inc_data.append(None)
            exp_data.append(None)
            inv_data.append(None)
            sav_data.append(None)
            burn_data.append(None)
        else:
            inc_data.append(float(m_inc))
            exp_data.append(float(m_exp))
            inv_data.append(float(m_inv))
            sav_data.append(float(m_inc - m_exp))
            burn_data.append(float(m_exp / days) if days > 0 else 0)

    # 4. Summary & Advanced Insights
    curr_expenses = exp_data[-1] if exp_data[-1] is not None else 0
    prev_expenses = exp_data[-2] if len(exp_data) > 1 and exp_data[-2] is not None else 0
    exp_change = ((curr_expenses - prev_expenses) / prev_expenses * 100) if prev_expenses > 0 else 0
    
    curr_savings = sav_data[-1] if sav_data[-1] is not None else 0
    prev_savings = sav_data[-2] if len(sav_data) > 1 and sav_data[-2] is not None else 0
    sav_change = ((curr_savings - prev_savings) / prev_savings * 100) if prev_savings > 0 else 0

    # 3-Month NW Growth
    nw_change_3m = 0
    nw_pct_3m = 0
    if len(nw_data) >= 4:
        nw_change_3m = nw_data[-1] - nw_data[-4]
        if nw_data[-4] > 0:
            nw_pct_3m = (nw_change_3m / nw_data[-4]) * 100
    
    # Savings Rate
    curr_income = inc_data[-1] if inc_data[-1] is not None else 0
    savings_rate = (curr_savings / curr_income * 100) if curr_income > 0 else 0
    
    # Streak Calculation
    savings_streak = 0
    for s in reversed(sav_data):
        if s is not None and s > 0:
            savings_streak += 1
        else:
            break
            
    # Best Savings Month
    is_best_savings = all(curr_savings >= s for s in (x for x in sav_data if x is not None))
    
    # Top Expense Category (Current Month)
    top_category = "N/A"
    if months_data:
        m_latest = months_data[-1]
        top_cat_agg = Expense.objects.filter(user=user, date__range=[m_latest['start'], m_latest['end']])\
            .values('category').annotate(total=Sum('base_amount')).order_by('-total').first()
        if top_cat_agg:
            top_category = top_cat_agg['category']

    context = {
        'labels': json.dumps(labels),
        'nw_data': json.dumps(nw_data),
        'exp_data': json.dumps(exp_data),
        'inv_data': json.dumps(inv_data),
        'sav_data': json.dumps(sav_data),
        'inc_data': json.dumps(inc_data),
        'burn_data': json.dumps(burn_data),
        'currency_symbol': currency_symbol,
        'summary': {
            'total_expenses': curr_expenses,
            'exp_change': round(exp_change, 1),
            'exp_change_abs': abs(round(exp_change, 1)),
            'exp_diff': abs(curr_expenses - prev_expenses),
            'total_savings': curr_savings,
            'sav_change': round(sav_change, 1),
            'sav_change_abs': abs(round(sav_change, 1)),
            'net_worth': float(current_net_worth),
            'nw_change_3m': nw_change_3m,
            'nw_pct_3m': round(nw_pct_3m, 1),
            'savings_rate': round(savings_rate, 1),
            'savings_streak': savings_streak,
            'is_best_savings': is_best_savings,
            'top_category': top_category,
            'burn_rate': burn_data[-1] if burn_data and burn_data[-1] is not None else 0,
            'prev_burn': burn_data[-2] if len(burn_data) > 1 and burn_data[-2] is not None else 0,
        },
        'is_history_limited': is_limited,
        'history_limit': history_limit,
    }
    
    return render(request, 'mom_analysis.html', context)
