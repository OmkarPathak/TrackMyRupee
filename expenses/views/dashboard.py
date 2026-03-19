from datetime import datetime, date, timedelta
import calendar
from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView
from django.db.models import Sum, Count, Q
from django.db.models.functions import TruncMonth, TruncDay, ExtractWeekDay
from django.urls import reverse
from django.utils.translation import gettext as _
from django.utils.html import mark_safe, escape, format_html, format_html_join
from django.http import JsonResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.contrib import messages

from ..models import Expense, Income, Category, UserProfile, RecurringTransaction, Account, Transfer
from ..utils import get_exchange_rate, generate_year_in_review_data
from .mixins import process_user_recurring_transactions
from ..templatetags.digit_filters import compact_amount

@login_required
def home_view(request):
    """
    Dashboard view with filters and multiple charts.
    """
    # Defensive check: Redirect to onboarding if user has NO data AND hasn't finished the flow
    try:
        if not request.user.profile.has_seen_tutorial:
            has_any_data = Expense.objects.filter(user=request.user).exists() or Income.objects.filter(user=request.user).exists()
            if not has_any_data:
                return redirect('onboarding')
    except UserProfile.DoesNotExist:
        # Ensure profile exists, then redirect
        UserProfile.objects.get_or_create(user=self.request.user if hasattr(self, 'request') else request.user)
        return redirect('onboarding')

    # Process recurring transactions
    process_user_recurring_transactions(request.user)
    
    # Global currency symbol for insights/metrics
    currency_symbol = request.user.profile.currency if hasattr(request.user, 'profile') else '₹'

    # Base QuerySet - All user expenses
    expenses = Expense.objects.filter(user=request.user).order_by('-date')
    
    # Wealth Growth (Investments) - Transfers to Investment accounts
    investments = Transfer.objects.filter(user=request.user, to_account__account_type='INVESTMENT')
    
    # Logic for EOM projection
    now = datetime.now()
    num_days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_passed = now.day
    
    # Filter Logic
    selected_years = request.GET.getlist('year')
    selected_months = request.GET.getlist('month')
    selected_categories = request.GET.getlist('category')
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    # Remove empty strings from lists
    selected_years = [y for y in selected_years if y]
    selected_months = [m for m in selected_months if m]
    selected_categories = [c for c in selected_categories if c]

    # Date Range takes precedence
    if start_date or end_date:
        if start_date:
            expenses = expenses.filter(date__gte=start_date)
        if end_date:
            expenses = expenses.filter(date__lte=end_date)
        
        # Reset lists for UI clarity since we are in range mode
        selected_years = []
        selected_months = []
        
        trend_title = _("Expenses Trend (Custom Range)")
    else:
        # Default to current month/year when no filter params are provided
        # (ignore non-filter params like 'onboarded' from onboarding redirect)
        has_filter_params = selected_years or selected_months or selected_categories
        if not has_filter_params:
            selected_years = [str(datetime.now().year)]
            selected_months = [str(datetime.now().month)]
        
        if selected_years:
            expenses = expenses.filter(date__year__in=selected_years)
        if selected_months:
            expenses = expenses.filter(date__month__in=selected_months)
            
        if len(selected_months) == 1 and len(selected_years) == 1:
            trend_title = _("Daily Expenses for %(month)s/%(year)s") % {'month': selected_months[0], 'year': selected_years[0]}
        else:
            trend_title = _("Monthly Expenses Trend")

    if selected_categories:
        expenses = expenses.filter(category__in=selected_categories)
        
    # Income Logic (Mirroring Expense Filters)
    incomes = Income.objects.filter(user=request.user)
    if start_date or end_date:
        if start_date:
            incomes = incomes.filter(date__gte=start_date)
        if end_date:
            incomes = incomes.filter(date__lte=end_date)
    else:
        if selected_years:
            incomes = incomes.filter(date__year__in=selected_years)
        if selected_months:
            incomes = incomes.filter(date__month__in=selected_months)
            investments = investments.filter(date__month__in=selected_months)
        if selected_years:
            investments = investments.filter(date__year__in=selected_years)

    if start_date or end_date:
        if start_date:
            investments = investments.filter(date__gte=start_date)
        if end_date:
            investments = investments.filter(date__lte=end_date)
    
    total_income = incomes.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
    total_investments = investments.aggregate(Sum('amount'))['amount__sum'] or 0
    all_dates = Expense.objects.filter(user=request.user).dates('date', 'year', order='DESC')
    years = sorted(list(set([d.year for d in all_dates] + [datetime.now().year])), reverse=True)
    all_categories = Expense.objects.filter(user=request.user).values_list('category', flat=True).distinct().order_by('category')

    # 1. Category Chart Data (Distribution) & Summary Table
    # We need to fetch raw values and merge them in Python to handle whitespace duplicates
    raw_category_data = expenses.values('category').annotate(total=Sum('base_amount'))
    
    # Process and merge duplicates
    merged_category_map = {}
    for item in raw_category_data:
        # Strip whitespace to normalize
        cat_name = item['category'].strip()
        amount = float(item['total'])
        
        if cat_name in merged_category_map:
            merged_category_map[cat_name] += amount
        else:
            merged_category_map[cat_name] = amount
            
    # Convert back to list of dicts for template/charts, sorted by total
    # This replaces the DB-ordered queryset with a sorted list
    category_data = [
        {'category': cat, 'total': amt} 
        for cat, amt in merged_category_map.items()
    ]
    category_data.sort(key=lambda x: x['total'], reverse=True)

    # Compute limits and usage per category for chart display
    category_limits = []
    # Optimization: Pre-fetch all categories for the user to avoid N+1 queries in the loop
    user_categories = {c.name: c for c in Category.objects.filter(user=request.user)}

    is_current_month_view = (len(selected_months) == 1 and str(now.month) in selected_months and str(now.year) in selected_years)

    for item in category_data:
        cat_name = item['category']
        cat_obj = user_categories.get(cat_name)
        
        limit = float(cat_obj.limit) if (cat_obj and cat_obj.limit) else None
        
        used_percent = round((item['total'] / limit * 100), 1) if limit else None
        
        # Calculate projection
        projected_total = None
        projected_percent = None
        if limit and days_passed > 0 and is_current_month_view:
            projected_total = (item['total'] / days_passed) * num_days_in_month
            projected_percent = round((projected_total / limit * 100), 1)

        category_limits.append({
            'name': cat_name,
            'total': item['total'],
            'limit': limit,
            'used_percent': used_percent,
            'projected_total': projected_total,
            'projected_percent': projected_percent,
        })
    
    total_monthly_budget = sum([float(c.limit) for c in user_categories.values() if c.limit])
    
    # Sort category data by total descending (already done at line 148, but reinforcing logic)
    category_data.sort(key=lambda x: x['total'], reverse=True)

    # 1.5 Group into Top 5 + Others for Chart Clarity
    if len(category_data) > 5:
        top_5 = category_data[:5]
        others_total = sum(float(item['total']) for item in category_data[5:])
        
        categories = [item['category'] for item in top_5] + [str(_("Others"))]
        category_amounts = [float(item['total']) for item in top_5] + [others_total]
    else:
        categories = [item['category'] for item in category_data]
        category_amounts = [float(item['total']) for item in category_data]
    
    # 2. Time Trend (Stacked) Data
    
    # Determine Labels (X-Axis)
    # Determine Labels (X-Axis)
    if start_date or end_date:
        # For custom range, if range < 60 days, show daily. Else monthly.
        # Simple heuristic: Always show daily for custom range for now, or let logic decide.
        # Let's stick to: if explicit month selected -> daily. If range -> daily (usually granular).
        trend_qs = expenses.annotate(period=TruncDay('date'))
        date_format = '%d %b'
    elif len(selected_months) == 1 and len(selected_years) == 1:
        # Daily view
        trend_qs = expenses.annotate(period=TruncDay('date'))
        date_format = '%d %b'
    else:
        # Monthly view
        trend_qs = expenses.annotate(period=TruncMonth('date'))
        date_format = '%b %Y'

    # Aggregate by Period for Total Spend
    total_data = trend_qs.values('period').annotate(total=Sum('base_amount')).order_by('period')
    
    # Process into Chart.js Datasets
    periods = [item['period'] for item in total_data]
    trend_labels = [p.strftime(date_format) for p in periods]
    trend_iso_dates = [p.strftime('%Y-%m-%d') for p in periods]
    
    trend_data = [float(item['total']) for item in total_data]

    # Determine if this is a daily (single-month) view
    trend_is_daily = bool(
        (start_date or end_date) or
        (len(selected_months) == 1 and len(selected_years) == 1)
    )

    # Compute 7-day rolling average for daily views
    trend_7d_avg = []
    if trend_is_daily and len(trend_data) > 1:
        for i in range(len(trend_data)):
            window = trend_data[max(0, i - 6):i + 1]
            trend_7d_avg.append(round(sum(window) / len(window), 2))

    trend_datasets = [{
        'label': str(_('Total Spent')),
        'data': trend_data,
        'backgroundColor': '#219EBC',
        'borderRadius': 4
    }]

    # 3. Top 5 Expenses
    top_expenses_qs = expenses.order_by('-base_amount')[:5]
    top_labels = [
        (e.description.decode('utf-8', errors='replace') if isinstance(e.description, bytes) else str(e.description))[:20] + '...' 
        if len(str(e.description)) > 20 else str(e.description) 
        for e in top_expenses_qs
    ]
    top_amounts = [float(e.base_amount) for e in top_expenses_qs]

    # --- NEW: Income vs Expenses Trend Data ---
    # Re-use the truncation logic determined above
    if start_date or end_date or (len(selected_months) == 1 and len(selected_years) == 1):
        trunc_func = TruncDay
    else:
        trunc_func = TruncMonth
        
    inc_trend = incomes.annotate(period=trunc_func('date')).values('period').annotate(total=Sum('base_amount')).order_by('period')
    exp_trend = expenses.annotate(period=trunc_func('date')).values('period').annotate(total=Sum('base_amount')).order_by('period')
    
    # Merge periods
    inc_periods = set(i['period'] for i in inc_trend)
    exp_periods = set(e['period'] for e in exp_trend)
    all_periods_sorted = sorted(list(inc_periods.union(exp_periods)))
    
    ie_labels = [p.strftime(date_format) for p in all_periods_sorted]
    
    # Optimization: Use dict lookup instead of filter inside loop
    inc_map = {i['period']: float(i['total']) for i in inc_trend}
    exp_map = {e['period']: float(e['total']) for e in exp_trend}
    
    ie_income_data = [inc_map.get(p, 0.0) for p in all_periods_sorted]
    ie_expense_data = [exp_map.get(p, 0.0) for p in all_periods_sorted]
    ie_savings_data = [inc_map.get(p, 0.0) - exp_map.get(p, 0.0) for p in all_periods_sorted]

    # --- NEW: Payment Method Distribution ---
    raw_payment_data = expenses.values('payment_method').annotate(total=Sum('base_amount')).order_by('payment_method')
    payment_map = {}
    for item in raw_payment_data:
        pm_name = item['payment_method'] or 'Unknown'
        payment_map[pm_name] = float(item['total'])
    
    # Sort by total desc
    sorted_payment_items = sorted(payment_map.items(), key=lambda x: x[1], reverse=True)
    payment_labels = [item[0] for item in sorted_payment_items]
    payment_data = [item[1] for item in sorted_payment_items]


    # 4. Summary Stats
    total_expenses = expenses.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
    transaction_count = expenses.count()
    top_category = category_data[0]['category'] if category_data else None
    
    savings = total_income - total_expenses

    # 4a. Internal Transfers (excluded from income/expense, just movement)
    transfers_qs = Transfer.objects.filter(user=request.user)
    if start_date or end_date:
        if start_date:
            transfers_qs = transfers_qs.filter(date__gte=start_date)
        if end_date:
            transfers_qs = transfers_qs.filter(date__lte=end_date)
    else:
        if selected_years:
            transfers_qs = transfers_qs.filter(date__year__in=selected_years)
        if selected_months:
            transfers_qs = transfers_qs.filter(date__month__in=selected_months)
    total_transfers = transfers_qs.aggregate(Sum('amount'))['amount__sum'] or 0
    transfer_count = transfers_qs.count()

    # --- NEW: Savings Projection (Linear Extrapolation) ---
    current_date = date.today()
    current_year = current_date.year
    current_month = current_date.month 

    # 1. Calculate YTD Savings (Strictly for current year, regardless of filters)
    ytd_income = Income.objects.filter(user=request.user, date__year=current_year, date__month__lte=current_month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
    ytd_expenses = Expense.objects.filter(user=request.user, date__year=current_year, date__month__lte=current_month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
    ytd_savings = ytd_income - ytd_expenses
    
    projected_savings = 0
    
    # Only project if we have data and positive savings
    if ytd_savings > 0:
        # Avoid division by zero if it's January (month 1)
        # Actually, even in Jan, months_passed is 1. So we are good.
        months_passed = current_month
        avg_monthly_savings = ytd_savings / months_passed
        
        months_remaining = 12 - months_passed
        projected_additional = avg_monthly_savings * months_remaining
        
        projected_savings = ytd_savings + projected_additional
    else:
        # If savings are negative or zero, projection is effectively "0" or "current state"
        # We might handle this in template
        projected_savings = 0

    # Calculate MoM Changes ONLY if exactly one year and one month are selected
    prev_month_data = None
    if len(selected_years) == 1 and len(selected_months) == 1:
        try:
            sel_year = int(selected_years[0])
            sel_month = int(selected_months[0])
            
            # Calculate previous month and year
            if sel_month == 1:
                prev_month = 12
                prev_year = sel_year - 1
            else:
                prev_month = sel_month - 1
                prev_year = sel_year

            # Current year-month stats
            prev_expenses_all = Expense.objects.filter(user=request.user, date__year=prev_year, date__month=prev_month)
            prev_expenses_op = prev_expenses_all.aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            prev_investments = Transfer.objects.filter(
                user=request.user, to_account__account_type='INVESTMENT',
                date__year=prev_year, date__month=prev_month
            ).aggregate(Sum('amount'))['amount__sum'] or 0

            prev_income = Income.objects.filter(user=request.user, date__year=prev_year, date__month=prev_month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            prev_savings = prev_income - prev_expenses_op

            def calc_pct(current, previous):
                if previous == 0:
                    return None
                return ((current - previous) / previous) * 100

            prev_month_data = {
                'income': prev_income,
                'expense': prev_expenses_op,
                'investments': prev_investments,
                'savings': prev_savings,
                'income_pct': calc_pct(total_income, prev_income),
                'expense_pct': calc_pct(total_expenses, prev_expenses_op),
                'investments_pct': calc_pct(total_investments, prev_investments),
                'savings_pct': calc_pct(savings, prev_savings),
                'savings_rate': (prev_savings / prev_income * 100) if prev_income > 0 else 0,
                'income_diff_amount': total_income - prev_income,
                'expense_diff_amount': total_expenses - prev_expenses_op,
                'investments_diff_amount': total_investments - prev_investments,
            }
            # Add absolute versions for percentages for template display
            for key in list(prev_month_data.keys()):
                val = prev_month_data[key]
                if val is not None and key.endswith('_pct'):
                    prev_month_data[f'{key}_abs'] = abs(val)
        except (ValueError, IndexError):
            pass

    # Calculate Hero Metrics for the Ideal Layout
    savings_rate_value = (savings / total_income * 100) if total_income > 0 else 0
    hero_status = 'needs_attention'
    if savings_rate_value >= 20:
        hero_status = 'excellent'
    elif savings_rate_value > 0:
        hero_status = 'good'
        
    trend_text = None
    trend_type = None
    if prev_month_data and 'savings_rate' in prev_month_data:
        rate_diff = savings_rate_value - prev_month_data['savings_rate']
        if rate_diff > 0:
            trend_text = f"Savings rate ↑ +{rate_diff:.1f}% vs last month"
            trend_type = 'positive'
        elif rate_diff < 0:
            trend_text = f"Savings rate ↓ {rate_diff:.1f}% vs last month"
            trend_type = 'negative'
        else:
            trend_text = "Savings rate unchanged vs last month"
            trend_type = 'neutral'

    hero_metrics = {
        'income': total_income,
        'spent': total_expenses,
        'saved': savings,
        'savings_rate': round(savings_rate_value, 1),
        'status': hero_status,
        'trend_text': trend_text,
        'trend_type': trend_type
    }

    # Prepare display labels for the template
    display_year = None
    display_month = None
    
    if len(selected_years) == 1:
        display_year = selected_years[0]
        
    if len(selected_months) == 1:
        try:
            m_idx = int(selected_months[0])
            display_month = _(calendar.month_name[m_idx])
        except (ValueError, IndexError):
            pass

    # NEW: Calculate Previous/Next Month URLs
    prev_month_url = None
    next_month_url = None

    if len(selected_years) == 1 and len(selected_months) == 1:
        try:
            curr_year = int(selected_years[0])
            curr_month = int(selected_months[0])
            
            # Previous Month
            if curr_month == 1:
                pm = 12
                py = curr_year - 1
            else:
                pm = curr_month - 1
                py = curr_year
            
            # Next Month
            if curr_month == 12:
                nm = 1
                ny = curr_year + 1
            else:
                nm = curr_month + 1
                ny = curr_year

            # Construct Query String (Preserve Categories)
            base_qs = []
            for c in selected_categories:
                base_qs.append(f'category={c}')
            
            qs_prev = base_qs + [f'year={py}', f'month={pm}']
            qs_next = base_qs + [f'year={ny}', f'month={nm}']
            
            prev_month_url = f"{reverse('home')}?{'&'.join(qs_prev)}"
            next_month_url = f"{reverse('home')}?{'&'.join(qs_next)}"
            
        except ValueError:
            pass
    
    # --- Emotional Feedback / Insights Logic (Enhanced) ---
    
    insights = []
    
    # helper for streaks
    def get_monthly_savings_status(u, y, m):
        inc = Income.objects.filter(user=u, date__year=y, date__month=m).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        exp = Expense.objects.filter(user=u, date__year=y, date__month=m).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        return inc > exp

    # Construct date params for deep linking
    date_params = ""
    for y in selected_years:
        date_params += f"&year={y}"
    for m in selected_months:
        date_params += f"&month={m}"

    # helper for category links
    def link_cats(cats):
        links_html = format_html_join(
            mark_safe(', '),
            '<a href="{}" class="alert-link text-decoration-underline">{}</a>',
            ((reverse('expense-list') + f"?category={c}{date_params}", c) for c in cats[:2])
        )
        if len(cats) > 2:
            return format_html('{}, etc.', links_html)
        return links_html

    # 0. Anomaly Detection (Spending Spike)
    # Only if viewing current month (or default view)
    if is_current_month_view and total_expenses > 0:
        # Calculate last 3 months average
        last_3_months_total = 0
        months_counted = 0
        for i in range(1, 4):
            # Calculate past month/year
            y = now.year
            m = now.month - i
            while m < 1:
                m += 12
                y -= 1
            
            m_total = Expense.objects.filter(user=request.user, date__year=y, date__month=m).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            if m_total > 0:
                last_3_months_total += m_total
                months_counted += 1
        
        if months_counted > 0:
            avg_past_spend = last_3_months_total / months_counted
            
            # Project current month
            days_in_month = calendar.monthrange(now.year, now.month)[1]
            days_passed = now.day
            if days_passed > 0:
                projected_spend = (float(total_expenses) / days_passed) * days_in_month
                avg_past_spend_float = float(avg_past_spend)
                
                if projected_spend > avg_past_spend_float * 1.25 and float(total_expenses) > 1000: # 25% Higher + Min Threshold
                    pct_higher = int(((projected_spend - avg_past_spend_float) / avg_past_spend_float) * 100)
                    insights.append({
                        'type': 'warning',
                        'icon': 'graph-up-arrow',
                        'title': _('Traffic Alert'),
                        'message': _("You're pacing %(pct_higher)s%% higher than usual. Slow down to stay on track!") % {'pct_higher': pct_higher},
                        'allow_share': False
                    })

        # 0.6 Predictive Spending Speed Warning
        speed_alert_categories = []
        for cat in category_limits:
            if cat['limit'] and cat['projected_percent'] and cat['projected_percent'] > 100 and (cat['used_percent'] or 0) <= 100:
                speed_alert_categories.append(cat['name'])
                
        if speed_alert_categories:
            if len(speed_alert_categories) == 1:
                msg = _("Trends show you might overspend on %(category)s soon. Take care!") % {'category': speed_alert_categories[0]}
            elif len(speed_alert_categories) == 2:
                msg = _("Trends show you might overspend on %(cat1)s and %(cat2)s soon.") % {'cat1': speed_alert_categories[0], 'cat2': speed_alert_categories[1]}
            else:
                msg = _("Trends show you might overspend on %(count)s categories soon (%(cats)s, etc).") % {
                    'count': len(speed_alert_categories),
                    'cats': ', '.join(speed_alert_categories[:2])
                }
                
            insights.append({
                'type': 'warning',
                'icon': 'speedometer',
                'title': _('Spending Speed Alert'),
                'message': msg,
                'allow_share': False
            })
        
    # --- "Where Did My Salary Go?" Data ---
    salary_breakdown = None
    if total_income > 0 and total_expenses > 0:
        # 1. Top 5 Categories
        top_5_categories = category_data[:5]
        
        # 2. Savings Rate
        savings_rate = (savings / total_income) * 100 if total_income > 0 else 0
        
        # 3. AI Insight (Trend analysis for top category)
        viral_insight = None
        if top_5_categories:
            top_cat = top_5_categories[0]['category']
            top_amount = top_5_categories[0]['total']
            
            # Calculate Percentage of total lifestyle expenses
            top_cat_pct = round((float(top_amount) / float(total_expenses) * 100)) if total_expenses > 0 else 0
            potential_savings = float(top_amount) * 0.20
            
            # POWER INSIGHT: Granular and Actionable
            power_insight = format_html(
                _("You spent <b>{pct}%</b> of your expenses on <b>{cat}</b> this month. If you reduce it by 20%, you could save <b>{sym}{savings}</b>."),
                pct=top_cat_pct,
                cat=_(top_cat),
                sym=currency_symbol,
                savings=compact_amount(potential_savings, currency_symbol)
            )
            
            # Update viral insight with this more powerful one
            # viral_insight = power_insight  # Moving this to smart_bullet_insights
            
            # Calculate 3-month average for this specific top category
            cat_3_month_total = 0
            cat_months_counted = 0
            for i in range(1, 4):
                y_calc = now.year
                m_calc = now.month - i
                while m_calc < 1:
                    m_calc += 12
                    y_calc -= 1
                
                m_cat_total = Expense.objects.filter(user=request.user, date__year=y_calc, date__month=m_calc, category__iexact=top_cat).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
                if m_cat_total > 0:
                    cat_3_month_total += m_cat_total
                    cat_months_counted += 1
            
            if cat_months_counted > 0:
                cat_avg = float(cat_3_month_total) / cat_months_counted
                if float(top_amount) > cat_avg * 1.1: # 10% higher
                    diff_pct = int(((float(top_amount) - cat_avg) / cat_avg) * 100)
                    viral_insight = _("You spent %(diff_pct)s%% more on %(category)s than your 3-month average.") % {
                        'diff_pct': diff_pct,
                        'category': _(top_cat)
                    }
        
        # 4. Daily Burn Rate
        num_days = calendar.monthrange(now.year, now.month)[1]
        if selected_months and len(selected_months) == 1:
            try:
                m = int(selected_months[0])
                y = int(selected_years[0]) if selected_years else now.year
                num_days = calendar.monthrange(y, m)[1]
            except:
                pass
        
        # If it's the current month, we might want to use days elapsed for a "live" feel
        days_elapsed = now.day if (not selected_months or (len(selected_months) == 1 and int(selected_months[0]) == now.month)) else num_days
        daily_burn = float(total_expenses) / days_elapsed if days_elapsed > 0 else 0

        # 5. Relatable Metric (Fun/Viral)
        relatable_metric = None
        if top_5_categories:
            top_amount = float(top_5_categories[0]['total'])
            top_cat_name = top_5_categories[0]['category']
            
            # Simple mapping of relatable items
            items = [
                {'name': _('Netflix subscriptions'), 'price': 499, 'icon': 'play-btn-fill'},
                {'name': _('Starbucks coffees'), 'price': 350, 'icon': 'cup-hot-fill'},
                {'name': _('premium Gym memberships'), 'price': 2500, 'icon': 'bicycle'},
            ]
            
            import random
            item = random.choice(items)
            count = int(top_amount / item['price'])
            
            if count > 1:
                relatable_metric = {
                    'text': _("Your %(category)s spend is equivalent to %(count)s %(item)s.") % {
                        'category': _(top_cat_name),
                        'count': count,
                        'item': item['name']
                    },
                    'icon': item['icon']
                }

        # Ideal spending pace: how much should have been spent by now
        ideal_spent_so_far = (total_monthly_budget / num_days * days_elapsed) if (total_monthly_budget > 0 and num_days > 0) else 0
        budget_diff = round(ideal_spent_so_far - float(total_expenses), 0)  # positive = under budget
        spent_percent = round(float(total_expenses) / total_monthly_budget * 100, 1) if total_monthly_budget > 0 else 0
        ideal_percent = round(days_elapsed / num_days * 100, 1) if num_days > 0 else 0

        spending_pace = {
            'daily_spending_pace': round(daily_burn, 0),
            'projected_month_spend': round(daily_burn * num_days, 0),
            'status': 'on_track',
            'diff_amount': max(0, round((daily_burn * num_days) - total_monthly_budget, 0)),
            'budget_multiplier': round((daily_burn * num_days) / total_monthly_budget, 1) if total_monthly_budget > 0 else 0,
            'budget_diff': budget_diff,
            'spent_percent': min(spent_percent, 150),  # cap at 150% for display
            'ideal_percent': ideal_percent,
            'days_elapsed': days_elapsed,
            'num_days': num_days,
            'ideal_spent_so_far': round(ideal_spent_so_far, 0),
        }

        short_insight = ""
        if hero_metrics['status'] == 'excellent':
            short_insight = _("🟢 Great month! You're saving more than usual.")
        elif hero_metrics['status'] == 'good':
            short_insight = _("🔵 Good progress. You're on the right track.")
        else:
            short_insight = _("🟠 Heads up. Expenses are slightly high this month.")

        hero_metrics['short_insight'] = short_insight

        if total_monthly_budget > 0:
            if spending_pace['projected_month_spend'] > total_monthly_budget:
                spending_pace['status'] = 'over_budget'
            elif spending_pace['projected_month_spend'] >= total_monthly_budget * 0.9:
                spending_pace['status'] = 'near_limit'

        salary_breakdown = {
            'income': total_income,
            'expenses': total_expenses,
            'savings': savings,
            'savings_rate': round(savings_rate, 1),
            'daily_burn': round(daily_burn, 0),
            'top_categories': category_limits[:3],
            'viral_insight': viral_insight,
            'relatable_metric': relatable_metric,
            'month_name': display_month if display_month else (calendar.month_name[now.month] if (selected_months and len(selected_months) == 1) else ""),
            'year': display_year if display_year else (now.year if (selected_years and len(selected_years) == 1) else ""),
            'spending_pace': spending_pace,
            'total_monthly_budget': total_monthly_budget
        }

    # 1. Budget Warnings (High Priority)

    over_budget_cats = [c for c in category_limits if c['used_percent'] is not None and c['used_percent'] > 100]
    near_budget_cats = [c for c in category_limits if c['used_percent'] is not None and 90 <= c['used_percent'] <= 100]
    
    # Check savings rate for "Softener" context
    savings_rate_alert = (savings / total_income * 100) if total_income > 0 else 0
    
    if over_budget_cats:
        if len(over_budget_cats) == 1:
            cat = over_budget_cats[0]
            exceeded = float(cat['total']) - float(cat['limit'])
            exceeded_str = compact_amount(exceeded, currency_symbol)
            
            if savings_rate_alert >= 20:
                msg = format_html(_("Even strong months have leaks. {cat_name} exceeded by {currency}{exceeded}."), cat_name=format_html("<b>{}</b>", cat['name']), currency=currency_symbol, exceeded=exceeded_str)
            else:
                msg = format_html(_("{cat_name} exceeded by {currency}{exceeded} — let’s rebalance to stay safe."), cat_name=format_html("<b>{}</b>", cat['name']), currency=currency_symbol, exceeded=exceeded_str)
        else:
            cat_details = []
            for cat in over_budget_cats:
                exceeded = float(cat['total']) - float(cat['limit'])
                exceeded_str = compact_amount(exceeded, currency_symbol)
                cat_details.append(format_html("<b>{}</b>: {}{}", cat['name'], currency_symbol, exceeded_str))
            
            cats_str = mark_safe(", ".join(cat_details))
            
            if savings_rate_alert >= 20:
                msg = format_html(_("Even strong months have leaks. {count} categories exceeded limits: {cats_str}."), count=len(over_budget_cats), cats_str=cats_str)
            else:
                msg = format_html(_("{count} categories exceeded limits: {cats_str} — let’s rebalance to stay safe."), count=len(over_budget_cats), cats_str=cats_str)

        insights.append({
            'type': 'warning', # Changed from danger
            'icon': 'exclamation-octagon-fill',
            'title': _('Budget Breached'),
            'message': msg,
            'allow_share': False
        })
    elif near_budget_cats:
        cats_str = link_cats([c['name'] for c in near_budget_cats])
        insights.append({
            'type': 'warning',
            'icon': 'exclamation-triangle-fill',
            'title': _('Approaching Limit'),
            'message': format_html(_("Heads up! You're close to overspending on {cats_str}."), cats_str=cats_str),
            'allow_share': False
        })

    # 2. Wins & Cause-Based Praise (Specific & Celebratory)
    if prev_month_data:
        # Calculate Category Savings (Cause of the win)
        # We need prev month category breakdown
        prev_cat_qs = Expense.objects.filter(user=request.user, date__year=prev_year, date__month=prev_month).values('category').annotate(total=Sum('base_amount'))
        prev_cat_map = {item['category'].strip(): float(item['total']) for item in prev_cat_qs}
        
        # Add micro trend indicators to category_limits
        for cat_info in category_limits:
            prev_total = prev_cat_map.get(cat_info['name'], 0)
            curr_total = cat_info['total']
            if prev_total > 0:
                diff_pct = ((curr_total - prev_total) / prev_total) * 100
                cat_info['trend_dir'] = 'up' if diff_pct > 0 else 'down' if diff_pct < 0 else 'flat'
                cat_info['trend_pct'] = abs(round(diff_pct))
        
        savings_contributors = []
        for cat, curr_total in merged_category_map.items():
            prev_total = prev_cat_map.get(cat, 0)
            if prev_total > curr_total:
                diff = prev_total - curr_total
                if diff > 100: # Threshold to mention
                    savings_contributors.append((cat, diff))
        savings_contributors.sort(key=lambda x: x[1], reverse=True)
        top_savers = [c[0] for c in savings_contributors[:2]]
        
        # Savings Win
        if total_income > 0 and savings > 0:
            savings_rate = (savings / total_income) * 100
            if savings_rate >= 20:
                msg_text = _("You've saved %(savings_rate)s%% of your income this month.") % {'savings_rate': f"{savings_rate:.0f}"}
                share_text = _("I saved %(savings_rate)s%% of my income this month using TrackMyRupee! 🏆") % {'savings_rate': f"{savings_rate:.0f}"}
                
                if top_savers:
                    cats_link = link_cats(top_savers)
                    msg = format_html(_("{msg_text} You spent less on {cats_link} — that's where the magic happened."), msg_text=msg_text, cats_link=cats_link)
                else:
                    msg = msg_text

                # Suppress "Super Saver" if Salary Breakdown is showing (as it's redundant)
                if not salary_breakdown:
                    insights.append({
                        'type': 'success',
                        'icon': 'trophy-fill',
                        'title': _('Super Saver Status! 🏆'),
                        'message': msg,
                        'allow_share': True,
                        'share_text': share_text
                    })
            elif prev_month_data['savings_pct'] and prev_month_data['savings_pct'] > 0:
                 insights.append({
                    'type': 'success',
                    'icon': 'graph-up-arrow',
                    'title': _('Momentum Building 🚀'),
                    'message': _("Your savings grew by %(savings_pct_abs)s%% vs last month. You're getting better at this!") % {'savings_pct_abs': f"{prev_month_data['savings_pct_abs']:.0f}"},
                    'allow_share': True,
                    'share_text': _("My savings grew by %(savings_pct_abs)s%% this month! 🚀 via TrackMyRupee") % {'savings_pct_abs': f"{prev_month_data['savings_pct_abs']:.0f}"}
                })
        
        # Expense Control Win (if we haven't already praised savings)
        if len(insights) == 0: 
            if prev_month_data['expense_pct'] and prev_month_data['expense_pct'] < -5:
                 msg_text = _("You've cut spending by %(expense_pct_abs)s%%.") % {'expense_pct_abs': f"{prev_month_data['expense_pct_abs']:.0f}"}
                 share_text = _("I cut my spending by %(expense_pct_abs)s%% this month! 👍 via TrackMyRupee") % {'expense_pct_abs': f"{prev_month_data['expense_pct_abs']:.0f}"}
                 
                 if top_savers:
                     cats_link = link_cats(top_savers)
                     msg = format_html(_("{msg_text} {cats_link} saw the biggest drops."), msg_text=msg_text, cats_link=cats_link)
                 else:
                     msg = msg_text
                 
                 insights.append({
                    'type': 'success',
                    'icon': 'check-circle-fill',
                    'title': _('You’re in Control 👍'),
                    'message': msg,
                    'allow_share': True,
                    'share_text': _("I cut my spending by %(expense_pct_abs)s%% this month! 👍 via TrackMyRupee") % {'expense_pct_abs': f"{prev_month_data['expense_pct_abs']:.0f}"}
                })

    # 3. Streak & Identity (Reassuring / Habit Forming)
    # Only calculate if current status is good
    if savings > 0 and len(selected_years) == 1 and len(selected_months) == 1:
        streak = 1 # Current month counts
        check_to_go = 5 # check max 5 months back
        curr_y_calc, curr_m_calc = int(selected_years[0]), int(selected_months[0])
        
        for i in range(check_to_go):
            # Go back one month
            if curr_m_calc == 1:
                curr_m_calc = 12
                curr_y_calc -= 1
            else:
                curr_m_calc -= 1
            
            if get_monthly_savings_status(request.user, curr_y_calc, curr_m_calc):
                streak += 1
            else:
                break
        
        if streak > 1:
            insights.append({
                'type': 'info', # Use Info for "Identity/Streak"
                'icon': 'fire',
                'title': _('On a Roll!'),
                'message': _("🔥 This is your %(streak)s month in a row staying under budget.") % {'streak': streak},
                'allow_share': True,
                'share_text': _("🔥 I've stayed under budget for %(streak)s months in a row! via TrackMyRupee") % {'streak': streak}
            })

    # NEW: Wealth Projection
    if projected_savings > 0:
        def format_indian_lakhs(amount):
            if amount >= 100000:
                return f"{amount/100000:.1f}L"
            elif amount >= 1000:
                return f"{amount/1000:.1f}k"
            return f"{amount:.0f}"
            
        proj_str = format_indian_lakhs(projected_savings)
        proj_bold = mark_safe(f"<b>{currency_symbol}{proj_str}</b>")
        insights.append({
            'type': 'success', 
            'icon': 'graph-up-arrow',
            'title': _('Wealth Projection'),
            'message': format_html(
                _("If you maintain this savings rate, you could accumulate {proj} this year. This reinforces future thinking."),
                proj=proj_bold
            ),
            'allow_share': True,
            'share_text': _("I'm on track to save big this year! 📈 via TrackMyRupee")
        })

    # 4. Fallback
    if not insights and savings > 0 and not salary_breakdown:
        insights.append({
            'type': 'info',
            'icon': 'piggy-bank-fill',
            'title': _('In the Green'),
            'message': _("You've saved %(savings)s so far. Keep it up!") % {'savings': savings},
            'allow_share': False
        })
    elif not insights and not salary_breakdown:
        insights.append({
            'type': 'secondary',
            'icon': 'stars',
            'title': _('Fresh Start'),
            'message': _("Small steps today lead to big results tomorrow. Let's track some expenses!"),
            'allow_share': False
        })

    # Split into Actionable Alerts and Smart Insights
    smart_insights = []
    actionable_alerts = []
    
    for insight in insights:
        if insight.get('type') in ['warning', 'danger']:
            actionable_alerts.append(insight)
        else:
            smart_insights.append(insight)
            
    # Add Upcoming Subscriptions to Alerts
    active_recurring = RecurringTransaction.objects.filter(
        user=request.user, 
        is_active=True
    )
    
    upcoming_payments = []
    today_date = date.today()
    seven_days_later = today_date + timedelta(days=7)
    
    for payment in active_recurring:
        if payment.next_due_date and today_date <= payment.next_due_date <= seven_days_later:
            upcoming_payments.append(payment)
            
    upcoming_payments.sort(key=lambda x: x.next_due_date)
    upcoming_payments = upcoming_payments[:3]
    
    for payment in upcoming_payments:
        days_left = (payment.next_due_date - date.today()).days
        if days_left == 0:
            when = _("Today")
        elif days_left == 1:
            when = _("Tomorrow")
        else:
            when = _("in %(days)s days") % {'days': days_left}
            
        actionable_alerts.append({
            'type': 'warning',
            'icon': 'calendar-event-fill',
            'title': _('Upcoming Payment'),
            'message': format_html(_("Your recurring payment for <b>{}</b> is due {}. ({})"), payment.description, when, f"{currency_symbol}{compact_amount(payment.amount, currency_symbol)}"),
            'allow_share': False
        })
        
    smart_insights = smart_insights[:3] # Limit to 3 (Layer 3 rule)
    
    # Layer 5: Monthly Story Generation
    if len(selected_months) == 1 and len(selected_years) == 1:
        story_month_name = calendar.month_name[int(selected_months[0])]
    else:
        story_month_name = _("This period")

    # Calculate Future Growth (Total Surplus = Investments + Remaining Savings)
    # savings is already (income - lifestyle_expenses) because total_expenses excludes investments
    total_wealth_contribution = max(0, savings)
    future_growth_pct = round((float(total_wealth_contribution) / float(total_income) * 100)) if total_income > 0 else 0
    lifestyle_pct = round((float(total_expenses) / float(total_income) * 100)) if total_income > 0 else 0
    
    income_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_income, currency_symbol)}</b>")
    lifestyle_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_expenses, currency_symbol)}</b>")
    invest_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_investments, currency_symbol)}</b>")
    future_total_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_wealth_contribution, currency_symbol)}</b>")
    
    # Narrative Structure
    monthly_story = format_html(
        _("In {month}, you earned {income}. You spent {lifestyle} on lifestyle, and invested {invest} toward future wealth."),
        month=story_month_name,
        income=income_bold,
        lifestyle=lifestyle_bold,
        invest=invest_bold
    )

    if total_wealth_contribution > 0:
        monthly_story += format_html(
            _(" That means {future_total} ({future_pct}%) went toward your future, while {lifestyle} ({life_pct}%) funded your lifestyle."),
            future_total=future_total_bold,
            future_pct=future_growth_pct,
            lifestyle=lifestyle_bold,
            life_pct=lifestyle_pct
        )
    
    if projected_savings > 0:
        proj_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(projected_savings, currency_symbol)}</b>")
        monthly_story += format_html(
            _(" At this pace, you could save {proj} by year's end."),
            proj=proj_bold
        )
    # Check for onboarding (True if user has NO data at all)
    has_any_data = Expense.objects.filter(user=request.user).exists() or Income.objects.filter(user=request.user).exists()

    # Logic for "Year in Review" Banner
    show_year_in_review = False
    year_in_review_year = None
    if has_any_data:
        # Show last year's review from Jan to Oct
        # Show this year's review in Nov/Dec
        if now.month >= 11:
            year_in_review_year = now.year
        else:
            year_in_review_year = now.year - 1
            
        if year_in_review_year:
            show_year_in_review = Expense.objects.filter(user=request.user, date__year=year_in_review_year).exists()

    # --- Smart Insights Bullets (New Card) ---
    smart_bullet_insights = []
    
    # 0. Power AI Insight (Top Priority)
    if total_income > 0 and total_expenses > 0 and top_5_categories:
        top_cat = top_5_categories[0]['category']
        top_amount = top_5_categories[0]['total']
        top_cat_pct = round((float(top_amount) / float(total_expenses) * 100)) if total_expenses > 0 else 0
        potential_savings = float(top_amount) * 0.20
        
        power_insight_text = format_html(
            _("You spent <b>{pct}%</b> of your expenses on <b>{cat}</b>. Reduce by 20% to save <span class='text-success fw-bold'>{sym}{savings}</span>."),
            pct=top_cat_pct,
            cat=_(top_cat),
            sym=currency_symbol,
                savings=compact_amount(potential_savings, currency_symbol)
        )
        smart_bullet_insights.append({
            'text': power_insight_text,
            'icon': 'bi-robot',
            'theme': 'primary'
        })
    
    # 1. Highest Spending Category
    if top_category:
        cat_url = f"{reverse('expense-list')}?category={top_category}"
        cat_obj = user_categories.get(top_category)
        icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
        smart_bullet_insights.append({
            'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> is your top expense this month."), url=cat_url, cat=top_category),
            'icon': icon_cls,
            'theme': 'primary'
        })

    # 2. Budget Breaches (High Priority)
    for cat in over_budget_cats:
        if len(smart_bullet_insights) >= 4: break
        over_amt = cat['total'] - cat['limit']
        if over_amt > 0:
            cat_url = f"{reverse('expense-list')}?category={cat['name']}"
            cat_obj = user_categories.get(cat['name'])
            icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
            smart_bullet_insights.append({
                'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> category exceeded budget by <span class='text-danger fw-bold'>{sym}{amt}</span>"), url=cat_url, cat=cat['name'], sym=currency_symbol, amt=int(over_amt)),
                'icon': icon_cls,
                'theme': 'danger'
            })
            
    # 3. Category MoM Spikes & Drops (Diverse insights)
    if prev_month_data and len(selected_years) == 1 and len(selected_months) == 1:
        # Fetch previous month category totals
        prev_cat_data = Expense.objects.filter(
            user=request.user, 
            date__year=prev_year, 
            date__month=prev_month
        ).values('category').annotate(total=Sum('base_amount'))
        
        prev_cat_map = {item['category'].strip(): float(item['total']) for item in prev_cat_data}
        
        for item in category_data:
            if len(smart_bullet_insights) >= 4: break
            cat_name = item['category']
            current_total = float(item['total'])
            prev_total = prev_cat_map.get(cat_name, 0)
            
            if prev_total > 0:
                diff_pct = ((current_total - prev_total) / prev_total) * 100
                
                # Check for significant spike (10%+)
                if diff_pct >= 10:
                    # Avoid redundancy if already in budget breaches or top category
                    is_redundant = any(c.get('cat') == cat_name or c.get('text').find(cat_name) != -1 for c in smart_bullet_insights)
                    if not is_redundant:
                        cat_url = f"{reverse('expense-list')}?category={cat_name}"
                        cat_obj = user_categories.get(cat_name)
                        icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
                        smart_bullet_insights.append({
                            'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> spending up <span class='text-danger fw-bold'>{pct}%</span> vs last month"), url=cat_url, cat=cat_name, pct=int(diff_pct)),
                            'icon': icon_cls,
                            'theme': 'warning'
                        })
                
                # Check for significant drop (10%+)
                elif diff_pct <= -10:
                    is_redundant = any(c.get('text').find(cat_name) != -1 for c in smart_bullet_insights)
                    if not is_redundant:
                        cat_url = f"{reverse('expense-list')}?category={cat_name}"
                        cat_obj = user_categories.get(cat_name)
                        icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
                        smart_bullet_insights.append({
                            'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> spending dropped <span class='text-success fw-bold'>{pct}%</span>! Nice job."), url=cat_url, cat=cat_name, pct=int(abs(diff_pct))),
                            'icon': icon_cls,
                            'theme': 'success'
                        })

    # Fallback/Empty state for smart insights
    if not smart_bullet_insights:
        smart_bullet_insights.append({
            'text': _("You're maintaining a steady financial pace."),
            'icon': 'bi-check-circle',
            'theme': 'success'
        })
        if hero_metrics['saved'] > 0:
            smart_bullet_insights.append({
                'text': _("Consistent tracking leads to better wealth."),
                'icon': 'bi-stars',
                'theme': 'primary'
            })

    # --- Recurring Transactions Summary (Optimized & Grouped) ---
    v_month = int(selected_months[0]) if len(selected_months) == 1 else now.month
    v_year = int(selected_years[0]) if len(selected_years) == 1 else now.year
    
    recurring_groups = {
        'INCOME': {'items': [], 'total': Decimal('0.00'), 'icon': '💰', 'label': _('Income')},
        'EXPENSE': {'items': [], 'total': Decimal('0.00'), 'icon': '💸', 'label': _('Expenses')},
        'INVESTMENT': {'items': [], 'total': Decimal('0.00'), 'icon': '📈', 'label': _('Investments')},
        'TRANSFER': {'items': [], 'total': Decimal('0.00'), 'icon': '🔄', 'label': _('Transfers')},
    }
    
    total_recurring_commitment = Decimal('0.00')
    
    active_recurring = RecurringTransaction.objects.filter(user=request.user, is_active=True)
    for rt in active_recurring:
        # Find if it occurs in the viewed month
        due_date = rt.start_date
        max_loops = 500 # Safety
        loops = 0
        while (due_date.year < v_year or (due_date.year == v_year and due_date.month < v_month)) and loops < max_loops:
            due_date = rt.get_next_date(due_date, rt.frequency)
            loops += 1
            
        if due_date.year == v_year and due_date.month == v_month:
            rtype = rt.transaction_type
            # Determine if it's an investment - Now handled via Transfers
                
            item = {
                'id': rt.id,
                'description': rt.description,
                'amount': rt.base_amount,
                'date': due_date,
                'type': rtype,
                'category': rt.category or (rt.source if rt.transaction_type == 'INCOME' else (_('Transfer') if rt.transaction_type == 'TRANSFER' else _('Recurring'))),
                'frequency_label': rt.get_frequency_display(),
                'from_account': getattr(rt.from_account, 'name', '') if rt.transaction_type == 'TRANSFER' else '',
                'to_account': getattr(rt.to_account, 'name', '') if rt.transaction_type == 'TRANSFER' else '',
            }
            
            recurring_groups[rtype]['items'].append(item)
            recurring_groups[rtype]['total'] += rt.base_amount
            
            # Transfers are neutral - they move money between accounts, not income/expense
            if rtype == 'INCOME':
                total_recurring_commitment -= rt.base_amount
            elif rtype != 'TRANSFER':
                total_recurring_commitment += rt.base_amount

    # Sorting items within groups by date
    for group in recurring_groups.values():
        group['items'].sort(key=lambda x: x['date'])

    # Calculate Net Recurring Balance (Positive if surplus, Negative if deficit)
    recurring_net_balance = recurring_groups['INCOME']['total'] - recurring_groups['EXPENSE']['total'] - recurring_groups['INVESTMENT']['total']

    # --- Expense Projection Chart Logic ---
    proj_labels = []
    proj_historical = []
    proj_forecast = []
    
    # Calculate last 6 months labels and data
    for i in range(5, -1, -1):
        m = v_month - i
        y = v_year
        while m < 1:
            m += 12
            y -= 1
        
        month_label = calendar.month_name[m][:3] + " " + str(y)[2:]
        proj_labels.append(month_label)
        
        # Aggregate expenses (Operating Only)
        m_total = Expense.objects.filter(
            user=request.user, 
            date__year=y, 
            date__month=m
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        
        proj_historical.append(float(m_total))
        proj_forecast.append(None) # No forecast for historical months
    
    # Calculate if we have enough data to show a projection (BEFORE adding None values)
    # Need at least one month with significant spending (> 50) to make it meaningful
    has_projection = any(v > 50 for v in proj_historical)
    
    # Calculate projection for next 3 months
    # Improved average: Exclude current month (if it's the viewed/real current month) 
    # and zero-months to get a more realistic "normal" spending pace
    is_viewing_current = (v_month == now.month and v_year == now.year)
    basis_vals = proj_historical[:-1] if is_viewing_current else proj_historical
    basis_vals = [v for v in basis_vals if v > 100] # Exclude tiny/zero months
    
    avg_spend = sum(basis_vals) / len(basis_vals) if basis_vals else (sum(proj_historical) / len(proj_historical) if proj_historical else 0)
    last_hist_val = proj_historical[-1]
    
    # The last historical month is the 'bridge' for the forecast line
    proj_forecast[-1] = last_hist_val
    
    for i in range(1, 4):
        m = v_month + i
        y = v_year
        while m > 12:
            m -= 12
            y += 1
            
    for i in range(1, 4):
        m = v_month + i
        y = v_year
        while m > 12:
            m -= 12
            y += 1
            
        month_label = calendar.month_name[m][:3] + " " + str(y)[2:]
        proj_labels.append(month_label)
        proj_historical.append(None)
        proj_forecast.append(float(avg_spend))

    # Net Worth & Asset Allocation Calculation
    accounts = Account.objects.filter(user=request.user)
    net_worth = accounts.aggregate(Sum('balance'))['balance__sum'] or Decimal('0.00')
    investment_accounts_balance = accounts.filter(account_type='INVESTMENT').aggregate(Sum('balance'))['balance__sum'] or Decimal('0.00')
    
    # Group by account type for Asset Allocation chart
    allocation_qs = accounts.values('account_type').annotate(total=Sum('balance')).order_by('-total')
    asset_allocation = []
    account_type_display = dict(Account.ACCOUNT_TYPES)
    cumulative_percent = 0
    circumference = 2 * 3.14159 * 45
    for item in allocation_qs:
        percent = round((float(item['total']) / float(net_worth) * 100), 1) if net_worth > 0 else 0
        asset_allocation.append({
            'type': account_type_display.get(item['account_type'], item['account_type']),
            'total': float(item['total']),
            'percent': percent,
            'arc_length': (percent / 100) * circumference,
            'offset_length': (cumulative_percent / 100) * circumference
        })
        cumulative_percent += percent

    # 5. Unified Activity Feed (Combined Expenses, Incomes, Transfers)
    # We'll tag each with 'transaction_type' for the template
    recent_expenses = list(expenses.order_by('-date')[:10])
    for e in recent_expenses: e.transaction_type = 'EXPENSE'
    
    recent_incomes = list(incomes.order_by('-date')[:10])
    for i in recent_incomes: i.transaction_type = 'INCOME'
    
    recent_transfers = list(transfers_qs.order_by('-date')[:10])
    for t in recent_transfers: t.transaction_type = 'TRANSFER'

    from itertools import chain
    recent_activity = sorted(
        chain(recent_expenses, recent_incomes, recent_transfers),
        key=lambda x: x.date,
        reverse=True
    )[:10]

    context = {
        'net_worth': net_worth,
        'accounts': accounts,
        'asset_allocation': asset_allocation,
        'recent_activity': recent_activity,
        'investment_accounts_balance': investment_accounts_balance,
        'has_projection': has_projection,
        'is_new_user': not has_any_data,
        'actionable_alerts': actionable_alerts,
        'smart_insights': smart_insights,
        'monthly_story': monthly_story,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'savings': savings,
        'recent_activity': recent_activity,
        'categories': categories,
        'category_amounts': category_amounts,
        'category_data': category_data, # Passing full queryset for the summary table
        'category_limits': category_limits,
        'trend_labels': trend_labels,
        'trend_datasets': trend_datasets,
        'trend_title': trend_title,
        'trend_is_daily': trend_is_daily,
        'trend_7d_avg': trend_7d_avg,
        'top_labels': top_labels,
        'top_amounts': top_amounts,
        # New Context
        'ie_labels': ie_labels,
        'ie_income_data': ie_income_data,
        'ie_expense_data': ie_expense_data,
        'ie_savings_data': ie_savings_data,
        'payment_labels': payment_labels,
        'payment_data': payment_data,
        'years': years,
        'all_categories': all_categories,
        'selected_years': selected_years,
        'selected_months': selected_months,
        'selected_year': display_year,    # NEW: For template display labels
        'selected_month': display_month,  # NEW: For template display labels
        'selected_categories': selected_categories,
        'months_list': [(i, calendar.month_name[i]) for i in range(1, 13)],
        'recurring_groups': recurring_groups,
        'recurring_net_balance': recurring_net_balance,
        'total_recurring_commitment': total_recurring_commitment,
        'top_category': top_category,
        'projected_savings': projected_savings, # NEW
        'start_date': start_date,
        'end_date': end_date,
        'prev_month_data': prev_month_data,
        'prev_month_url': prev_month_url,
        'next_month_url': next_month_url,
        'show_tutorial': not request.user.profile.has_seen_tutorial or request.GET.get('tour') == 'true',
        'has_any_budget': any((c.get('limit') or 0) > 0 for c in category_limits),
        'show_year_in_review': show_year_in_review,
        'year_in_review_year': year_in_review_year,
        'salary_breakdown': salary_breakdown,
        'hero_metrics': hero_metrics,
        'smart_bullet_insights': smart_bullet_insights,
        'total_investments': total_investments,
        'total_transfers': total_transfers,
        'transfer_count': transfer_count,
        'trend_labels': trend_labels,
        'trend_iso_dates': trend_iso_dates,
        'selected_categories': selected_categories,
        'proj_labels': proj_labels,
        'proj_historical': proj_historical,
        'proj_forecast': proj_forecast,
    }
    return render(request, 'home.html', context)

@login_required
def complete_tutorial(request):
    if request.method == 'POST':
        profile = request.user.profile
        profile.has_seen_tutorial = True
        profile.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)

