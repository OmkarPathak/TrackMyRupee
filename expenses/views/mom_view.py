import calendar
import json
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import render
from django.utils import timezone

from ..account_types import STRATEGY, investment_codes, strategy_for
from ..ledger_read_service import LedgerReadService
from ..models import Account, CapitalEvent, Expense, Income, Transfer
from ..savings import monthly_savings
from ..templatetags.digit_filters import compact_amount


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
    curr_date = timezone.localdate()
    
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

    # --- PERFORMANCE OPTIMIZATION: BATCH MONTHLY TOTALS ---
    from django.db.models.functions import TruncMonth
    history_start = months_data[0]['start']
    
    batch_inv = Transfer.objects.filter(
        user=user, date__gte=history_start,
        to_account__account_type__in=list(investment_codes())
    ).annotate(m=TruncMonth('date')).values('m').annotate(total=Sum('converted_amount'))
    mo_inv_map = {(item['m'].year, item['m'].month): float(item['total']) for item in batch_inv}

    # Savings per month (interest and counted capital events included) from the single definition
    month_savings = monthly_savings(user, history_start, months_data[-1]['end'])

    # 2. Net worth at the END of each month, from the ledger as it stood on that day. (Working backwards
    # from today by subtracting each month's income minus spending was wrong: loan principal and transfers
    # do not change net worth, while interest, price moves and exchange rates do.) The current month ends
    # in the future, so it is simply the net worth now.
    current_net_worth, current_balances = LedgerReadService.get_net_worth(user)
    nw_data = []
    for m in months_data:
        if m['end'] >= timezone.localdate():
            nw_data.append(float(current_net_worth))
        else:
            nw_data.append(float(LedgerReadService.get_net_worth(user, as_of=m['end'])[0]))

    # 3. Income, Expense, Savings for each month
    labels = []
    exp_data = []
    sav_data = []
    inc_data = []
    inv_data = []
    burn_data = []
    today = timezone.localdate()

    for m in months_data:
        m_result = month_savings.get((m['year'], m['month']))
        m_inc = float(m_result.income) if m_result else 0
        m_exp = float(m_result.spending) if m_result else 0
        m_inv = mo_inv_map.get((m['year'], m['month']), 0)

        
        labels.append(m['label'])
        
        # Determine number of days to divide by for Burn Rate
        if m['year'] == today.year and m['month'] == today.month:
            days = today.day
        else:
            days = calendar.monthrange(m['year'], m['month'])[1]
        
        # If 0, it's likely missing data or just 0
        if m_inc == 0 and m_exp == 0:

            inc_data.append(None)
            exp_data.append(None)
            inv_data.append(None)
            sav_data.append(None)
            burn_data.append(None)
        else:
            inc_data.append(float(m_inc))
            exp_data.append(float(m_exp))
            inv_data.append(float(m_inv))
            sav_data.append(float(m_result.savings))
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
    
    # Savings Rate (single definition in expenses/savings.py)
    latest = months_data[-1] if months_data else None
    latest_result = month_savings.get((latest['year'], latest['month'])) if latest else None
    savings_rate = float(latest_result.rate) if latest_result else 0
    
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

    # Check if there's any actual data (not all None)
    has_data = any(x is not None for x in inc_data) or any(x is not None for x in exp_data)


    # Category Creep Detection
    creep_categories = []
    has_enough_creep_data = False
    
    # We need last 3 months of category totals
    if len(months_data) >= 3:
        m1_start, m1_end = months_data[-3]['start'], months_data[-3]['end']
        m2_start, m2_end = months_data[-2]['start'], months_data[-2]['end']
        m3_start, m3_end = months_data[-1]['start'], months_data[-1]['end']
        
        m1_totals = {x['category']: float(x['total']) for x in Expense.objects.filter(user=user, date__range=[m1_start, m1_end]).values('category').annotate(total=Sum('base_amount'))}
        m2_totals = {x['category']: float(x['total']) for x in Expense.objects.filter(user=user, date__range=[m2_start, m2_end]).values('category').annotate(total=Sum('base_amount'))}
        m3_totals = {x['category']: float(x['total']) for x in Expense.objects.filter(user=user, date__range=[m3_start, m3_end]).values('category').annotate(total=Sum('base_amount'))}
        
        has_m1 = any(v > 0 for v in m1_totals.values())
        has_m2 = any(v > 0 for v in m2_totals.values())
        has_m3 = any(v > 0 for v in m3_totals.values())
        
        if has_m1 and has_m2 and has_m3:
            has_enough_creep_data = True
            
            from datetime import timedelta as _td

            from ..budget_resolver import effective_limits_by_name, plan_allowed_categories
            from ..budgets import has_limit, normalize_category
            all_cats = set(m3_totals.keys())
            # The warning is about NEXT month, so compare with the limit that will apply then
            allowed_names = {normalize_category(c.name) for c in plan_allowed_categories(user)}
            next_month = (m3_start.replace(day=28) + _td(days=4)).replace(day=1)
            cat_limits = {name: float(v) for name, v in effective_limits_by_name(user, next_month).items()
                          if name in allowed_names and has_limit(v)}
            
            for cat in all_cats:
                e1 = m1_totals.get(cat, 0.0)
                e2 = m2_totals.get(cat, 0.0)
                e3 = m3_totals.get(cat, 0.0)
                
                if e1 > 0 and e2 > e1 and e3 > e2:
                    g12 = (e2 - e1) / e1
                    g23 = (e3 - e2) / e2
                    avg_growth = (g12 + g23) / 2
                    pct_str = f"+{int(round(avg_growth * 100))}%"
                    limit = cat_limits.get(normalize_category(cat))
                    
                    if limit:
                        projected = e3 * (1 + avg_growth)
                        if projected > limit and e3 <= limit:
                            subtext = f"3-month trend — will breach {currency_symbol}{compact_amount(limit, currency_symbol)} budget next month at this pace"
                        else:
                            subtext = "Mild uptick — within budget but consistent upward movement"
                    else:
                        subtext = "Growing steadily — consistent upward movement month-over-month"
                    
                    creep_categories.append({
                        'name': cat,
                        'pct': pct_str,
                        'tag_class': 'bg-danger-subtle text-danger' if avg_growth >= 0.1 else ('bg-warning-subtle text-warning' if avg_growth >= 0.07 else 'bg-info-subtle text-info'),
                        'subtext': subtext,
                        'growth_rate': avg_growth
                    })
            
            creep_categories.sort(key=lambda x: x['growth_rate'], reverse=True)

    # Asset Allocation Health: how the money you hold is spread, from each account's value in your
    # currency (deposits accrued, holdings at market), counting only accounts with a positive balance.
    # Cards and loans are what you owe, not what you hold, and a user with nothing to spread sees an
    # empty card rather than invented figures.
    bank_codes = {'BANK', 'SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT'}
    growing_codes = set(investment_codes())
    bank_tot = Decimal('0.00')
    inv_tot = Decimal('0.00')
    total_tot = Decimal('0.00')
    for acc in Account.objects.filter(user=user, is_active=True):
        if strategy_for(acc.account_type) in (STRATEGY.REVOLVING_CREDIT, STRATEGY.LOAN_OUTSTANDING):
            continue
        value = current_balances.get(acc.pk, Decimal('0.00'))
        if value <= 0:
            continue
        if acc.account_type in bank_codes:
            bank_tot += value
        elif acc.account_type in growing_codes:
            inv_tot += value
        total_tot += value

    has_allocation = total_tot > 0
    bank_pct = (bank_tot / total_tot) * 100 if has_allocation else Decimal('0')
    inv_pct = (inv_tot / total_tot) * 100 if has_allocation else Decimal('0')

    if bank_tot >= 500000:
        suggest_sip = 5000
    elif bank_tot >= 200000:
        suggest_sip = 2000
    else:
        suggest_sip = 1000
    sip_added_val = suggest_sip * 36

    # Context Prep
    context = {
        'labels': json.dumps(labels),
        'has_data': has_data,
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
        'creep_categories': creep_categories,
        'has_enough_creep_data': has_enough_creep_data,
        'asset_allocation': {
            'has_data': has_allocation,
            'bank_pct': round(bank_pct, 1),
            'inv_pct': round(inv_pct, 1),
            'bank_balance_formatted': f"{currency_symbol}{compact_amount(bank_tot, currency_symbol)}",
            'inv_balance_formatted': f"{currency_symbol}{compact_amount(inv_tot, currency_symbol)}",
            'suggest_sip_formatted': f"{currency_symbol}{compact_amount(suggest_sip, currency_symbol)}",
            'sip_added_3y': f"{currency_symbol}{compact_amount(sip_added_val, currency_symbol)}",
            'bank_status_class': 'bg-danger' if bank_pct > 50 else 'bg-success',
            'bank_status_label': 'Over-idle' if bank_pct > 50 else 'Healthy',
            'bank_status_badge_class': 'bg-danger-subtle text-danger' if bank_pct > 50 else 'bg-success-subtle text-success',
            'inv_status_class': 'bg-warning' if inv_pct < 20 else 'bg-success',
            'inv_status_label': 'Under-invested' if inv_pct < 20 else 'Healthy Target',
            'inv_status_badge_class': 'bg-warning-subtle text-warning' if inv_pct < 20 else 'bg-success-subtle text-success',
        }
    }
    
    return render(request, 'mom_analysis.html', context)