class AnalyticsView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/analytics.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Check for Pro Access
        if not hasattr(user, 'profile') or not user.profile.is_pro:
            context['is_locked'] = True
            return context
        
        today = timezone.now().date()
        
        # Get Available Years from User Data
        expense_years = list(Expense.objects.filter(user=user).dates('date', 'year').values_list('date__year', flat=True))
        income_years = list(Income.objects.filter(user=user).dates('date', 'year').values_list('date__year', flat=True))
        available_years = sorted(list(set(expense_years + income_years)), reverse=True)
        
        if not available_years:
            available_years = [today.year]
            
        # Determine Selected Year
        selected_year_str = self.request.GET.get('year')
        if selected_year_str and selected_year_str.isdigit():
            selected_year = int(selected_year_str)
        else:
            selected_year = today.year
            
        context['available_years'] = available_years
        context['selected_year'] = selected_year
        context['is_current_year'] = (selected_year == today.year)
        
        # 1. Monthly Trends (Selected Year)
        labels = []
        income_data = []
        expense_data = []
        balance_rate_data = []
        
        # Determine the start and end date for the selected year
        start_date = date(selected_year, 1, 1)
        end_date = date(selected_year, 12, 31)
        
        # Fetch data grouped by Month for the selected year
        monthly_income = Income.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount')).order_by('month')
        
        monthly_expenses = Expense.objects.filter(
            user=user, date__gte=start_date, date__lte=end_date
        ).annotate(month=TruncMonth('date')).values('month').annotate(total=Sum('base_amount')).order_by('month')
        
        # Merge data into a map {date: {income: 0, expense: 0}}
        data_map = {}
        
        # Initialize map with all 12 months to ensure 0s for missing months
        # Iterate from start_date to today month by month
        curr = start_date
        while curr <= today:
            d = curr.replace(day=1)
            data_map[d] = {'income': 0, 'expense': 0}
            # Move to next month
            # Carefully handle month increment
            next_month = curr.month + 1
            next_year = curr.year
            if next_month > 12:
                next_month = 1
                next_year += 1
            curr = date(next_year, next_month, 1)

        # Fill with DB data
        # Fill with DB data
        for item in monthly_income:
            if item['month']:
                d = item['month']
                if isinstance(d, datetime):
                    d = d.date()
                d = d.replace(day=1)
                if d in data_map:
                    data_map[d]['income'] = float(item['total'])
                
        for item in monthly_expenses:
             if item['month']:
                d = item['month']
                if isinstance(d, datetime):
                    d = d.date()
                d = d.replace(day=1)
                if d in data_map:
                    data_map[d]['expense'] = float(item['total'])
                
        # Sort and prepare lists
        sorted_keys = sorted(data_map.keys())
        # Limit to last 12 months if while loop went over
        sorted_keys = sorted_keys[-12:]
        
        for k in sorted_keys:
            labels.append(date_format(k, 'M Y'))
            inc = data_map[k]['income']
            exp = data_map[k]['expense']
            income_data.append(inc)
            expense_data.append(exp)
            
            # Balance Rate = (Income - Expense) / Income * 100
            if inc > 0:
                rate = ((inc - exp) / inc) * 100
            else:
                rate = 0
            balance_rate_data.append(round(rate, 1))

        context['chart_labels'] = labels
        context['income_data'] = income_data
        context['expense_data'] = expense_data
        context['balance_rate_data'] = balance_rate_data
        
        # 2. Category Breakdown (Selected Year)
        category_stats = Expense.objects.filter(
            user=user, date__year=selected_year
        ).values('category').annotate(total=Sum('base_amount')).order_by('-total')
        
        cat_labels = [_(x['category']) for x in category_stats]
        cat_data = [float(x['total']) for x in category_stats]
        
        context['cat_labels'] = cat_labels
        context['cat_data'] = cat_data
        
        # 3. Key Metrics (YTD / Full Year depending on selection)
        if selected_year == today.year:
            # For current year, limit to today so future recurring entries don't skew YTD
            ytd_income_agg = Income.objects.filter(user=user, date__year=selected_year, date__lte=today).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            ytd_expense_agg = Expense.objects.filter(user=user, date__year=selected_year, date__lte=today).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        else:
            # For past years, show the full year's total
            ytd_income_agg = Income.objects.filter(user=user, date__year=selected_year).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            ytd_expense_agg = Expense.objects.filter(user=user, date__year=selected_year).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        
        context['total_income_ytd'] = ytd_income_agg
        context['total_expense_ytd'] = ytd_expense_agg
        context['total_balance_ytd'] = ytd_income_agg - ytd_expense_agg
        
        if ytd_income_agg > 0:
            context['avg_balance_rate'] = round(((ytd_income_agg - ytd_expense_agg) / ytd_income_agg) * 100, 1)
        else:
            context['avg_balance_rate'] = 0
            
        # ---------------------------------------------------------
        # 4. Sankey Data (Income -> Expenses/Savings) YTD
        # ---------------------------------------------------------
        sankey_data = []
        if ytd_income_agg > 0 or ytd_expense_agg > 0:
            top_cats = list(category_stats[:8])
            other_cats = list(category_stats[8:])
            
            for cat in top_cats:
                sankey_data.append([_('Income'), _(cat['category']), float(cat['total'])])
                
            if other_cats:
                other_total = sum(float(cat['total']) for cat in other_cats)
                if other_total > 0:
                    sankey_data.append([_('Income'), _('Other Expenses'), other_total])
                    
            savings = ytd_income_agg - ytd_expense_agg
            if savings > 0:
                sankey_data.append([_('Income'), _('Savings'), float(savings)])
            elif savings < 0:
                # If deficit, flow deficit into Income so Google Chart balances it as money source
                sankey_data.append([_('Deficit (Overspending)'), _('Income'), abs(float(savings))])
                
        context['sankey_data'] = sankey_data
            
        # ---------------------------------------------------------
        # 5. Cashflow Forecasting (Next 6 Months)
        # ---------------------------------------------------------
        
        # A. Calculate Historical Average (Last 3 completed months)
        # We need completed months to avoid partial data skewing
        avg_income = 0
        avg_expense = 0
        months_counted = 0
        
        # Iterate back 3 months
        for i in range(1, 4):
            # Calculate past month/year
            # e.g. If now is May, we want April, March, Feb
            past_date = today.replace(day=1) - timedelta(days=20*i) # rough jump back
            # normalize to 1st of that month
            past_date = past_date.replace(day=1)
            
            # Re-calculate cleanly
            # If today is 2023-05-15
            # i=1 -> 2023-04-01
            # i=2 -> 2023-03-01
            # i=3 -> 2023-02-01
            year = today.year
            month = today.month - i
            while month < 1:
                month += 12
                year -= 1
            
            p_income = Income.objects.filter(user=user, date__year=year, date__month=month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            p_expense = Expense.objects.filter(user=user, date__year=year, date__month=month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            
            avg_income += p_income
            avg_expense += p_expense
            months_counted += 1
            
        if months_counted > 0:
            avg_income /= months_counted
            avg_expense /= months_counted

        # ---------------------------------------------------------
        # 5. Financial Health Score & Insights
        # ---------------------------------------------------------
        balance_rate = context.get('avg_balance_rate', 0)
        health_score = 0
        health_label = ""
        health_color = ""
        insights = []
        
        # Currency symbol for formatting
        currency = user.profile.currency if hasattr(user, 'profile') else '₹'
        savings_ytd = float(ytd_income_agg) - float(ytd_expense_agg)
        income_f = float(ytd_income_agg)
        expense_f = float(ytd_expense_agg)
        
        # Top category context (used across insights)
        top_cat_name = cat_labels[0] if cat_data else None
        top_cat_pct = 0
        if top_cat_name and income_f > 0:
            top_cat_pct = (cat_data[0] / income_f) * 100
        
        # Score Logic with dynamic messages
        if balance_rate < 0:
            health_score = 10
            health_label = _("At Risk")
            health_color = "danger"
            overspend_pct = abs(balance_rate)
            msg = _("You spent %(currency)s%(expense)s against %(currency)s%(income)s income (%(pct)s%% overspend).") % {
                'currency': currency,
                'expense': f"{expense_f:,.0f}",
                'income': f"{income_f:,.0f}",
                'pct': f"{overspend_pct:.0f}",
            }
            if top_cat_name:
                msg += " " + _("Review '%(cat)s' which accounts for %(cat_pct)s%% of your spending.") % {
                    'cat': escape(top_cat_name),
                    'cat_pct': f"{top_cat_pct:.0f}",
                }
            insights.append(msg)
        elif balance_rate < 10:
            health_score = 40
            health_label = _("Caution")
            health_color = "warning"
            msg = _("You saved %(currency)s%(savings)s out of %(currency)s%(income)s (%(rate)s%%).") % {
                'currency': currency,
                'savings': f"{savings_ytd:,.0f}",
                'income': f"{income_f:,.0f}",
                'rate': f"{balance_rate:.0f}",
            }
            if top_cat_name and cat_data:
                potential_saving = cat_data[0] * 0.2
                msg += " " + _("Cutting '%(cat)s' by 20%% could save %(currency)s%(amount)s more.") % {
                    'cat': escape(top_cat_name),
                    'currency': currency,
                    'amount': f"{potential_saving:,.0f}",
                }
            insights.append(msg)
        elif balance_rate < 30:
            health_score = 70
            health_label = _("Stable")
            health_color = "info"
            gap_to_30 = (0.30 * income_f) - savings_ytd if income_f > 0 else 0
            msg = _("You saved %(currency)s%(savings)s out of %(currency)s%(income)s (%(rate)s%%).") % {
                'currency': currency,
                'savings': f"{savings_ytd:,.0f}",
                'income': f"{income_f:,.0f}",
                'rate': f"{balance_rate:.0f}",
            }
            if gap_to_30 > 0:
                msg += " " + _("Just %(currency)s%(gap)s more to hit the 30%% savings benchmark.") % {
                    'currency': currency,
                    'gap': f"{gap_to_30:,.0f}",
                }
            insights.append(msg)
        else:
            health_score = 95
            health_label = _("Wealth Builder")
            health_color = "success"
            insights.append(
                _("You saved %(currency)s%(savings)s out of %(currency)s%(income)s (%(rate)s%%). That's top-tier financial discipline.") % {
                    'currency': currency,
                    'savings': f"{savings_ytd:,.0f}",
                    'income': f"{income_f:,.0f}",
                    'rate': f"{balance_rate:.0f}",
                }
            )

        # Category Insight (high concentration warning)
        if cat_data and top_cat_name:
            if income_f > 0:
                try:
                    if top_cat_pct > 30:
                        safe_top_cat = escape(top_cat_name)
                        insights.append(
                            _("'%(cat)s' is consuming %(pct)s%% of your income (%(currency)s%(amount)s). Consider setting a budget limit.") % {
                                'cat': safe_top_cat,
                                'pct': f"{top_cat_pct:.0f}",
                                'currency': currency,
                                'amount': f"{cat_data[0]:,.0f}",
                            }
                        )
                except (ValueError, TypeError):
                    pass

        # Spending Trend Projection (uses 3-month avg calculated earlier)
        if avg_expense > 0 and expense_f > 0 and selected_year == today.year:
            avg_expense_f = float(avg_expense)
            # Compare current year monthly average to historical
            months_elapsed = today.month
            current_monthly_avg = expense_f / months_elapsed if months_elapsed > 0 else 0
            if current_monthly_avg > avg_expense_f * 1.15:  # 15% higher than historical
                pct_increase = ((current_monthly_avg - avg_expense_f) / avg_expense_f) * 100
                next_month_name = (today.replace(day=28) + timedelta(days=4)).strftime('%B')
                projected = current_monthly_avg
                insights.append(
                    _("Spending is trending %(pct)s%% above your 3-month average. %(month)s may reach %(currency)s%(projected)s at this pace.") % {
                        'pct': f"{pct_increase:.0f}",
                        'month': next_month_name,
                        'currency': currency,
                        'projected': f"{projected:,.0f}",
                    }
                )
        # ---------------------------------------------------------
        # 5b. Health Breakdown Panel (Expandable Metrics)
        # ---------------------------------------------------------
        health_breakdown = []
        
        # 1. Savings Rate
        savings_rate_val = round(balance_rate, 1)
        if savings_rate_val >= 20:
            sr_status, sr_icon = 'success', 'check-circle-fill'
        elif savings_rate_val > 0:
            sr_status, sr_icon = 'warning', 'exclamation-triangle-fill'
        else:
            sr_status, sr_icon = 'danger', 'x-circle-fill'
        health_breakdown.append({
            'label': _('Savings Rate'),
            'value': f"{savings_rate_val}%",
            'status': sr_status,
            'icon': sr_icon,
        })
        
        # 2. Expense Growth (current monthly avg vs 3-month historical avg)
        avg_expense_f = float(avg_expense) if avg_expense > 0 else 0
        months_elapsed = today.month if selected_year == today.year else 12
        current_monthly_avg = expense_f / months_elapsed if months_elapsed > 0 else 0
        
        if avg_expense_f > 0:
            expense_growth_pct = round(((current_monthly_avg - avg_expense_f) / avg_expense_f) * 100, 1)
        else:
            expense_growth_pct = 0
        
        eg_prefix = '+' if expense_growth_pct > 0 else ''
        if expense_growth_pct <= 0:
            eg_status, eg_icon = 'success', 'check-circle-fill'
        elif expense_growth_pct <= 15:
            eg_status, eg_icon = 'warning', 'exclamation-triangle-fill'
        else:
            eg_status, eg_icon = 'danger', 'x-circle-fill'
        health_breakdown.append({
            'label': _('Expense Growth'),
            'value': f"{eg_prefix}{expense_growth_pct}%",
            'status': eg_status,
            'icon': eg_icon,
        })
        
        # 3. Consistency (months with positive savings out of last 10)
        consistency_count = 0
        consistency_total = 10
        for i in range(1, consistency_total + 1):
            c_year = today.year
            c_month = today.month - i
            while c_month < 1:
                c_month += 12
                c_year -= 1
            c_inc = Income.objects.filter(user=user, date__year=c_year, date__month=c_month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            c_exp = Expense.objects.filter(user=user, date__year=c_year, date__month=c_month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            if c_inc > c_exp and c_inc > 0:
                consistency_count += 1
        
        if consistency_count >= 7:
            cs_status, cs_icon = 'success', 'check-circle-fill'
        elif consistency_count >= 4:
            cs_status, cs_icon = 'warning', 'exclamation-triangle-fill'
        else:
            cs_status, cs_icon = 'danger', 'x-circle-fill'
        health_breakdown.append({
            'label': _('Consistency'),
            'value': f"{consistency_count}/{consistency_total}",
            'status': cs_status,
            'icon': cs_icon,
        })
        
        # 4. Risk Buffer (months of runway = total savings / avg monthly expense)
        if avg_expense_f > 0 and savings_ytd > 0:
            risk_buffer_months = round(savings_ytd / avg_expense_f, 1)
        else:
            risk_buffer_months = 0
        
        if risk_buffer_months >= 6:
            rb_status, rb_icon = 'success', 'check-circle-fill'
        elif risk_buffer_months >= 3:
            rb_status, rb_icon = 'warning', 'exclamation-triangle-fill'
        else:
            rb_status, rb_icon = 'danger', 'x-circle-fill'
        health_breakdown.append({
            'label': _('Risk Buffer'),
            'value': _("%(months)s months") % {'months': f"{risk_buffer_months:.0f}"},
            'status': rb_status,
            'icon': rb_icon,
        })
        
        # Health Summary sentence (combine best + worst)
        status_order = {'success': 2, 'warning': 1, 'danger': 0}
        best = max(health_breakdown, key=lambda x: status_order[x['status']])
        worst = min(health_breakdown, key=lambda x: status_order[x['status']])
        
        summary_parts = []
        if best['status'] == 'success':
            summary_parts.append(_("Your %(label)s is excellent.") % {'label': best['label'].lower()})
        if worst['status'] != 'success' and worst != best:
            summary_parts.append(_("However, %(label)s needs attention (%(value)s).") % {'label': worst['label'].lower(), 'value': worst['value']})
        health_summary = ' '.join(str(p) for p in summary_parts) if summary_parts else ''

        context['health_score'] = health_score
        context['health_label'] = health_label
        context['health_color'] = health_color
        context['insights'] = insights
        context['health_breakdown'] = health_breakdown
        context['health_summary'] = health_summary
            


        # 4. Day-of-Week Spending (Selected Year)
        dow_stats = Expense.objects.filter(
            user=user, date__year=selected_year
        ).annotate(weekday=ExtractWeekDay('date')).values('weekday').annotate(
            total=Sum('base_amount')
        ).order_by('weekday')
        
        # ExtractWeekDay: 1 (Sun) to 7 (Sat)
        dow_labels = [_('Sun'), _('Mon'), _('Tue'), _('Wed'), _('Thu'), _('Fri'), _('Sat')]
        dow_data = [0] * 7
        for item in dow_stats:
            # Handle possible key variations depending on DB backend
            wd = item.get('weekday')
            if wd is not None:
                dow_data[wd - 1] = float(item['total'])
        
        context['dow_labels'] = dow_labels
        context['dow_data'] = dow_data

        # 5. Cumulative Burn-down (Current Month)
        # Only relevant for the current month/year
        this_month_labels = []
        this_month_spent = []
        this_month_budget_line = []
        this_month_projection = []
        
        if today.year == selected_year:
            # Current Month Cumulative
            days_in_month = calendar.monthrange(today.year, today.month)[1]
            daily_spending = Expense.objects.filter(
                user=user, date__year=today.year, date__month=today.month
            ).values('date__day').annotate(total=Sum('base_amount')).order_by('date__day')
            
            daily_map = {item['date__day']: float(item['total']) for item in daily_spending}
            
            total_monthly_limit = Category.objects.filter(user=user).aggregate(Sum('limit'))['limit__sum'] or 0
            total_monthly_limit = float(total_monthly_limit)
            
            cumulative = 0
            this_month_projection = [None] * days_in_month
            
            for day in range(1, days_in_month + 1):
                this_month_labels.append(str(day))
                
                # Only add actual spent data up to today
                if day <= today.day:
                    cumulative += daily_map.get(day, 0)
                    this_month_spent.append(round(cumulative, 2))
                else:
                    this_month_spent.append(None)
                
                # Ideal line (pro-rated budget)
                if total_monthly_limit > 0:
                    this_month_budget_line.append(round((total_monthly_limit / days_in_month) * day, 2))
                else:
                    this_month_budget_line.append(0)

            # Calculation for Projection (Burn Rate)
            if today.day > 0 and cumulative > 0:
                burn_rate = cumulative / today.day
                proj_cumulative = cumulative
                # Start projection from today
                this_month_projection[today.day - 1] = round(cumulative, 2)
                for day in range(today.day + 1, days_in_month + 1):
                    proj_cumulative += burn_rate
                    this_month_projection[day - 1] = round(proj_cumulative, 2)
        
        context['burn_down_labels'] = this_month_labels
        context['burn_down_spent'] = this_month_spent
        context['burn_down_budget'] = this_month_budget_line
        context['burn_down_projection'] = this_month_projection



        # 7. Recurring vs One-time (Current Month)
        recurring_sum = Expense.objects.filter(
            user=user, 
            date__year=today.year, 
            date__month=today.month,
            description__icontains='(Recurring)'
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
        
        total_sum = Expense.objects.filter(
            user=user, date__year=today.year, date__month=today.month
        ).aggregate(Sum('base_amount'))['base_amount__sum'] or 1 # Avoid div by zero
        
        rec_val = float(recurring_sum)
        one_time_val = float(total_sum) - rec_val
        
        context['recurring_split_labels'] = [_('Recurring'), _('One-time')]
        context['recurring_split_data'] = [rec_val, max(0, one_time_val)]
        # ---------------------------------------------------------
        # 5. Cashflow Forecasting (Next 6 Months)
        # ---------------------------------------------------------
        
        # A. Calculate Historical Average (Last 3 completed months)
        avg_income = 0
        avg_expense = 0
        months_counted = 0
        
        for i in range(1, 4):
            year = today.year
            month = today.month - i
            while month < 1:
                month += 12
                year -= 1
            
            p_income = Income.objects.filter(user=user, date__year=year, date__month=month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            p_expense = Expense.objects.filter(user=user, date__year=year, date__month=month).aggregate(Sum('base_amount'))['base_amount__sum'] or 0
            
            avg_income += p_income
            avg_expense += p_expense
            months_counted += 1
            
        if months_counted > 0:
            avg_income /= months_counted
            avg_expense /= months_counted

        # B. Future Monthly Projection (incorporating recurring rules)
        forecast_income = []
        forecast_expenses = []
        forecast_labels = []
        
        active_recurring = RecurringTransaction.objects.filter(user=user, is_active=True)
        
        # Calculate isolated monthly recurring to subtract from baseline
        monthly_rec_income_baseline = 0
        monthly_rec_expense_baseline = 0
        for r in active_recurring:
            if r.frequency == 'MONTHLY':
                if r.transaction_type == 'INCOME':
                    monthly_rec_income_baseline += float(r.amount)
                else:
                    monthly_rec_expense_baseline += float(r.amount)
        
        for i in range(1, 7):
            forecast_year = today.year
            forecast_month = today.month + i
            while forecast_month > 12:
                forecast_month -= 12
                forecast_year += 1
            
            month_date = date(forecast_year, forecast_month, 1)
            forecast_labels.append(date_format(month_date, 'M Y'))
            
            rec_income_for_month = 0
            rec_expense_for_month = 0
            
            for r in active_recurring:
                if r.frequency == 'MONTHLY':
                    if r.transaction_type == 'INCOME':
                        rec_income_for_month += float(r.amount)
                    elif r.transaction_type == 'EXPENSE':
                        rec_expense_for_month += float(r.amount)
                elif r.frequency == 'YEARLY':
                    # Only add if it happens in this specific month
                    if r.start_date.month == forecast_month:
                        if r.transaction_type == 'INCOME':
                            rec_income_for_month += float(r.amount)
                        elif r.transaction_type == 'EXPENSE':
                            rec_expense_for_month += float(r.amount)
            
            # Combine Historical Avg (minus recurring) + Specific Month's Recurring
            # We assume historical avg includes average recurring, so we substitute
            projected_inc = max(float(avg_income), monthly_rec_income_baseline) + (rec_income_for_month - monthly_rec_income_baseline)
            projected_exp = max(float(avg_expense), monthly_rec_expense_baseline) + (rec_expense_for_month - monthly_rec_expense_baseline)
            
            forecast_income.append(round(projected_inc, 0))
            forecast_expenses.append(round(projected_exp, 0))
            
        context['forecast_income'] = forecast_income
        context['forecast_expenses'] = forecast_expenses
        context['forecast_labels'] = forecast_labels

        return context

class BudgetDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/budget_dashboard.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = date.today()
        
        month_param = self.request.GET.get('month')
        year_param = self.request.GET.get('year')
        
        month = int(month_param) if month_param else today.month
        year = int(year_param) if year_param else today.year
        
        # Ensure context variables for filters are correct
        context['current_month'] = month
        context['current_year'] = year
        
        categories = Category.objects.filter(user=user)
        budget_data = []
        
        total_budget = 0
        categorized_spent = 0
        
        # Calculate total spending across ALL expenses for the month
        grand_total_spent = Expense.objects.filter(
            user=user,
            date__year=year,
            date__month=month
        ).aggregate(Total=Sum('base_amount'))['Total'] or 0

        for category in categories:
            spent = Expense.objects.filter(
                user=user,
                category=category.name,
                date__year=year,
                date__month=month
            ).aggregate(Total=Sum('base_amount'))['Total'] or 0
            
            percentage = (spent / category.limit * 100) if category.limit and category.limit > 0 else 0
            
            budget_data.append({
                'category': category,
                'spent': spent,
                'limit': category.limit,
                'percentage': min(percentage, 100),
                'actual_percentage': percentage,
                'remaining': (category.limit - spent) if category.limit and spent <= category.limit else 0,
                'over_budget': (spent - category.limit) if category.limit and spent > category.limit else 0
            })
            
            if category.limit:
                total_budget += category.limit
            categorized_spent += spent
            
        context.update({
            'budget_data': budget_data,
            'total_budget': total_budget,
            'total_spent': grand_total_spent,
            'total_remaining': (total_budget - grand_total_spent) if total_budget > grand_total_spent else 0,
            'over_budget_amount': (grand_total_spent - total_budget) if grand_total_spent > total_budget else 0,
            'total_percentage': min((grand_total_spent / total_budget * 100), 100) if total_budget else 0,
            'actual_total_percentage': (grand_total_spent / total_budget * 100) if total_budget else 0,
            'month_name': date(year, month, 1).strftime('%B'),
        })

        # MoM Calculation for Budget Dashboard
        if month == 1:
            prev_month = 12
            prev_year = year - 1
        else:
            prev_month = month - 1
            prev_year = year

        prev_spent = Expense.objects.filter(
            user=user,
            date__year=prev_year,
            date__month=prev_month
        ).aggregate(Total=Sum('base_amount'))['Total'] or 0

        if prev_spent > 0:
            context['spent_mom_pct'] = ((grand_total_spent - prev_spent) / prev_spent) * 100
            context['spent_mom_pct_abs'] = abs(context['spent_mom_pct'])
        else:
            context['spent_mom_pct'] = None
            context['spent_mom_pct_abs'] = None

        context.update({
            'current_month': month,
            'current_year': year,
            'months': [(i, calendar.month_name[i]) for i in range(1, 13)],
            'years': range(today.year - 2, today.year + 2),
        })
        return context

class YearInReviewView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/year_in_review.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        year = int(self.request.GET.get('year', date.today().year))
        context['review_data'] = generate_year_in_review_data(self.request.user, year)
        return context

    def dispatch(self, request, *args, **kwargs):
        from django.contrib import messages
        from django.shortcuts import redirect
        if not request.user.profile.is_plus:
            messages.info(request, "Year in Review is a Premium feature. Upgrade to Plus or Pro to unlock your personalized financial story!")
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)
