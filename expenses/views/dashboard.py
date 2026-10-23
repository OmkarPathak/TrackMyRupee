import calendar
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import ExtractWeekDay, TruncDay, TruncMonth
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import escape, format_html, format_html_join, mark_safe
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from ..account_types import investment_codes, is_liability
from ..filters import DASHBOARD_FILTERS
from ..ledger_read_service import LedgerReadService
from ..models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    LoanRepayment,
    Notification,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
    UserProfile,
)
from ..recurring_utils import (
    calculate_recurring_equivalents,
)
from finance_tracker.plans import get_limit

from ..budgets import (
    BUDGET_WARNING_PERCENT,
    budget_status,
    capital_report_name,
    normalize_category,
)
from ..savings import (
    calculate_savings,
    monthly_parts,
    period_totals,
    savings_for_period,
    savings_from_querysets,
    totals_by_period,
)
from ..services import FinancialService, LoanService, SalaryAnalysisService
from ..templatetags.digit_filters import compact_amount
from ..utils import (
    format_indian_number,
    generate_year_in_review_data,
    get_exchange_rate,
)
from ..cache_utils import suppress_dashboard_cache_invalidation
from ..insights import shadow as insights_shadow
from .mixins import HtmxPartialTemplateMixin, process_user_recurring_transactions
from .utils import parse_month_year


@login_required
def home_view(request):
    """
    Dashboard view with filters and multiple charts.
    """
    # --- ZERO-QUERY WARM LOAD ---
    # 95%+ of dashboard opens (login/PWA) hit this view with no filter params. Cache the
    # fully-built context for 4 hours so repeat loads skip every query below entirely.
    # Cache is invalidated immediately on transaction mutations via signals (see signals.py).
    is_testing = getattr(settings, 'TESTING', False) or 'test' in sys.argv
    time_period_param = request.GET.get('time_period')
    is_default_filter_view = (
        (not time_period_param or time_period_param == 'this_month')
        and not request.GET.get('year')
        and not request.GET.get('month')
        and not request.GET.get('category')
        and not request.GET.get('payment_method')
        and not request.GET.get('account')
        and not request.GET.get('start_date')
        and not request.GET.get('end_date')
    )
    home_cache_key = f'home_default_data_{request.user.id}' if (is_default_filter_view and not is_testing) else None
    cached_context = cache.get(home_cache_key) if home_cache_key else None
    if cached_context is not None:
        user_salary_date = getattr(getattr(request.user, 'profile', None), 'salary_date', 1) or 1
        if cached_context.get('salary_date', 1) != user_salary_date:
            cached_context = None
            cache.delete(home_cache_key)

    # Compute once — reused both for the onboarding redirect AND the year-in-review
    # banner near the bottom of this view. On a cache hit it is derived from the cached
    # context (any Expense/Income write invalidates that entry), saving an EXISTS pair
    # (~2 DB round trips) on every warm dashboard load.
    if cached_context is not None and 'is_new_user' in cached_context:
        has_any_data = not cached_context['is_new_user']
    else:
        has_any_data = (
            Expense.objects.filter(user=request.user).exists()
            or Income.objects.filter(user=request.user).exists()
        )

    # Defensive check: Redirect to onboarding if user has NO data AND hasn't finished the flow
    try:
        if not request.user.profile.has_seen_tutorial and not has_any_data:
            return redirect('onboarding')
    except UserProfile.DoesNotExist:
        # Ensure profile exists, then redirect
        UserProfile.objects.get_or_create(user=request.user)
        return redirect('onboarding')

    if cached_context is not None:
        context = dict(cached_context)
        # Refresh the handful of fields that are request/profile-specific and must never be stale.
        context['is_net_worth_locked'] = not request.user.profile.has_net_worth_access
        context['is_ai_locked'] = not request.user.profile.has_ai_access
        context['show_tutorial'] = not request.user.profile.has_seen_tutorial or request.GET.get('tour') == 'true'
        context['is_new_user'] = not has_any_data
        context['filter_config'] = DASHBOARD_FILTERS
        if 'applied_state' not in context:
            context['applied_state'] = {
                'search': '',
                'time_period': 'this_month',
                'start_date': '',
                'end_date': '',
                'sort': '',
                'filters': {},
            }
        return render(request, 'home.html', context)

    # Process recurring transactions on cache miss / custom filter
    with suppress_dashboard_cache_invalidation(invalidate_on_exit=False):
        process_user_recurring_transactions(request.user, max_catchup=2)

    # --- NET WORTH TREND (Last 6 Months) ---
    net_worth_history = FinancialService.get_monthly_history(request.user, 6)
    
    net_worth_labels = [date_format(m['month'], 'M Y') for m in net_worth_history]
    net_worth_data = [m['savings'] for m in net_worth_history] # Using savings as a proxy for monthly cash flow trend
    # If the user wants actual cumulative net worth, we'd need a starting balance. 
    # But based on original code (lines 1384-1400), it was calculating monthly savings.
    
    # Global currency symbol for insights/metrics
    currency_symbol = request.user.profile.currency if hasattr(request.user, 'profile') else '₹'
    
    def format_currency(amount):
        if str(currency_symbol).upper() in ['INR', '₹']:
            return f"{currency_symbol}{format_indian_number(amount)}"
        return f"{currency_symbol}{int(amount):,}"

    # Helper: sum transfer amounts converted to user's base currency
    def sum_transfers_base(qs):
        return qs.aggregate(total=Sum('converted_amount'))['total'] or Decimal('0.00')

    # Base QuerySet - All user expenses
    expenses = Expense.objects.filter(user=request.user).select_related('account', 'category_fk').order_by('-date')
    
    # Wealth Growth (Investments) - Transfers to Investment accounts
    investments = Transfer.objects.filter(user=request.user, to_account__account_type__in=list(investment_codes())).select_related('from_account', 'to_account')
    
    # Logic for EOM projection
    now = timezone.localtime(timezone.now())
    num_days_in_month = calendar.monthrange(now.year, now.month)[1]
    days_passed = now.day
    
    # Filter Logic
    time_period = request.GET.get('time_period')
    start_date = request.GET.get('start_date') or ''
    end_date = request.GET.get('end_date') or ''
    start_date_str = start_date
    end_date_str = end_date
    selected_years = request.GET.getlist('year')
    selected_months = request.GET.getlist('month')
    selected_categories = [c.strip() for c in request.GET.getlist('category') if c and c.strip()]
    selected_payment_methods = [pm.strip() for pm in request.GET.getlist('payment_method') if pm and pm.strip()]
    selected_accounts = [a.strip() for a in request.GET.getlist('account') if a and a.strip()]

    start_date_obj = None
    end_date_obj = None
    if start_date_str:
        try:
            start_date_obj = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    if end_date_str:
        try:
            end_date_obj = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            pass

    # Remove empty strings from lists
    selected_years = [y for y in selected_years if y]
    selected_months = [m for m in selected_months if m]

    salary_cycle_active = False
    salary_cycle_start = None
    salary_cycle_end = None
    trend_is_daily = False
    effective_start_date = None
    effective_end_date = None
    today = timezone.localtime().date()
    display_year = None
    display_month = None

    from ..periods import calculate_budget_period_factor, get_cycle_context, resolve_period

    if not time_period:
        if start_date_obj or end_date_obj:
            time_period = 'custom'
        elif selected_years or selected_months:
            time_period = 'custom'
        else:
            time_period = 'this_month'

    if time_period == 'custom' and not (start_date_obj or end_date_obj) and len(selected_months) == 1 and len(selected_years) == 1:
        # Keep existing convention for an explicit single year plus single month selection:
        # "the cycle that starts in the named month", built from that month's last day.
        try:
            sel_month = int(selected_months[0])
            sel_year = int(selected_years[0])
            target_date = date(sel_year, sel_month, calendar.monthrange(sel_year, sel_month)[1])
            salary_cycle_start, salary_cycle_end = SalaryAnalysisService.get_salary_cycle_dates(request.user, target_date)
            user_profile = getattr(request.user, 'profile', None)
            salary_cycle_active = bool(user_profile and user_profile.salary_date and user_profile.salary_date != 1)
            effective_start_date = salary_cycle_start
            effective_end_date = salary_cycle_end
        except (ValueError, IndexError):
            salary_cycle_active = False
            effective_start_date = date(sel_year, sel_month, 1)
            effective_end_date = date(sel_year, sel_month, calendar.monthrange(sel_year, sel_month)[1])
        trend_title = _("Daily Expenses for Salary Cycle") if salary_cycle_active else (_("Daily Expenses for %(month)s/%(year)s") % {'month': selected_months[0], 'year': selected_years[0]})
        trend_is_daily = True
        display_year = str(sel_year)
        display_month = _(calendar.month_name[sel_month])
    else:
        resolved_period = resolve_period(
            user=request.user,
            time_period=time_period,
            start_date=start_date_str,
            end_date=end_date_str,
            today=today,
        )
        time_period = resolved_period.key
        effective_start_date = resolved_period.start
        effective_end_date = resolved_period.end

        if resolved_period.key in ('this_month', 'calendar_month'):
            salary_cycle_active = resolved_period.is_cycle
            if salary_cycle_active:
                salary_cycle_start = resolved_period.start
                salary_cycle_end = resolved_period.end
            trend_title = _("Daily Expenses for Salary Cycle") if salary_cycle_active else _("Daily Expenses for This Month")
            trend_is_daily = True
            selected_years = [str(now.year)]
            selected_months = [str(now.month)]
            display_year = str(now.year)
            display_month = _(calendar.month_name[now.month])

        elif resolved_period.key in ('last_month', 'calendar_last_month'):
            salary_cycle_active = resolved_period.is_cycle
            if salary_cycle_active:
                salary_cycle_start = resolved_period.start
                salary_cycle_end = resolved_period.end
            first_day_this_month = today.replace(day=1)
            last_day_last_month = first_day_this_month - timedelta(days=1)
            trend_title = _("Daily Expenses for Salary Cycle") if salary_cycle_active else (_("Daily Expenses for %(month)s/%(year)s") % {'month': last_day_last_month.month, 'year': last_day_last_month.year})
            trend_is_daily = True
            selected_years = [str(last_day_last_month.year)]
            selected_months = [str(last_day_last_month.month)]
            display_year = str(last_day_last_month.year)
            display_month = _(calendar.month_name[last_day_last_month.month])

        elif resolved_period.key == 'last_3_months':
            trend_title = _("Expenses Trend (Last 3 Months)")
            trend_is_daily = False
            display_year = str(today.year)
            display_month = None

        elif resolved_period.key == 'last_6_months':
            trend_title = _("Expenses Trend (Last 6 Months)")
            trend_is_daily = False
            display_year = str(today.year)
            display_month = None

        elif resolved_period.key == 'this_year':
            trend_title = _("Expenses Trend (This Year)")
            trend_is_daily = False
            display_year = str(today.year)
            display_month = None

        elif resolved_period.key == 'all':
            trend_title = _("All-Time Expenses Trend")
            trend_is_daily = False
            display_year = None
            display_month = None

        elif resolved_period.key == 'custom':
            if start_date_obj or end_date_obj:
                trend_title = _("Expenses Trend (Custom Range)")
                if start_date_obj and end_date_obj:
                    trend_is_daily = (end_date_obj - start_date_obj).days <= 60
                else:
                    trend_is_daily = True
                display_year = None
                display_month = None
            else:
                effective_start_date = None
                effective_end_date = None
                trend_title = _("Monthly Expenses Trend")
                trend_is_daily = False
                display_year = selected_years[0] if len(selected_years) == 1 else None
                display_month = _(calendar.month_name[int(selected_months[0])]) if len(selected_months) == 1 else None
        else:
            trend_title = _("Expenses Trend")
            trend_is_daily = False
            display_year = str(today.year)
            display_month = None

    if effective_start_date:
        expenses = expenses.filter(date__gte=effective_start_date)
    if effective_end_date:
        expenses = expenses.filter(date__lte=effective_end_date)
    if not (effective_start_date or effective_end_date):
        if selected_years:
            expenses = expenses.filter(date__year__in=selected_years)
        if selected_months:
            expenses = expenses.filter(date__month__in=selected_months)

    if selected_categories:
        expenses = expenses.filter(category__in=selected_categories)
    if selected_payment_methods:
        expenses = expenses.filter(payment_method__in=selected_payment_methods)
    if selected_accounts:
        expenses = expenses.filter(account_id__in=selected_accounts)

    applied_filters = {}
    if selected_categories:
        applied_filters['category'] = selected_categories
    if selected_payment_methods:
        applied_filters['payment_method'] = selected_payment_methods
    if selected_accounts:
        applied_filters['account'] = selected_accounts

    cycle_ctx = get_cycle_context(user=request.user, today=today)
    applied_state = {
        'search': '',
        'time_period': time_period,
        'start_date': start_date_str if time_period == 'custom' else (effective_start_date.strftime('%Y-%m-%d') if time_period == 'custom' and effective_start_date else ''),
        'end_date': end_date_str if time_period == 'custom' else (effective_end_date.strftime('%Y-%m-%d') if time_period == 'custom' and effective_end_date else ''),
        'sort': '',
        'filters': applied_filters,
        'cycle_active': cycle_ctx['cycle_active'],
        'salary_date': cycle_ctx['salary_date'],
        'cycle_range': cycle_ctx['cycle_range'],
        'prev_cycle_range': cycle_ctx['prev_cycle_range'],
        'calendar_month_range': cycle_ctx['calendar_month_range'],
        'last_month_range': cycle_ctx['last_month_range'],
        'last_3_months_range': cycle_ctx['last_3_months_range'],
        'this_year_range': cycle_ctx['this_year_range'],
        'cycle_day': cycle_ctx['cycle_day'],
        'cycle_total_days': cycle_ctx['cycle_total_days'],
        'cycle_pct': cycle_ctx['cycle_pct'],
    }
        
    # Income Logic (Mirroring Expense Filters)
    incomes = Income.objects.filter(user=request.user).select_related('account', 'source_fk')
    if selected_accounts:
        incomes = incomes.filter(account_id__in=selected_accounts)
        investments = investments.filter(Q(from_account_id__in=selected_accounts) | Q(to_account_id__in=selected_accounts))

    if effective_start_date or effective_end_date:
        if effective_start_date:
            incomes = incomes.filter(date__gte=effective_start_date)
            investments = investments.filter(date__gte=effective_start_date)
        if effective_end_date:
            incomes = incomes.filter(date__lte=effective_end_date)
            investments = investments.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            incomes = incomes.filter(date__year__in=selected_years)
            investments = investments.filter(date__year__in=selected_years)
        if selected_months:
            incomes = incomes.filter(date__month__in=selected_months)
            investments = investments.filter(date__month__in=selected_months)
    
    total_investments = sum_transfers_base(investments)
    
    # Fetch loan repayments for the selected period (moved up to fix UnboundLocalError)
    loan_repayments_selected = LoanRepayment.objects.filter(loan__user=request.user).select_related('loan')
    if selected_accounts:
        loan_repayments_selected = loan_repayments_selected.filter(from_account_id__in=selected_accounts)
    if effective_start_date or effective_end_date:
        if effective_start_date:
            loan_repayments_selected = loan_repayments_selected.filter(date__gte=effective_start_date)
        if effective_end_date:
            loan_repayments_selected = loan_repayments_selected.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            loan_repayments_selected = loan_repayments_selected.filter(date__year__in=selected_years)
        if selected_months:
            loan_repayments_selected = loan_repayments_selected.filter(date__month__in=selected_months)
    
    # Income (with its cashback/refund part) and the loan repayments come back in one query (expenses/savings.py);
    # the repayment count is reused for transaction_count below.
    _period = period_totals(incomes, Expense.objects.none(), loan_repayments_selected, CapitalEvent.objects.none())
    total_income, total_cb_rf_income = _period['income'], _period['cashback_refund']
    loan_stats = {
        'total_interest': _period['interest'] or None,
        'total_emi': _period['emi'] or None,
        'repayment_count': _period['repayments'],
    }
    total_loan_interest = loan_stats['total_interest'] or 0
    total_loan_emi = loan_stats['total_emi'] or 0
    total_loan_principal = total_loan_emi - total_loan_interest

    all_dates = Expense.objects.filter(user=request.user).dates('date', 'year', order='DESC')
    years = sorted(list(set([d.year for d in all_dates] + [now.year])), reverse=True)

    # Optimization: Pre-fetch all categories for the user to avoid N+1 queries in the loop
    user_categories = {c.name: c for c in Category.objects.filter(user=request.user)}

    # --- PERFORMANCE OPTIMIZATION: BATCH MONTHLY TOTALS ---
    # Fetch 2 years of monthly totals in one go to avoid multiple N+1 aggregations in loops
    # Using Django Cache to prevent hitting the DB on every single dashboard load.
    monthly_summary_cache_key = f'monthly_summary_map_{request.user.id}' if not is_testing else None
    monthly_summary_map = cache.get(monthly_summary_cache_key) if monthly_summary_cache_key else None

    if monthly_summary_map is None:
        hist_start = (timezone.now().replace(day=1) - timedelta(days=730)).date()
        # 'expense' is the combined total (expense + loan interest + capital events) used by
        # the trend/average code below. 'expense_base' and 'loan_interest' are kept separate
        # too, so YTD / historical-average figures elsewhere can be derived without extra queries.
        # All four tables come back in one query (see expenses/savings.py).
        monthly_summary_map = {} # (year, month) -> {'income': 0, 'expense': 0, 'expense_base': 0, 'loan_interest': 0}
        for key, part in monthly_parts(request.user, hist_start).items():
            expense_base, interest = float(part['expenses']), float(part['interest'])
            monthly_summary_map[key] = {
                'income': float(part['income']),
                'expense': expense_base + interest + float(part['capital']),
                'expense_base': expense_base,
                'loan_interest': interest,
            }

        if monthly_summary_cache_key:
            cache.set(monthly_summary_cache_key, monthly_summary_map, 300) # Cache for 5 minutes


    # 1. Category Chart Data (Distribution) & Summary Table
    # We need to fetch raw values and merge them in Python to handle whitespace duplicates
    # One grouped query feeds the category chart, the payment-method split and the totals below.
    expense_groups = list(expenses.order_by().values('category', 'payment_method')
                          .annotate(total=Sum('base_amount'), n=Count('id')))
    raw_category_data = expense_groups
    
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

    # Fetch capital events in the viewed period with exclude_from_budget=False
    budget_events = CapitalEvent.objects.filter(user=request.user, exclude_from_budget=False)
    if selected_accounts:
        budget_events = budget_events.filter(account_id__in=selected_accounts)
    if effective_start_date or effective_end_date:
        if effective_start_date:
            budget_events = budget_events.filter(date__gte=effective_start_date)
        if effective_end_date:
            budget_events = budget_events.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            budget_events = budget_events.filter(date__year__in=selected_years)
        if selected_months:
            budget_events = budget_events.filter(date__month__in=selected_months)
            
    # Group by subtype in DB (one query returning N_distinct_subtypes rows) instead of
    # fetching every CapitalEvent row into Python to sum them.
    _subtype_display = dict(CapitalEvent._meta.get_field('subtype').flatchoices)
    # Captured here so the net-worth-change block below can reuse this period's total
    # instead of firing a duplicate CapitalEvent query with the same filters.
    period_capital_events_budget_total = 0.0
    for item in budget_events.values('category', 'subtype').annotate(total=Sum('base_amount')):
        cat_name = item['category'] or _subtype_display.get(item['subtype'], item['subtype'])
        matched_cat = None
        for uc_name in user_categories.keys():
            if uc_name.lower() == cat_name.lower():
                matched_cat = uc_name
                break
        if matched_cat:
            cat_name = matched_cat
        merged_category_map[cat_name] = merged_category_map.get(cat_name, 0.0) + float(item['total'])
        period_capital_events_budget_total += float(item['total'])
    
    # Add Loan Interest to breakdown
    if total_loan_interest > 0:
        cat_name = str(_("Loan Interest"))
        merged_category_map[cat_name] = merged_category_map.get(cat_name, 0.0) + float(total_loan_interest)
            
    # Convert back to list of dicts for template/charts, sorted by total
    # This replaces the DB-ordered queryset with a sorted list
    category_data = [
        {'category': cat, 'total': amt} 
        for cat, amt in merged_category_map.items()
    ]
    category_data.sort(key=lambda x: x['total'], reverse=True)

    # Determine the budget scaling factor for the filtered period, and which months (and what share
    # of each) the period covers so limits can be looked up per month (budget_resolver.period_limits)
    from ..budget_resolver import month_start as _month_start, month_weights_for_range
    budget_month_weights = None
    from ..budget_cycle import current_budget_month, key_for_range, previous_month, salary_day_of
    _salary_day = salary_day_of(request.user)
    _exact_key = (key_for_range(_salary_day, effective_start_date, effective_end_date)
                  if effective_start_date and effective_end_date else None)
    if time_period == 'this_month':
        # the budget month the person is in (the salary cycle when they have a salary day)
        period_budget_factor = 1.0
        budget_month_weights = [(current_budget_month(request.user, today), 1.0)]
    elif time_period == 'last_month':
        period_budget_factor = 1.0
        budget_month_weights = [(previous_month(current_budget_month(request.user, today)), 1.0)]
    elif _exact_key is not None:
        period_budget_factor = 1.0          # exactly one budget month's days: that month's limit, whole
        budget_month_weights = [(_exact_key, 1.0)]
    elif effective_start_date and effective_end_date:
        period_budget_factor = calculate_budget_period_factor(effective_start_date, effective_end_date)
        budget_month_weights = month_weights_for_range(effective_start_date, effective_end_date)
    elif time_period == 'all':
        earliest_expense_date = expenses.order_by('date').values_list('date', flat=True).first()
        period_start = earliest_expense_date or today
        period_budget_factor = calculate_budget_period_factor(period_start, today)
        budget_month_weights = month_weights_for_range(period_start, today)
        if period_budget_factor < 1.0:
            period_budget_factor = 1.0
            budget_month_weights = [(_month_start(today), 1.0)]
    elif time_period == 'custom' and not (effective_start_date or effective_end_date):
        if selected_years and selected_months:
            period_budget_factor = float(len(selected_years) * len(selected_months))
            budget_month_weights = [(date(int(y), int(m), 1), 1.0) for y in selected_years for m in selected_months]
        elif selected_years:
            period_budget_factor = float(len(selected_years) * 12.0)
            budget_month_weights = [(date(int(y), m, 1), 1.0) for y in selected_years for m in range(1, 13)]
        elif selected_months:
            period_budget_factor = float(len(selected_months))
            budget_month_weights = [(date(today.year, int(m), 1), 1.0) for m in selected_months]
        else:
            period_budget_factor = 1.0
    elif effective_start_date and not effective_end_date:
        period_budget_factor = calculate_budget_period_factor(effective_start_date, today)
        budget_month_weights = month_weights_for_range(effective_start_date, today)
    elif effective_end_date and not effective_start_date:
        earliest_expense_date = expenses.order_by('date').values_list('date', flat=True).first()
        period_start = earliest_expense_date or effective_end_date
        period_budget_factor = calculate_budget_period_factor(period_start, effective_end_date)
        budget_month_weights = month_weights_for_range(period_start, effective_end_date)
    else:
        period_budget_factor = 1.0

    if period_budget_factor <= 0:
        period_budget_factor = 1.0
    if not budget_month_weights:
        budget_month_weights = [(_month_start(today), 1.0)]

    # Limits come from the versioned budget tables. Only categories the plan allows are budgeted.
    from ..budget_resolver import period_limits, plan_allowed_categories
    budget_categories = plan_allowed_categories(request.user, user_categories.values())
    period_limit_map, monthly_limit_map = period_limits(
        request.user, budget_month_weights, period_budget_factor, budget_categories)

    # Compute limits and usage per category for chart display
    category_limits = []

    is_current_month_view = (len(selected_months) == 1 and str(now.month) in selected_months and str(now.year) in selected_years)

    for item in category_data:
        cat_name = item['category']
        cat_obj = user_categories.get(cat_name)
        
        cat_key = normalize_category(cat_name)
        raw_limit = float(monthly_limit_map[cat_key]) if monthly_limit_map.get(cat_key) is not None else None
        limit = round(period_limit_map[cat_key], 2) if period_limit_map.get(cat_key) is not None else None
        
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
            'monthly_limit': raw_limit,
            'used_percent': used_percent,
            'status': budget_status(item['total'], limit),   # same rule as the Budget page
            'projected_total': projected_total,
            'projected_percent': projected_percent,
        })
    
    seen_budget_names = set()
    total_monthly_budget = 0.0
    total_period_budget_raw = 0.0
    for c in budget_categories:
        key = normalize_category(c.name)
        if key in seen_budget_names:
            continue
        seen_budget_names.add(key)
        if monthly_limit_map.get(key) is not None:
            total_monthly_budget += float(monthly_limit_map[key])
        if period_limit_map.get(key) is not None:
            total_period_budget_raw += period_limit_map[key]
    total_period_budget = round(total_period_budget_raw, 2)
    
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
    if trend_is_daily:
        trend_qs = expenses.annotate(period=TruncDay('date'))
        date_fmt = '%d %b'
    else:
        trend_qs = expenses.annotate(period=TruncMonth('date'))
        date_fmt = '%b %Y'

    # Aggregate by Period for Total Spend
    total_data = trend_qs.values('period').annotate(total=Sum('base_amount')).order_by('period')
    
    # Process into Chart.js Datasets
    periods = [item['period'] for item in total_data]
    trend_labels = [p.strftime(date_fmt) for p in periods]
    trend_iso_dates = [p.strftime('%Y-%m-%d') for p in periods]
    
    trend_data = [float(item['total']) for item in total_data]

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

    # Include capital events that are NOT excluded from averages (i.e. exclude_from_averages=False)
    included_events_qs = CapitalEvent.objects.filter(user=request.user, exclude_from_averages=False)
    if selected_accounts:
        included_events_qs = included_events_qs.filter(account_id__in=selected_accounts)
    if effective_start_date or effective_end_date:
        if effective_start_date:
            included_events_qs = included_events_qs.filter(date__gte=effective_start_date)
        if effective_end_date:
            included_events_qs = included_events_qs.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            included_events_qs = included_events_qs.filter(date__year__in=selected_years)
        if selected_months:
            included_events_qs = included_events_qs.filter(date__month__in=selected_months)

    # --- NEW: Income vs Expenses Trend Data ---
    trunc_func = TruncDay if trend_is_daily else TruncMonth
        
    # The expense trend is the one already fetched for the Total Spent chart (same period, same filters). Income,
    # loan interest and counted capital events for the same periods come back in one query. The loan repayments
    # are filtered exactly like loan_repayments_selected above.
    exp_trend_base = total_data
    _trend = totals_by_period(
        trunc_func,
        income=(incomes, Sum('base_amount')),
        interest=(loan_repayments_selected, Sum(F('interest_portion') * F('exchange_rate'))),
        capital=(included_events_qs, Sum('base_amount')),
    )

    # Merge periods
    def normalize_period(p):
        if hasattr(p, 'date'):
            p = p.date()
        if trunc_func == TruncMonth:
            return p.replace(day=1)
        return p

    invest_map = {}
    for t in investments:
        p_key = normalize_period(t.date)
        if t.from_account and t.from_account.currency != currency_symbol:
            rate = get_exchange_rate(t.from_account.currency, currency_symbol)
            amt = float((t.amount * rate).quantize(Decimal('0.01')))
        else:
            amt = float(t.amount)
        invest_map[p_key] = invest_map.get(p_key, 0.0) + amt

    inc_map = {normalize_period(p): float(v) for p, v in _trend['income'].items()}
    inc_periods = set(inc_map)
    exp_periods_base = set(normalize_period(e['period']) for e in exp_trend_base)
    loan_map = {normalize_period(p): {'interest': float(v or 0)} for p, v in _trend['interest'].items()}
    loan_periods = set(loan_map)
    invest_periods = set(invest_map.keys())
    cap_map = {normalize_period(p): float(v or 0) for p, v in _trend['capital'].items()}
    all_periods_sorted = sorted(list(inc_periods.union(exp_periods_base).union(loan_periods).union(invest_periods).union(cap_map.keys())))
    
    ie_labels = [p.strftime(date_fmt) for p in all_periods_sorted]
    
    # Optimization: Use dict lookup instead of filter inside loop
    exp_map = {normalize_period(e['period']): float(e['total']) for e in exp_trend_base}
    # Add loan interest to exp_map and calculate savings
    ie_income_data = []
    ie_expense_data = []
    ie_savings_data = []
    ie_invested_data = []
    
    for p in all_periods_sorted:
        inc_val = inc_map.get(p, 0.0)
        # Same definition as the savings card (expenses/savings.py): expenses + loan interest +
        # capital events counted in averages. Loan principal is repaying debt, not spending.
        exp_val = exp_map.get(p, 0.0) + loan_map.get(p, {}).get('interest', 0.0) + cap_map.get(p, 0.0)
        inv_val = invest_map.get(p, 0.0)

        ie_income_data.append(inc_val)
        ie_expense_data.append(exp_val)
        ie_savings_data.append(inc_val - exp_val)
        ie_invested_data.append(inv_val)

    # --- NEW: Payment Method Distribution ---
    raw_payment_data = expense_groups
    payment_map = {}
    for item in raw_payment_data:
        pm_name = item['payment_method'] or 'Unknown'
        payment_map[pm_name] = payment_map.get(pm_name, 0.0) + float(item['total'])
    
    # Sort by total desc
    sorted_payment_items = sorted(payment_map.items(), key=lambda x: x[1], reverse=True)
    payment_labels = [item[0] for item in sorted_payment_items]
    payment_data = [item[1] for item in sorted_payment_items]


    # 4. Summary Stats
    _expense_agg = {'total': sum((g['total'] or 0 for g in expense_groups), Decimal('0')) if expense_groups else None,
                    'count': sum(g['n'] for g in expense_groups)}
    total_expenses_base = _expense_agg['total'] or 0
    
    # Single aggregate replaces both the Python-loop sum and the separate .count() call
    _cap_agg = included_events_qs.aggregate(total=Sum('base_amount'), count=Count('id'))
    included_capital_events_total = _cap_agg['total'] or Decimal('0.00')
    _included_events_count = _cap_agg['count'] or 0

    total_expenses = total_expenses_base + total_loan_interest + included_capital_events_total
    transaction_count = _expense_agg['count'] + loan_stats['repayment_count'] + _included_events_count
    
    # Savings and the savings rate are defined once, in expenses/savings.py
    # (principal repayment is not spending; only interest is).
    current_savings = calculate_savings(
        income=total_income,
        cashback_refund=total_cb_rf_income,
        operating_expenses=total_expenses_base,
        loan_interest=total_loan_interest,
        capital_events=included_capital_events_total,
    )
    savings = current_savings.savings
    savings_rate_denominator = current_savings.denominator
    
    # Calculate MoM Changes ONLY if exactly one year and one month are selected
    prev_month_data = None
    comparison_label = _("vs last month")
    prev_year = None
    prev_month = None
    prev_window = None
    if len(selected_years) == 1 and len(selected_months) == 1:
        try:
            sel_year = int(selected_years[0])
            sel_month = int(selected_months[0])

            if salary_cycle_active and salary_cycle_start and salary_cycle_end:
                comparison_label = _("vs previous cycle")
                prev_cycle_end = salary_cycle_start - timedelta(days=1)
                prev_cycle_start, prev_cycle_end = SalaryAnalysisService.get_salary_cycle_dates(request.user, prev_cycle_end)
                prev_window = {'date__gte': prev_cycle_start, 'date__lte': prev_cycle_end}
            else:
                # Calculate previous month and year
                if sel_month == 1:
                    prev_month = 12
                    prev_year = sel_year - 1
                else:
                    prev_month = sel_month - 1
                    prev_year = sel_year
                prev_window = {'date__year': prev_year, 'date__month': prev_month}

            # The previous period is read with the same category / payment / account filters as
            # the current one, otherwise the comparison would set a filtered figure against an
            # unfiltered one.
            prev_expenses_all = Expense.objects.filter(user=request.user, **prev_window)
            if selected_categories:
                prev_expenses_all = prev_expenses_all.filter(category__in=selected_categories)
            if selected_payment_methods:
                prev_expenses_all = prev_expenses_all.filter(payment_method__in=selected_payment_methods)
            prev_incomes_qs = Income.objects.filter(user=request.user, **prev_window)
            prev_repayments_qs = LoanRepayment.objects.filter(loan__user=request.user, **prev_window)
            prev_capital_qs = CapitalEvent.objects.filter(user=request.user, **prev_window)
            prev_investments_qs = Transfer.objects.filter(
                user=request.user, to_account__account_type__in=list(investment_codes()), **prev_window
            )
            if selected_accounts:
                prev_expenses_all = prev_expenses_all.filter(account_id__in=selected_accounts)
                prev_incomes_qs = prev_incomes_qs.filter(account_id__in=selected_accounts)
                prev_repayments_qs = prev_repayments_qs.filter(from_account_id__in=selected_accounts)
                prev_capital_qs = prev_capital_qs.filter(account_id__in=selected_accounts)
                prev_investments_qs = prev_investments_qs.filter(
                    Q(from_account_id__in=selected_accounts) | Q(to_account_id__in=selected_accounts)
                )

            prev_investments = sum_transfers_base(prev_investments_qs)
            _prev = period_totals(prev_incomes_qs, prev_expenses_all, prev_repayments_qs, prev_capital_qs)
            prev_expenses_op = _prev['expenses'] or 0
            prev_income, prev_cb_rf = _prev['income'], _prev['cashback_refund']
            prev_cap_events = _prev['capital']
            prev_loan_interest = _prev['interest'] or 0
            prev_loan_emi = _prev['emi'] or 0
            prev_loan_principal = prev_loan_emi - prev_loan_interest
            prev_result = calculate_savings(
                income=prev_income,
                cashback_refund=prev_cb_rf,
                operating_expenses=prev_expenses_op,
                loan_interest=prev_loan_interest,
                capital_events=prev_cap_events,
            )
            prev_expenses_total = prev_result.spending
            prev_savings = prev_result.savings

            def calc_pct(current, previous):
                if previous == 0:
                    return None
                return ((current - previous) / previous) * 100

            # TREND DATA FOR PREVIOUS MONTH
            prev_trend_qs = prev_expenses_all.annotate(period=TruncDay('date'))
            prev_day_data = prev_trend_qs.values('period').annotate(total=Sum('base_amount')).order_by('period')
            if salary_cycle_active and salary_cycle_start and salary_cycle_end:
                prev_day_map = {
                    ((item['period'].date() if hasattr(item['period'], 'date') else item['period']) - prev_cycle_start).days + 1: float(item['total'])
                    for item in prev_day_data
                }
                prev_num_days = (prev_cycle_end - prev_cycle_start).days + 1
                daily_map_key = 'cycle_day'
            else:
                prev_day_map = {item['period'].day: float(item['total']) for item in prev_day_data}
                prev_num_days = calendar.monthrange(prev_year, prev_month)[1]
                daily_map_key = 'month_day'

            prev_daily_burn = float(prev_expenses_total) / prev_num_days if prev_num_days > 0 else 0

            prev_month_data = {
                'income': prev_income,
                'expense': prev_expenses_total,
                'investments': prev_investments,
                'savings': prev_savings,
                'income_pct': calc_pct(total_income, prev_income),
                'expense_pct': calc_pct(total_expenses, prev_expenses_total),
                'investments_pct': calc_pct(total_investments, prev_investments),
                'savings_pct': calc_pct(savings, prev_savings),
                'savings_rate': float(prev_result.rate),
                'income_diff_amount': total_income - prev_income,
                'expense_diff_amount': total_expenses - prev_expenses_total,
                'investments_diff_amount': total_investments - prev_investments,
                'daily_burn': prev_daily_burn,
                'daily_map': prev_day_map,
                'daily_map_key': daily_map_key,
            }
            # Add absolute versions for percentages for template display
            for key in list(prev_month_data.keys()):
                val = prev_month_data[key]
                if val is not None and key.endswith('_pct'):
                    prev_month_data[f'{key}_abs'] = abs(val)
        except (ValueError, IndexError):
            pass

    # Process Previous Month Trend Data for Chart Comparison
    prev_trend_data = []
    if trend_is_daily and prev_month_data and 'daily_map' in prev_month_data:
        daily_map = prev_month_data['daily_map']
        if prev_month_data.get('daily_map_key') == 'cycle_day' and salary_cycle_active and salary_cycle_start:
            for p in periods:
                period_date = p.date() if hasattr(p, 'date') else p
                day_idx = (period_date - salary_cycle_start).days + 1
                prev_trend_data.append(daily_map.get(day_idx, 0.0))
        else:
            for p in periods:
                prev_trend_data.append(daily_map.get(p.day, 0.0))
    top_category = category_data[0]['category'] if category_data else None

    # Internal Transfers queryset (used by recent_activity feed)
    transfers_qs = Transfer.objects.filter(user=request.user).select_related('from_account', 'to_account')
    if selected_accounts:
        transfers_qs = transfers_qs.filter(Q(from_account_id__in=selected_accounts) | Q(to_account_id__in=selected_accounts))
    if effective_start_date or effective_end_date:
        if effective_start_date:
            transfers_qs = transfers_qs.filter(date__gte=effective_start_date)
        if effective_end_date:
            transfers_qs = transfers_qs.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            transfers_qs = transfers_qs.filter(date__year__in=selected_years)
        if selected_months:
            transfers_qs = transfers_qs.filter(date__month__in=selected_months)

    # --- NEW: Savings Projection (Linear Extrapolation) ---
    current_date = timezone.localdate()
    current_year = current_date.year
    current_month = current_date.month 

    # 1. Calculate YTD Savings (Strictly for current year, regardless of filters)
    # Derived from monthly_summary_map (already batched above) instead of 3 extra DB queries.
    ytd_income = sum(monthly_summary_map.get((current_year, m), {}).get('income', 0.0) for m in range(1, current_month + 1))
    # 'expense' is expenses + loan interest + capital events counted in averages, i.e. the same
    # spending the savings card uses (principal excluded), so the projection matches it.
    ytd_expenses = sum(monthly_summary_map.get((current_year, m), {}).get('expense', 0.0) for m in range(1, current_month + 1))
    ytd_savings = ytd_income - ytd_expenses
    
    projected_savings = 0
    avg_monthly_savings = 0
    
    # Only project if we have data and positive savings
    if ytd_savings > 0:
        # Avoid division by zero if it's January (month 1)
        # Actually, even in Jan, months_passed is 1. So we are good.
        months_passed = max(current_month, 1)
        avg_monthly_savings = ytd_savings / months_passed
        
        months_remaining = 12 - months_passed
        projected_additional = avg_monthly_savings * months_remaining
        
        projected_savings = ytd_savings + projected_additional
    else:
        # If savings are negative or zero, projection is effectively "0" or "current state"
        # We might handle this in template
        projected_savings = 0


    # Calculate Hero Metrics for the Ideal Layout
    savings_rate_value = float(current_savings.rate)
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
            trend_text = _("Savings efficiency ↑ +%(diff).1f%% %(period)s") % {'diff': rate_diff, 'period': comparison_label}
            trend_type = 'positive'
        elif rate_diff < 0:
            trend_text = _("Savings efficiency ↓ %(diff).1f%% %(period)s") % {'diff': abs(rate_diff), 'period': comparison_label}
            trend_type = 'negative'
        else:
            trend_text = _("Savings efficiency unchanged")
            trend_type = 'neutral'

    hero_metrics = {
        'income': total_income,
        'spent': total_expenses,
        'saved': savings,
        'savings_rate': round(savings_rate_value, 1),
        'savings_rate_diff': round(abs(rate_diff), 1) if prev_month_data and 'savings_rate' in prev_month_data else None,
        'status': hero_status,
        'trend_text': trend_text,
        'trend_type': trend_type,
        'savings_diff_pct': prev_month_data.get('savings_pct') if prev_month_data else None,
        'savings_diff_pct_abs': prev_month_data.get('savings_pct_abs') if prev_month_data else None,
    }

    # Prepare display labels for the template
    if not display_year and len(selected_years) == 1:
        display_year = selected_years[0]
        
    if not display_month and len(selected_months) == 1:
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

            # Construct Query String (Preserve Categories, Payment Methods, Accounts)
            base_qs = []
            for c in selected_categories:
                base_qs.append(f'category={c}')
            for pm in selected_payment_methods:
                base_qs.append(f'payment_method={pm}')
            for a in selected_accounts:
                base_qs.append(f'account={a}')
            
            qs_prev = base_qs + [f'year={py}', f'month={pm}']
            qs_next = base_qs + [f'year={ny}', f'month={nm}']
            
            prev_month_url = f"{reverse('home')}?{'&'.join(qs_prev)}"
            next_month_url = f"{reverse('home')}?{'&'.join(qs_next)}"
            
        except ValueError:
            pass
    
    # --- Emotional Feedback / Insights Logic (Enhanced) ---
    
    insights = []
    # What this legacy code concludes for each migrated insight, for the Insights Engine's shadow comparison
    shadow_legacy = {}

    # Pre-fetch maturing accounts once — used in both the Anomaly Detection alert block
    # (is_current_month_view only) and the Smart Bullet Insights block (always).
    # Avoids running the same identical Account query twice per request.
    maturing_soon_accounts = list(Account.objects.filter(
        user=request.user,
        is_active=True,
        deposit_maturity_date__isnull=False,
        deposit_maturity_date__gte=now.date(),
        deposit_maturity_date__lte=now.date() + timedelta(days=30),
    ).order_by('deposit_maturity_date'))

    # helper for streaks
    def get_monthly_savings_status(u, y, m):
        status = monthly_summary_map.get((y, m), {'income': 0, 'expense': 0})
        return status['income'] > status['expense']

    # Construct date params for deep linking so downstream pages follow the same period context.
    date_param_parts = []
    if start_date or end_date:
        if start_date:
            date_param_parts.append(f"start_date={start_date}")
        if end_date:
            date_param_parts.append(f"end_date={end_date}")
    elif salary_cycle_active and salary_cycle_start and salary_cycle_end:
        date_param_parts.append(f"start_date={salary_cycle_start.strftime('%Y-%m-%d')}")
        date_param_parts.append(f"end_date={salary_cycle_end.strftime('%Y-%m-%d')}")
    else:
        for y in selected_years:
            date_param_parts.append(f"year={y}")
        for m in selected_months:
            date_param_parts.append(f"month={m}")

    date_params = "".join(f"&{p}" for p in date_param_parts)

    def expense_list_url(category_name=None):
        params = []
        if category_name:
            params.append(f"category={category_name}")
        params.extend(date_param_parts)
        query = "&".join(params)
        if query:
            return f"{reverse('expense-list')}?{query}"
        return reverse('expense-list')

    # helper for category links
    def link_cats(cats):
        links_html = format_html_join(
            mark_safe(', '),
            '<a href="{}" class="alert-link text-decoration-underline">{}</a>',
            ((expense_list_url(c), c) for c in cats[:2])
        )
        if len(cats) > 2:
            return format_html('{}, etc.', links_html)
        return links_html

    # 0. Anomaly Detection (Spending Spike)
    # Only if viewing current month (or default view)
    if is_current_month_view and total_expenses > 0:
        shadow_legacy['cashflow.pace_vs_usual'] = None
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
            
            m_total = monthly_summary_map.get((y, m), {}).get('expense', 0)
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
                    shadow_legacy['cashflow.pace_vs_usual'] = {'pct': pct_higher}
                    insights.append({
                        'type': 'warning',
                        'icon': 'graph-up-arrow',
                        'title': _('Traffic Alert'),
                        'message': _("You're pacing %(pct_higher)s%% higher than usual. Slow down to stay on track!") % {'pct_higher': pct_higher},
                        'allow_share': False
                    })

        # 0.5 Fixed-Income Maturing Soon Alert (uses pre-fetched list from above)
        shadow_legacy['accounts.deposit_maturing'] = {a.name.strip().lower() for a in maturing_soon_accounts}
        for mat_acc in maturing_soon_accounts:
            mat_date_str = date_format(mat_acc.deposit_maturity_date, 'd M Y')
            mat_amount_str = f"{mat_acc.currency}{compact_amount(mat_acc.balance, mat_acc.currency)}"
            insights.append({
                'type': 'warning',
                'icon': 'hourglass-split',
                'title': _('Fixed-Income Maturing Soon ⏳'),
                'message': _("%(name)s matures on %(date)s — %(amount)s will need a home.") % {
                    'name': escape(mat_acc.name),
                    'date': mat_date_str,
                    'amount': escape(mat_amount_str),
                },
                'allow_share': False
            })

        # 0.6 Predictive Spending Speed Warning
        speed_alert_categories = []
        for cat in category_limits:
            if cat['limit'] and cat['projected_percent'] and cat['projected_percent'] > 100 and (cat['used_percent'] or 0) <= 100:
                speed_alert_categories.append(cat['name'])
                
        shadow_legacy['budgets.category_runout'] = {n.strip().lower() for n in speed_alert_categories}
        if speed_alert_categories:
            if len(speed_alert_categories) == 1:
                msg = _("Trends show you might overspend on %(category)s soon. Take care!") % {'category': escape(speed_alert_categories[0])}
            elif len(speed_alert_categories) == 2:
                msg = _("Trends show you might overspend on %(cat1)s and %(cat2)s soon.") % {'cat1': escape(speed_alert_categories[0]), 'cat2': escape(speed_alert_categories[1])}
            else:
                msg = _("Trends show you might overspend on %(count)s categories soon (%(cats)s, etc).") % {
                    'count': len(speed_alert_categories),
                    'cats': escape(', '.join(speed_alert_categories[:2]))
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
    
    if total_expenses > 0:
        # 1. Top 5 Categories
        top_5_categories = category_data[:5]
        
        # 2. Savings Rate
        savings_rate = float(current_savings.rate)
        
        # 3. AI Insight (Trend analysis for top category)
        viral_insight = None
        if is_current_month_view:
            shadow_legacy['expenses.category_above_usual'] = None
        if top_5_categories:
            top_cat = top_5_categories[0]['category']
            top_amount = top_5_categories[0]['total']
            
            # Calculate Percentage of total lifestyle expenses
            top_cat_pct = round(float(top_amount) / float(total_expenses) * 100) if total_expenses > 0 else 0
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
            
            # OPTIMIZATION: Fetch category history for the top category in one query
            cat_3m_history = Expense.objects.filter(
                user=request.user, 
                category__iexact=top_cat,
                date__gte=now.replace(day=1) - timedelta(days=100)
            ).annotate(m=TruncMonth('date')).values('m').annotate(total=Sum('base_amount'))
            cat_3m_map = {(item['m'].year, item['m'].month): float(item['total']) for item in cat_3m_history}

            cat_3_month_total = 0
            cat_months_counted = 0
            for i in range(1, 4):
                y_calc = now.year
                m_calc = now.month - i
                while m_calc < 1:
                    m_calc += 12
                    y_calc -= 1
                
                m_cat_total = cat_3m_map.get((y_calc, m_calc), 0)
                if m_cat_total > 0:
                    cat_3_month_total += m_cat_total
                    cat_months_counted += 1
            
            if cat_months_counted > 0:
                cat_avg = float(cat_3_month_total) / cat_months_counted
                if float(top_amount) > cat_avg * 1.1: # 10% higher
                    diff_pct = int(((float(top_amount) - cat_avg) / cat_avg) * 100)
                    if is_current_month_view:
                        shadow_legacy['expenses.category_above_usual'] = {'category': normalize_category(top_cat), 'pct': diff_pct}
                    viral_insight = _("You spent %(diff_pct)s%% more on %(category)s than your 3-month average.") % {
                        'diff_pct': diff_pct,
                        'category': _(top_cat)
                    }
        
        # 4. Daily Burn Rate
        if effective_start_date and effective_end_date:
            num_days = max((effective_end_date - effective_start_date).days + 1, 1)
            if today < effective_start_date:
                days_elapsed = 0
            elif today > effective_end_date:
                days_elapsed = num_days
            else:
                days_elapsed = (today - effective_start_date).days + 1
        elif effective_start_date:
            days_elapsed = max((today - effective_start_date).days + 1, 1)
            num_days = days_elapsed
        else:
            num_days = calendar.monthrange(now.year, now.month)[1]
            if selected_months and len(selected_months) == 1:
                try:
                    m = int(selected_months[0])
                    y = int(selected_years[0]) if selected_years else now.year
                    num_days = calendar.monthrange(y, m)[1]
                except:
                    pass
            days_elapsed = now.day if (not selected_months or (len(selected_months) == 1 and int(selected_months[0]) == now.month)) else num_days

        daily_burn = float(total_expenses) / days_elapsed if days_elapsed > 0 else 0

        # 5. Relatable Metric (Fun/Viral)
        relatable_metric = None
        # The prices below are rupee prices, so the comparison is only offered to rupee users.
        if top_5_categories and currency_symbol == '₹':
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
        pace_budget = total_period_budget
        ideal_spent_so_far = (pace_budget / num_days * days_elapsed) if (pace_budget > 0 and num_days > 0) else 0
        budget_diff = round(ideal_spent_so_far - float(total_expenses), 0)  # positive = under budget
        spent_percent = round(float(total_expenses) / pace_budget * 100, 1) if pace_budget > 0 else 0
        ideal_percent = round(days_elapsed / num_days * 100, 1) if num_days > 0 else 0
        daily_spending_pace_value = round(daily_burn, 0)
        remaining_days = max(num_days - days_elapsed, 0)
        projected_month_spend = round(float(total_expenses) + (daily_spending_pace_value * remaining_days), 0)
        projected_budget_diff = round(pace_budget - projected_month_spend, 0) if pace_budget > 0 else 0

        # Daily burn comparison with last month
        burn_diff_pct = None
        if prev_month_data and prev_month_data.get('daily_burn', 0) > 0:
            burn_diff_pct = ((daily_burn - prev_month_data['daily_burn']) / prev_month_data['daily_burn']) * 100

        spending_pace = {
            'daily_spending_pace': daily_spending_pace_value,
            'projected_month_spend': projected_month_spend,
            'status': 'on_track',
            'diff_amount': max(0, round(projected_month_spend - pace_budget, 0)),
            'budget_multiplier': round(projected_month_spend / pace_budget, 1) if pace_budget > 0 else 0,
            'budget_diff': budget_diff,
            'projected_budget_diff': projected_budget_diff,
            'spent_percent': min(spent_percent, 150),  # cap at 150% for display
            'ideal_percent': ideal_percent,
            'days_elapsed': days_elapsed,
            'num_days': num_days,
            'remaining_days': remaining_days,
            'ideal_spent_so_far': round(ideal_spent_so_far, 0),
            'burn_diff_pct': burn_diff_pct,
            'burn_diff_pct_abs': abs(burn_diff_pct) if burn_diff_pct is not None else None,
        }

        short_insight = ""
        if hero_metrics['status'] == 'excellent':
            short_insight = _("🟢 Great month! You're saving more than usual.")
        elif hero_metrics['status'] == 'good':
            short_insight = _("🔵 Good progress. You're on the right track.")
        else:
            short_insight = _("🟠 Heads up. Expenses are slightly high this month.")

        hero_metrics['short_insight'] = short_insight

        if pace_budget > 0:
            if spending_pace['projected_month_spend'] > pace_budget:
                spending_pace['status'] = 'over_budget'
            elif spending_pace['projected_month_spend'] >= pace_budget * 0.9:
                spending_pace['status'] = 'near_limit'

        # --- Impulse vs Planned Ratio ---
        # Planned = expense description matches an active recurring transaction (it was budgeted/expected)
        # Impulse = everything else (ad-hoc, unplanned spend)
        impulse_ratio = None
        try:
            # Fetch active recurring expense descriptions once (normalized to lowercase strip)
            recurring_desc_set = list(
                RecurringTransaction.objects.filter(
                    user=request.user, is_active=True, transaction_type='EXPENSE'
                ).values_list('description', flat=True)
            )
            # Strip and lower for comparison
            recurring_desc_lower = [d.strip().lower() for d in recurring_desc_set if d]

            # Build period queryset (same filters as the main expense queryset)
            period_expenses_qs = expenses

            # Build SQL Q for "planned" expenses:
            # planned = contains '(recurring)' OR description (lowercased) is in recurring_desc_lower
            planned_q = Q(description__icontains='(recurring)')
            for desc in recurring_desc_lower:
                planned_q |= Q(description__iexact=desc)

            # DB-side aggregation — no Python iteration over expense rows
            agg = period_expenses_qs.aggregate(
                planned_total=Sum('base_amount', filter=planned_q),
                impulse_total=Sum('base_amount', filter=~planned_q),
            )
            planned_total = agg['planned_total'] or Decimal('0.00')
            impulse_total = agg['impulse_total'] or Decimal('0.00')

            period_total = impulse_total + planned_total
            if period_total > 0:
                impulse_pct = round(float(impulse_total) / float(period_total) * 100)
                planned_pct = 100 - impulse_pct

                # Previous period comparison — handle salary cycle vs calendar month
                prev_impulse_pct = None
                if prev_month_data:
                    if salary_cycle_active and salary_cycle_start and salary_cycle_end:
                        # Use the previous cycle date range (already calculated above)
                        prev_cycle_end_date = salary_cycle_start - timedelta(days=1)
                        prev_cycle_start_date, prev_cycle_end_date = SalaryAnalysisService.get_salary_cycle_dates(
                            request.user, prev_cycle_end_date
                        )
                        prev_exp_qs = Expense.objects.filter(
                            user=request.user,
                            date__gte=prev_cycle_start_date,
                            date__lte=prev_cycle_end_date,
                        )
                    elif prev_year and prev_month:
                        prev_exp_qs = Expense.objects.filter(
                            user=request.user,
                            date__year=prev_year,
                            date__month=prev_month,
                        )
                    else:
                        prev_exp_qs = None

                    if prev_exp_qs is not None:
                        # DB-side aggregation for previous period impulse too
                        prev_agg = prev_exp_qs.aggregate(
                            prev_total=Sum('base_amount'),
                            prev_planned=Sum('base_amount', filter=planned_q),
                        )
                        prev_period_total = prev_agg['prev_total'] or Decimal('0.00')
                        prev_planned_total = prev_agg['prev_planned'] or Decimal('0.00')
                        prev_impulse_total = prev_period_total - prev_planned_total
                        if prev_period_total > 0:
                            prev_impulse_pct = round(float(prev_impulse_total) / float(prev_period_total) * 100)

                impulse_ratio = {
                    'impulse_pct': impulse_pct,
                    'planned_pct': planned_pct,
                    'impulse_amount': round(float(impulse_total), 0),
                    'planned_amount': round(float(planned_total), 0),
                    'prev_impulse_pct': prev_impulse_pct,
                    'period_label': (display_month + ' ' + str(display_year)) if (display_month and display_year) else (calendar.month_name[now.month] + ' so far'),
                }
        except Exception:
            pass


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
            'total_monthly_budget': total_period_budget,
            'impulse_ratio': impulse_ratio,
        }

    # 1. Budget Warnings (High Priority)

    over_budget_cats = [c for c in category_limits if c['used_percent'] is not None and c['used_percent'] > 100]
    near_budget_cats = [c for c in category_limits if c['status'] == 'limit']   # 85%+ like the Budget page
    
    if is_current_month_view:
        shadow_legacy['budgets.limit_passed'] = {c['name'].strip().lower() for c in over_budget_cats}
        shadow_legacy['budgets.limit_nearing'] = set() if over_budget_cats else {c['name'].strip().lower() for c in near_budget_cats}

    # Check savings rate for "Softener" context
    savings_rate_alert = float(current_savings.rate)
    
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
        if is_current_month_view:
            shadow_legacy['cashflow.savings_momentum'] = False
        # Calculate Category Savings (Cause of the win)
        # We need prev month category breakdown
        prev_cat_qs = Expense.objects.filter(user=request.user, **prev_window).values('category').annotate(total=Sum('base_amount'))
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
        if savings_rate_denominator > 0 and savings > 0:
            savings_rate = float(current_savings.rate)
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
                 if is_current_month_view:
                     shadow_legacy['cashflow.savings_momentum'] = True
                 savings_str = "10x+" if prev_month_data['savings_pct_abs'] > 999 else f"{prev_month_data['savings_pct_abs']:.0f}%"
                 insights.append({
                    'type': 'success',
                    'icon': 'graph-up-arrow',
                    'title': _('Momentum Building 🚀'),
                    'message': _("Your savings grew by %(savings_pct_abs)s %(period)s. You're getting better at this!") % {'savings_pct_abs': savings_str, 'period': comparison_label},
                    'allow_share': True,
                    'share_text': _("My savings grew by %(savings_pct_abs)s this month! 🚀 via TrackMyRupee") % {'savings_pct_abs': savings_str}
                })
        
        # Expense Control Win (if we haven't already praised savings)
        if len(insights) == 0: 
            if prev_month_data['expense_pct'] and prev_month_data['expense_pct'] < -5:
                 expense_str = "10x+" if prev_month_data['expense_pct_abs'] > 999 else f"{prev_month_data['expense_pct_abs']:.0f}%"
                 msg_text = _("You've cut spending by %(expense_pct_abs)s.") % {'expense_pct_abs': expense_str}
                 share_text = _("I cut my spending by %(expense_pct_abs)s this month! 👍 via TrackMyRupee") % {'expense_pct_abs': expense_str}
                 
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
                    'share_text': _("I cut my spending by %(expense_pct_abs)s this month! 👍 via TrackMyRupee") % {'expense_pct_abs': expense_str}
                })

    # 3. Streak & Identity (Reassuring / Habit Forming)
    # Only calculate if current status is good
    if is_current_month_view:
        shadow_legacy['cashflow.saving_streak'] = None
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
            if is_current_month_view:
                shadow_legacy['cashflow.saving_streak'] = streak
            def get_ordinal(n):
                if 11 <= (n % 100) <= 13:
                    return f"{n}th"
                return f"{n}{{}}".format({1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th'))

            insights.append({
                'type': 'info', # Use Info for "Identity/Streak"
                'icon': 'fire',
                'title': _('On a Roll!'),
                'message': _("This is your %(streak)s month in a row staying under budget.") % {'streak': get_ordinal(streak)},
                'allow_share': True,
                'share_text': _("I've stayed under budget for %(streak)s months in a row! via TrackMyRupee") % {'streak': streak}
            })

    if is_current_month_view:
        shadow_legacy['cashflow.year_pace'] = Decimal(str(projected_savings)) if projected_savings > 0 else None
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
                _("If you maintain this pace (based on year-to-date average savings), you could accumulate {proj} this year."),
                proj=proj_bold,
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
    today_date = timezone.localdate()
    seven_days_later = today_date + timedelta(days=7)

    upcoming_payments = list(
        RecurringTransaction.objects.filter(
            user=request.user,
            is_active=True,
            next_due_date__gte=today_date,
            next_due_date__lte=seven_days_later
        ).order_by('next_due_date')[:3]
    )
    
    shadow_legacy['recurring.bills_soon'] = [(p.description.strip(), p.next_due_date.isoformat()) for p in upcoming_payments]
    for payment in upcoming_payments:
        days_left = (payment.next_due_date - today_date).days
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

    if insights_shadow.should_compare(request.user):
        insights_shadow.compare(request.user, shadow_legacy)
    
    # Layer 5: Monthly Story Generation
    if len(selected_months) == 1 and len(selected_years) == 1:
        story_month_name = calendar.month_name[int(selected_months[0])]
    else:
        story_month_name = _("This period")

    # Calculate Future Growth (Total Surplus = Investments + Remaining Savings)
    # savings is already (income - lifestyle_expenses) because total_expenses excludes investments
    total_wealth_contribution = max(0, savings)
    future_growth_pct = round(float(total_wealth_contribution) / float(total_income) * 100) if total_income > 0 else 0
    lifestyle_pct = round(float(total_expenses) / float(total_income) * 100) if total_income > 0 else 0
    
    # Calculate one-off income for flagging in Monthly Story
    one_off_income_agg = incomes.filter(source_type__in=['Investment Returns', 'Other']).aggregate(Sum('base_amount'))['base_amount__sum'] or Decimal('0.00')
    
    income_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_income, currency_symbol)}</b>")
    if one_off_income_agg > 0:
        one_off_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(one_off_income_agg, currency_symbol)}</b>")
        income_bold = format_html(
            _("{income} (including {one_off} in one-off/investment income)"),
            income=income_bold,
            one_off=one_off_bold
        )
    lifestyle_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_expenses, currency_symbol)}</b>")
    invest_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_investments, currency_symbol)}</b>")
    future_total_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(total_wealth_contribution, currency_symbol)}</b>")
    
    # Narrative Structure
    if total_wealth_contribution > 0:
        if total_investments > 0:
            monthly_story = format_html(
                _("In {month}, you earned {income}. You spent {lifestyle} ({life_pct}%) on lifestyle, and saved {future_total} ({future_pct}%) for your future (including {invest} invested)."),
                month=story_month_name,
                income=income_bold,
                lifestyle=lifestyle_bold,
                life_pct=lifestyle_pct,
                future_total=future_total_bold,
                future_pct=future_growth_pct,
                invest=invest_bold
            )
        else:
            monthly_story = format_html(
                _("In {month}, you earned {income}. You spent {lifestyle} ({life_pct}%) on lifestyle, and saved {future_total} ({future_pct}%) for your future."),
                month=story_month_name,
                income=income_bold,
                lifestyle=lifestyle_bold,
                life_pct=lifestyle_pct,
                future_total=future_total_bold,
                future_pct=future_growth_pct
            )
    else:
        if total_investments > 0:
            monthly_story = format_html(
                _("In {month}, you earned {income}. You spent {lifestyle} on lifestyle, and invested {invest} toward future wealth."),
                month=story_month_name,
                income=income_bold,
                lifestyle=lifestyle_bold,
                invest=invest_bold
            )
        else:
            monthly_story = format_html(
                _("In {month}, you earned {income}. You spent {lifestyle} on lifestyle."),
                month=story_month_name,
                income=income_bold,
                lifestyle=lifestyle_bold
            )
    
    if projected_savings > 0:
        proj_bold = mark_safe(f"<b>{currency_symbol}{compact_amount(projected_savings, currency_symbol)}</b>")
        monthly_story += format_html(
            _(" At this pace (using your year-to-date average), you could save {proj} by year's end."),
            proj=proj_bold
        )
    # has_any_data was computed once at the very top of this view — no second query needed.

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

    # --- Capital Events for the current period ---
    # Fetch all capital events for the viewed period in a single query (no N+1).
    capital_events_qs = CapitalEvent.objects.filter(user=request.user).select_related('account', 'linked_loan')
    if selected_accounts:
        capital_events_qs = capital_events_qs.filter(account_id__in=selected_accounts)
    if effective_start_date or effective_end_date:
        if effective_start_date:
            capital_events_qs = capital_events_qs.filter(date__gte=effective_start_date)
        if effective_end_date:
            capital_events_qs = capital_events_qs.filter(date__lte=effective_end_date)
    else:
        if selected_years:
            capital_events_qs = capital_events_qs.filter(date__year__in=selected_years)
        if selected_months:
            capital_events_qs = capital_events_qs.filter(date__month__in=selected_months)

    capital_events_list = list(capital_events_qs)

    # Build chart annotation data: one marker per event, sorted by date
    capital_event_annotations = [
        {
            'date': e.date.strftime('%Y-%m-%d'),
            'amount': float(e.base_amount),
            'subtype': e.get_subtype_display(),
            'note': e.note or '',
            'currency': e.currency,
        }
        for e in capital_events_list
        if e.exclude_from_averages
    ]

    # Dashboard callout: aggregate multiple events into a single card message
    capital_event_callout = None
    if capital_events_list:
        excluded_events = [e for e in capital_events_list if e.exclude_from_averages]
        if excluded_events:
            if len(excluded_events) == 1:
                ev = excluded_events[0]
                capital_event_callout = {
                    'count': 1,
                    'total': float(ev.base_amount),
                    'label': ev.get_subtype_display(),
                    'regular_spent': float(total_expenses),
                    'events': excluded_events,
                }
            else:
                capital_event_callout = {
                    'count': len(excluded_events),
                    'total': sum(float(e.base_amount) for e in excluded_events),
                    'label': _('Capital Events'),
                    'regular_spent': float(total_expenses),
                    'events': excluded_events,
                }

    # --- Smart Insights Bullets (New Card) ---
    raw_insights = []

    # Capital Event Insight
    if capital_event_callout:
        list_url = reverse('capital-event-list')
        if capital_event_callout['count'] == 1:
            ev = capital_event_callout['events'][0]
            if ev.note and ev.note.strip():
                title_text = format_html(_("Capital Event: <b>{}</b> this month"), ev.note.strip())
            else:
                title_text = mark_safe(_("<b>One-off Capital Event</b>"))
        else:
            title_text = format_html(_("<b>{} Capital Events this month</b>"), capital_event_callout['count'])
        
        curr_symbol = capital_event_callout['events'][0].currency
        event_text = format_html(
            _("{title}"
              " You made a one-off payment of {curr}{total}. "
              "<a href='{url}' class='text-primary fw-semibold text-decoration-none ms-1'>View Capital Events &rarr;</a>"),
            title=title_text,
            curr=curr_symbol,
            total=compact_amount(capital_event_callout['total'], curr_symbol),
            regular=compact_amount(capital_event_callout['regular_spent'], curr_symbol),
            url=list_url
        )
        raw_insights.append({
            'text': event_text,
            'icon': 'bi-flag',
            'theme': 'primary',
            'score': 5
        })

    # Maturing Fixed-Income Accounts Bullet Insight
    # Reuse the pre-fetched list from above — no second DB query
    for mat_acc in maturing_soon_accounts:
        mat_date_str = date_format(mat_acc.deposit_maturity_date, 'd M Y')
        mat_amount_str = f"{mat_acc.currency}{compact_amount(mat_acc.balance, mat_acc.currency)}"
        raw_insights.append({
            'text': format_html(_("<b>{}</b> matures on <b>{}</b> — <span class='text-primary fw-bold'>{}</span> will need a home."), mat_acc.name, mat_date_str, mat_amount_str),
            'icon': 'bi-clock-history',
            'theme': 'warning',
            'score': 2
        })
    
    # 0. Power AI Insight (Positive/Proactive)
    if total_income > 0 and total_expenses > 0 and top_5_categories:
        # Pick the top category, but skip 'Rent' for the 'Cut 20%' insight as it's usually fixed
        target_cat_data = top_5_categories[0]
        if target_cat_data['category'] == 'Rent' and len(top_5_categories) > 1:
            target_cat_data = top_5_categories[1]
            
        top_cat = target_cat_data['category']
        top_amount = target_cat_data['total']
        top_cat_pct = round(float(top_amount) / float(total_expenses) * 100) if total_expenses > 0 else 0
        potential_savings = float(top_amount) * 0.20
        
        power_insight_text = format_html(
            _("You spent <b>{pct}%</b> on <b>{cat}</b> this month. Cut 20% to save <span class='text-success fw-bold'>{sym}{savings}</span>."),
            pct=top_cat_pct,
            cat=_(top_cat),
            sym=currency_symbol,
            savings=compact_amount(potential_savings, currency_symbol)
        )
        raw_insights.append({
            'text': power_insight_text,
            'icon': 'bi-robot',
            'theme': 'primary',
            'score': 20 # Positive/Actionable
        })
    
    # 1. Highest Spending Category (Neutral)
    if top_category:
        cat_url = expense_list_url(top_category)
        cat_obj = user_categories.get(top_category)
        icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
        raw_insights.append({
            'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> is your top expense this month."), url=cat_url, cat=top_category),
            'icon': icon_cls,
            'theme': 'primary',
            'score': 40, # Neutral
            'cat': top_category
        })

    # 2. Budget Breaches (Warnings)
    for cat in over_budget_cats:
        over_amt = cat['total'] - cat['limit']
        if over_amt > 0:
            cat_url = expense_list_url(cat['name'])
            cat_obj = user_categories.get(cat['name'])
            icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
            raw_insights.append({
                'text': format_html(_("<a href='{url}' class='text-decoration-none text-reset hover-link'>{cat}</a> category exceeded budget by <span class='text-danger fw-bold'>{val}</span>"), url=cat_url, cat=cat['name'], val=format_currency(over_amt)),
                'icon': icon_cls,
                'theme': 'danger',
                'score': 30, # Warning
                'cat': cat['name']
            })
            
    # 3. Category MoM Spikes & Drops (Mixed)
    if prev_month_data and len(selected_years) == 1 and len(selected_months) == 1 and not salary_cycle_active:
        if 'prev_cat_map' not in locals():
            prev_cat_qs = Expense.objects.filter(
                user=request.user, 
                date__year=prev_year, 
                date__month=prev_month
            ).values('category').annotate(total=Sum('base_amount'))
            prev_cat_map = {item['category'].strip(): float(item['total']) for item in prev_cat_qs}
        
        spikes = []
        drops = []
        
        for item in category_data:
            cat_name = item['category']
            current_total = float(item['total'])
            prev_total = prev_cat_map.get(cat_name, 0)
            
            if prev_total > 0:
                diff_pct = ((current_total - prev_total) / prev_total) * 100
                
                # Check for significant changes (10%+)
                if diff_pct >= 10:
                    # Avoid redundancy
                    is_redundant = any(c.get('cat') == cat_name or (c.get('text') and cat_name in str(c.get('text'))) or (c.get('text') and hasattr(c.get('text'), 'find') and c.get('text').find(cat_name) != -1) for c in raw_insights)
                    if not is_redundant:
                        spikes.append({'cat': cat_name, 'pct': int(diff_pct)})
                elif diff_pct <= -10:
                    is_redundant = any(c.get('cat') == cat_name or (c.get('text') and cat_name in str(c.get('text'))) or (c.get('text') and hasattr(c.get('text'), 'find') and c.get('text').find(cat_name) != -1) for c in raw_insights)
                    if not is_redundant:
                        drops.append({'cat': cat_name, 'pct': int(abs(diff_pct))})

        # Process Spikes (Club if > 1)
        if len(spikes) > 1:
            spike_links = []
            for s in spikes[:3]:
                url = expense_list_url(s['cat'])
                spike_links.append(format_html("<a href='{url}' class='text-decoration-none text-reset hover-link fw-bold'>{cat}</a>", url=url, cat=s['cat']))
            
            spike_cats_html = spike_links[0]
            if len(spike_links) > 1:
                spike_cats_html = format_html("{} and {}", mark_safe(", ".join([str(x) for x in spike_links[:-1]])), spike_links[-1])
            
            raw_insights.append({
                'text': format_html(_("Heads up! Spending is up in <b>{count} categories</b> including {cats}. <br> <span class='small opacity-50'>Take a quick look to see if these spikes were intentional.</span>"), count=len(spikes), cats=spike_cats_html),
                'icon': 'bi-exclamation-triangle',
                'theme': 'warning',
                'score': 30
            })
        else:
            for s in spikes:
                cat_url = expense_list_url(s['cat'])
                cat_obj = user_categories.get(s['cat'])
                icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
                raw_insights.append({
                    'text': format_html(_("Heads up! <a href='{url}' class='text-decoration-none text-reset hover-link fw-bold'>{cat}</a> is up <span class='text-danger fw-bold'>{pct}%</span>. <br> <span class='small opacity-50'>Take a quick look to see if this spike was intentional.</span>"), url=cat_url, cat=s['cat'], pct=s['pct']),
                    'icon': icon_cls,
                    'theme': 'warning',
                    'score': 30,
                    'cat': s['cat']
                })

        # Process Drops (Club if > 1)
        if len(drops) > 1:
            drop_links = []
            for d in drops[:3]:
                url = expense_list_url(d['cat'])
                drop_links.append(format_html("<a href='{url}' class='text-decoration-none text-reset hover-link fw-bold'>{cat}</a>", url=url, cat=d['cat']))
            
            drop_cats_html = drop_links[0]
            if len(drop_links) > 1:
                drop_cats_html = format_html("{} and {}", mark_safe(", ".join([str(x) for x in drop_links[:-1]])), drop_links[-1])
            
            raw_insights.append({
                'text': format_html(_("Look at you! You've reduced spending in <b>{count} categories</b> including {cats}! <br> <span class='small opacity-50'>That's money staying in your pocket where it belongs.</span>"), count=len(drops), cats=drop_cats_html),
                'icon': 'bi-lightning-charge',
                'theme': 'success',
                'score': 20
            })
        else:
            for d in drops:
                cat_url = expense_list_url(d['cat'])
                cat_obj = user_categories.get(d['cat'])
                icon_cls = cat_obj.icon if cat_obj else 'bi-tag'
                raw_insights.append({
                    'text': format_html(_("Look at you! <a href='{url}' class='text-decoration-none text-reset hover-link fw-bold'>{cat}</a> spending dropped <span class='text-success fw-bold'>{pct}%</span>! <br> <span class='small opacity-50'>That's money staying in your pocket where it belongs.</span>"), url=cat_url, cat=d['cat'], pct=d['pct']),
                    'icon': icon_cls,
                    'theme': 'success',
                    'score': 20,
                    'cat': d['cat']
                })

    # 4. Financial Coach Moments (Milestones)
    # Net Worth Milestone (match Net Worth card logic to avoid contradictory insights)
    # NOTE: _ledger_net_worth_result is reused below in the Net Worth section to avoid a second bulk query.
    _ledger_net_worth_result = LedgerReadService.get_net_worth(request.user)
    milestone_net_worth = _ledger_net_worth_result[0]
    milestones = [100000, 500000, 1000000, 2500000, 5000000, 10000000]
    applicable_milestone = None
    for m in milestones:
        if float(milestone_net_worth) >= m:
            applicable_milestone = m
        else:
            break

    if applicable_milestone:
        raw_insights.append({
            'text': format_html(_("You crossed <span class='fw-bold'>{}</span> in net worth! <br> <span class='small opacity-50'>That's years of discipline showing up as a number. Most people never get here. You did.</span>"), compact_amount(applicable_milestone, currency_symbol)),
            'icon': 'bi-trophy',
            'theme': 'warning',
            'score': 10 # Milestone
        })
            
    # Good Month Moment
    if prev_month_data and prev_month_data.get('savings', 0) > 0 and savings > prev_month_data['savings'] * Decimal('1.2'):
        projected_annual = savings * 12
        raw_insights.append({
            'text': format_html(_("High five! You're saving more than usual this month. <br> <span class='small opacity-50'>If you keep this momentum, you could save <span class='fw-bold text-success'>{}</span> this year!</span>"), compact_amount(projected_annual, currency_symbol)),
            'icon': 'bi-stars',
            'theme': 'success',
            'score': 10 # Milestone
        })

    # Sort by score: Milestones (10) > Positive (20) > Warnings (30) > Neutral (40)
    raw_insights.sort(key=lambda x: x['score'])
    
    # Slice to a reasonable amount (e.g., 6)
    smart_bullet_insights = raw_insights[:10]

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
        'LOAN': {'items': [], 'total': Decimal('0.00'), 'icon': '🏦', 'label': _('Loan Repayments')},
        'CAPITAL': {'items': [], 'total': Decimal('0.00'), 'icon': '💼', 'label': _('Capital Events')},
        'INSURANCE_PREMIUM': {'items': [], 'total': Decimal('0.00'), 'icon': '🛡️', 'label': _('Insurance Premiums')},
    }
    active_recurring = RecurringTransaction.objects.filter(user=request.user, is_active=True).select_related('account', 'from_account', 'to_account')
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
            # Determine if it's an investment
            if rtype == 'TRANSFER' and rt.to_account and rt.to_account.account_type in investment_codes():
                rtype = 'INVESTMENT'
                
            item = {
                'id': rt.id,
                'pk': rt.uuid,
                'uuid': rt.uuid,
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

    # Sorting items within groups by date
    for group in recurring_groups.values():
        group['items'].sort(key=lambda x: x['date'])

    # Calculate Net Recurring Balance (Positive if surplus, Negative if deficit)
    recurring_net_balance = (
        recurring_groups['INCOME']['total']
        - recurring_groups['EXPENSE']['total']
        - recurring_groups['INVESTMENT']['total']
        - recurring_groups['LOAN']['total']
        - recurring_groups['CAPITAL']['total']
        - recurring_groups['INSURANCE_PREMIUM']['total']
    )

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
        m_total = monthly_summary_map.get((y, m), {}).get('expense', 0)
        
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

    # Net Worth & Asset Allocation Calculation (multi-currency aware)
    accounts = Account.objects.filter(user=request.user, is_active=True)
    base_currency = currency_symbol  # user's profile currency

    # Convert balances to base currency from adapter (feature-flagged with fallback).
    # Reuse the result already fetched for the milestone section above (avoids duplicate bulk queries).
    net_worth, account_base_balances = _ledger_net_worth_result

    # Net Worth Change Calculation (Growth this month)
    # We estimate start-of-month net worth as current net worth minus this month's net cashflow (income - expense)
    # This assumes all income/expense transactions affect the total net worth.
    
    total_liabilities = Decimal(str(LoanService.get_total_liabilities(request.user)))
    net_worth_before_liabilities = net_worth + total_liabilities
    # Note: LedgerReadService.get_net_worth already subtracted liabilities. Do not double subtract.
    
    net_worth_change = Decimal('0.00')
    net_worth_percent = Decimal('0.00')
    
    # Get income and expense sums for the current month ONLY (for change indicators)
    curr_mon_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # If the view above already computed totals for exactly this calendar month (the default,
    # unfiltered dashboard view — including the common case where the salary cycle happens to
    # span the full calendar month), reuse them instead of firing 4 duplicate queries.
    _reuse_month_totals = (
        not (start_date or end_date)
        and is_current_month_view
        and not selected_categories
        and not selected_payment_methods
        and not selected_accounts
        and (
            not salary_cycle_active
            or (salary_cycle_start == curr_mon_start.date() and salary_cycle_end and salary_cycle_end >= now.date())
        )
    )
    if _reuse_month_totals:
        # The page already computed this month's savings (same definition).
        month_savings = current_savings
    else:
        month_savings = savings_for_period(request.user, curr_mon_start.date(), timezone.localdate())

    # Net worth grows by what you save: income minus spending, where repaying loan principal is
    # not spending (cash becomes less debt) and capital events count per their own switches.
    net_worth_change = month_savings.savings
    start_net_worth = net_worth - net_worth_change
    
    if start_net_worth > 0:
        net_worth_percent = (net_worth_change / start_net_worth * 100).quantize(Decimal('0.1'))
    elif start_net_worth == 0 and net_worth_change > 0:
        net_worth_percent = Decimal('100.0')

    # --- NET WORTH TREND (Sparkline and Chart) ---
    net_worth_trend = FinancialService.get_cumulative_net_worth_history(request.user, net_worth, 6)
    
    # For the main chart context
    net_worth_history_formatted = FinancialService.get_monthly_history(request.user, 6)
    net_worth_labels = [date_format(m['month'], 'M Y') for m in net_worth_history_formatted]
    net_worth_data = net_worth_trend # Use the cumulative values for the trend

    # Group by account type group for Asset Allocation chart (using converted balances)
    code_to_group = {}
    for group_name, choices in Account.ACCOUNT_TYPES:
        for val, label in choices:
            if val not in code_to_group:
                code_to_group[val] = group_name

    from collections import defaultdict
    group_totals = defaultdict(Decimal)
    liability_pks = set()
    for acc in accounts:
        if is_liability(acc.account_type):
            # Cards and loans are what you owe, not what you hold (a loan account's value is the
            # outstanding principal, a positive number), so they never belong in the allocation.
            liability_pks.add(acc.pk)
            continue
        group_name = code_to_group.get(acc.account_type, 'Other')
        group_totals[group_name] += account_base_balances[acc.pk]

    asset_allocation = []
    cumulative_percent = 0
    circumference = 2 * 3.14159 * 45
    
    # Use sum of positive balances for allocation donut to avoid >100% or negative segments
    total_assets = sum(float(v) for pk, v in account_base_balances.items() if v > 0 and pk not in liability_pks)
    
    import re
    for group_name, total in sorted(group_totals.items(), key=lambda x: x[1], reverse=True):
        if total <= 0: continue # Skip liabilities in allocation donut
        percent = round((float(total) / float(total_assets) * 100), 1) if total_assets > 0 else 0
        group_type_id = re.sub(r'[^A-Z0-9_]', '_', group_name.upper())
        asset_allocation.append({
            'type_key': group_type_id,
            'type': group_name,
            'total': float(total),
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

    recent_contributions = list(GoalContribution.objects.filter(goal__user=request.user).order_by('-date')[:10])
    for c in recent_contributions:
        c.transaction_type = 'SAVINGS'
        c.description = _("Contribution: %(goal)s") % {'goal': c.goal.name}

    from itertools import chain
    recent_activity = sorted(
        chain(recent_expenses, recent_incomes, recent_transfers, recent_contributions),
        key=lambda x: x.date,
        reverse=True
    )[:10]

    # Add Savings Amount to hero_metrics for the hero card
    hero_metrics['savings_amount'] = net_worth_change

    # --- NET WORTH FORECAST (Next 3 Months) ---
    # Derive the 3-month historical average from monthly_summary_map (0 extra DB queries)
    # instead of FinancialService.get_historical_average, which re-queries the same data.
    _hist_y, _hist_m = now.year, now.month
    _hist_months = []
    for _hist_i in range(3):
        _hist_m -= 1
        if _hist_m == 0:
            _hist_m = 12
            _hist_y -= 1
        _hist_months.append((_hist_y, _hist_m))
    _hist_income_sum = sum(monthly_summary_map.get(k, {}).get('income', 0.0) for k in _hist_months)
    _hist_expense_sum = sum(
        monthly_summary_map.get(k, {}).get('expense_base', 0.0) + monthly_summary_map.get(k, {}).get('loan_interest', 0.0)
        for k in _hist_months
    )
    avg_monthly_savings = Decimal(str((_hist_income_sum - _hist_expense_sum) / 3))
    
    # 0.5 Integrated Sparkline Logic
    # History is 6 months. We append 3 forecast months.
    forecast_index_start = len(net_worth_trend) # Usually 6
    
    for i in range(1, 4):
        f_month = now.month + i
        f_year = now.year
        while f_month > 12:
            f_month -= 12
            f_year += 1
        
        month_date = date(f_year, f_month, 1)
        projected_val = net_worth + (avg_monthly_savings * i)
        
        # Add to the integrated sparkline arrays
        net_worth_labels.append(date_format(month_date, 'M Y'))
        net_worth_trend.append(float(projected_val))
    # Onboarding Checklist State
    profile = request.user.profile
    checklist_cache_key = f'checklist_done_{request.user.id}'
    
    # Show checklist card only for users who completed onboarding v2 (has onboarding_completed_at)
    # and have not dismissed it
    show_onboarding_checklist = False
    completed_count = 0
    total_checklist_count = 6
    checklist_items = []

    if profile.onboarding_completed_at and not profile.dismissed_onboarding_checklist:
        from ..onboarding_v2 import get_checklist_items
        checklist_items_raw, completed_count, total_checklist_count = get_checklist_items(request.user)
        show_onboarding_checklist = completed_count < total_checklist_count
        # Map to dashboard format
        checklist_items = [
            {
                'key': item['id'],
                'label': item['title'],
                'desc': item.get('description', ''),
                'done': item['done'],
                'url': item.get('flow_url', ''),
            }
            for item in checklist_items_raw
        ]
        if not show_onboarding_checklist:
            cache.set(checklist_cache_key, True, 60 * 60 * 24)

    # Optimization: Calculate bento invested data in a single query instead of N+1
    if net_worth_history:
        bento_start_date = net_worth_history[0]['month'].replace(day=1)
        investments_grouped = Transfer.objects.filter(
            user=request.user,
            to_account__account_type__in=list(investment_codes()),
            date__gte=bento_start_date
        ).annotate(
            month_group=TruncMonth('date')
        ).values('month_group').annotate(
            total=Sum('converted_amount')
        ).order_by('month_group')
        
        investments_by_month = {
            item['month_group'].strftime('%Y-%m') if item['month_group'] else '': item['total'] or Decimal('0.00')
            for item in investments_grouped
        }
        
        bento_invested_data_list = [
            float(investments_by_month.get(m['month'].strftime('%Y-%m'), 0))
            for m in net_worth_history
        ]
    else:
        bento_invested_data_list = []

    # Ensure salary cycle dates are always available for the dashboard header
    header_salary_cycle_start = salary_cycle_start
    header_salary_cycle_end = salary_cycle_end
    if not header_salary_cycle_start or not header_salary_cycle_end:
        header_salary_cycle_start, header_salary_cycle_end = SalaryAnalysisService.get_salary_cycle_dates(request.user, today)

    context = {
        'show_onboarding_checklist': show_onboarding_checklist,
        'onboarding_checklist_completed_count': completed_count,
        'onboarding_checklist_items': checklist_items,
        'net_worth': net_worth,
        'net_worth_change': net_worth_change,
        'net_worth_percent': net_worth_percent,
        'net_worth_trend': net_worth_trend,
        'forecast_index_start': forecast_index_start,
        'net_worth_labels': net_worth_labels,
        'is_net_worth_locked': not request.user.profile.has_net_worth_access,
        'is_ai_locked': not request.user.profile.has_ai_access,
        'accounts': list(accounts),
        'net_worth_before_liabilities': net_worth_before_liabilities,
        'asset_allocation': asset_allocation,
        'recent_activity': recent_activity,
        'has_projection': has_projection,
        'projected_savings': projected_savings,
        'is_new_user': not has_any_data,
        'actionable_alerts': actionable_alerts,
        'smart_insights': smart_insights,
        'monthly_story': monthly_story,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'savings': savings,
        'remaining_savings': savings - total_investments,
        'categories': categories,
        'category_amounts': category_amounts,
        'category_data': category_data, # Passing full queryset for the summary table
        'category_limits': category_limits,
        'trend_labels': trend_labels,
        'trend_datasets': trend_datasets,
        'trend_title': trend_title,
        'trend_is_daily': trend_is_daily,
        'trend_7d_avg': trend_7d_avg,
        'prev_trend_data': prev_trend_data,
        'top_labels': top_labels,
        'top_amounts': top_amounts,
        # New Context
        'bento_income_data': [m['income'] for m in net_worth_history],
        'bento_income_labels': [date_format(m['month'], 'M Y') for m in net_worth_history],
        'bento_invested_data': bento_invested_data_list,
        'bento_invested_labels': [date_format(m['month'], 'M Y') for m in net_worth_history],
        'ie_labels': ie_labels,
        'ie_income_data': ie_income_data,
        'ie_expense_data': ie_expense_data,
        'ie_savings_data': ie_savings_data,
        'ie_invested_data': ie_invested_data,
        'payment_labels': payment_labels,
        'payment_data': payment_data,
        'years': years,
        'selected_years': selected_years,
        'selected_months': selected_months,
        'selected_year': display_year,    # NEW: For template display labels
        'selected_month': display_month,  # NEW: For template display labels
        'selected_categories': selected_categories,
        'selected_payment_methods': selected_payment_methods,
        'selected_accounts': selected_accounts,
        'salary_cycle_active': salary_cycle_active,
        'salary_cycle_start': header_salary_cycle_start,
        'salary_cycle_end': header_salary_cycle_end,
        'salary_cycle_range': cycle_ctx['cycle_range'],
        'salary_date': cycle_ctx['salary_date'],
        'comparison_label': comparison_label,
        'months_list': [(i, calendar.month_name[i]) for i in range(1, 13)],
        'recurring_groups': recurring_groups,
        'recurring_net_balance': recurring_net_balance,
        'filter_config': DASHBOARD_FILTERS,
        'applied_state': applied_state,
        'start_date': start_date_str or (start_date_obj.strftime('%Y-%m-%d') if start_date_obj else ''),
        'end_date': end_date_str or (end_date_obj.strftime('%Y-%m-%d') if end_date_obj else ''),
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
        'trend_iso_dates': trend_iso_dates,
        'proj_labels': proj_labels,
        'proj_historical': proj_historical,
        'proj_forecast': proj_forecast,
        # Capital Events
        'capital_event_annotations': capital_event_annotations,
    }

    if home_cache_key:
        cache.set(home_cache_key, context, 14400)  # 4 hours (was 300s)

    # --- SMART CONTEXTUAL NUDGES ---
    # Instead of showing on the dashboard, we add them to the notification system.
    
    # helper for creating nudges
    def add_nudge_alt(title, message, n_type='SYSTEM', link=None, slug=None):
        try:
            obj, created = Notification.objects.get_or_create(
                user=request.user,
                slug=slug,
                defaults={
                    'title': title,
                    'message': message,
                    'notification_type': n_type,
                    'link': link,
                    'is_read': False
                }
            )
        except Notification.MultipleObjectsReturned:
            # If multiple exist for some reason (e.g. legacy data), keep the newest, delete others
            notifications = Notification.objects.filter(user=request.user, slug=slug).order_by('-created_at')
            obj = notifications.first()
            notifications.exclude(id=obj.id).delete()
            created = False
        
        # If it already existed but was created before we had link logic, update it
        if not created and not obj.link and link:
            obj.link = link
            obj.notification_type = n_type
            obj.save()

    # 1. Accounts Nudge: If only 1 account exists
    # `accounts` (same active-accounts queryset) was already evaluated for net worth above,
    # so len() reuses its result cache instead of issuing a COUNT query.
    if len(accounts) == 1:
        add_nudge_alt(
            _('Smart Tip: Multiple Accounts'),
            _('Add separate accounts (like cash, bank, or UPI) to track your money more accurately across all sources.'),
            n_type='SYSTEM',
            link=reverse('settings-home'), # Settings/Accounts page
            slug='nudge-multiple-accounts'
        )
    
    # 2. Expense Category Nudge: Check for "Miscellaneous" or "Other" usage
    misc_usage = Expense.objects.filter(
        user=request.user, 
        category__in=['Miscellaneous', 'Other', 'Misc']
    ).count()
    if misc_usage >= 3:
        add_nudge_alt(
            _('Organize Your Spend'),
            _('Categorizing helps you see exactly where your money goes. Try creating specific categories for better insights!'),
            n_type='ANALYTICS',
            link=reverse('category-list'),
            slug='nudge-organize-spend'
        )
    
    # 3. Potential Recurring Nudge: Looking for patterns (throttled: the GROUP BY below is
    # expensive, so only re-check once per day per user instead of on every page load).
    recurring_suffix = ' (Recurring)'

    def normalize_recurring_description(value):
        normalized = (value or '').strip().lower()
        if normalized.endswith(recurring_suffix.lower()):
            normalized = normalized[:-len(recurring_suffix)].rstrip()
        return normalized

    nudge_repeat_cache_key = f'nudge_repeat_checked_{request.user.id}'
    if cache.get(nudge_repeat_cache_key) is None:
        three_months_ago = now - timedelta(days=90)
        repeating_expenses = Expense.objects.filter(
            user=request.user, 
            date__gte=three_months_ago
        ).values('description', 'amount').annotate(
            count=Count('id')
        ).filter(count__gte=3).exclude(
            description__in=['', 'Miscellaneous', 'Other']
        ).exclude(
            description__iendswith=recurring_suffix
        ).order_by('-count')
        
        top_repeat = None
        if repeating_expenses.exists():
            # Optimization: Fetch active recurring descriptions (normalized) for the user to compare
            active_recurring_desc = set(RecurringTransaction.objects.filter(
                user=request.user, is_active=True, transaction_type='EXPENSE'
            ).values_list('description', flat=True))
            
            # Normalize for comparison
            active_recurring_desc_norm = {normalize_recurring_description(d) for d in active_recurring_desc}
            
            for repeat in repeating_expenses[:10]: # Check top 10 candidates
                desc_norm = normalize_recurring_description(repeat['description'])
                if desc_norm not in active_recurring_desc_norm:
                    top_repeat = repeat
                    break

        if top_repeat:
            # link to recurring form with pre-filled description and amount
            recurring_link = f"{reverse('recurring-create')}?description={top_repeat['description']}&amount={top_repeat['amount']}"
            add_nudge_alt(
                _('Automate Repeat Bills?'),
                format_html(
                    _('Looks like {desc} repeats monthly. Want to transition it to a recurring transaction?'),
                    desc=top_repeat['description']
                ),
                n_type='ANALYTICS',
                link=recurring_link,
                slug=f"nudge-recurring-{top_repeat['description']}-{now.year}-{now.month}"
            )

        cache.set(nudge_repeat_cache_key, True, 60 * 60 * 24)

    return render(request, 'home.html', context)

@login_required
def complete_tutorial(request):
    if request.method == 'POST':
        profile = request.user.profile
        profile.has_seen_tutorial = True
        profile.save()
        return JsonResponse({'success': True})
    return JsonResponse({'success': False}, status=400)

class BudgetDashboardView(HtmxPartialTemplateMixin, LoginRequiredMixin, TemplateView):
    """The Budget page. Everything shown comes from one payload (expenses.budget_page). It uses the
    app's unified filter toolbar: the time-period menu picks the month, search and Status narrow the
    categories, Sort orders them. The shell is swapped over htmx; limit edits go through api/budgets/."""
    template_name = 'expenses/budget_dashboard.html'
    htmx_template_name = 'expenses/partials/_budget_dashboard_content.html'

    def get_context_data(self, **kwargs):
        from ..budget_page import build_budget_page
        from ..filters.definitions import BUDGET_FILTERS
        from ..filters.engine import apply_filter_config
        from ..periods import resolve_period

        context = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.localdate()

        # Which month: the filter toolbar's time period (calendar months only), or ?month=&year=
        # (older links and the month tests).
        matched, state = apply_filter_config(Category.objects.filter(user=user), self.request, BUDGET_FILTERS)
        # (a budget month is the calendar month, or the salary cycle when the profile has a salary day)
        from ..budget_cycle import budget_month_of, current_budget_month, previous_month, salary_day_of
        current_key = current_budget_month(user, today)
        if self.request.GET.get('month') or self.request.GET.get('year'):
            month, year = parse_month_year(self.request, today=today)
            month_start = date(year, month, 1)
        else:
            key = state['time_period']
            if key in ('last_month', 'calendar_last_month'):
                month_start = previous_month(current_key)
            elif key == 'custom':
                period = resolve_period(user=user, time_period='custom', start_date=state['start_date'],
                                        end_date=state['end_date'], today=today)
                month_start = budget_month_of(salary_day_of(user), period.end or period.start or today)
            else:
                month_start = current_key
            month, year = month_start.month, month_start.year

        sort_param = state['sort'] if state['sort'] in ('urgent', 'spent', 'limit', 'name') else 'urgent'
        page = build_budget_page(user, month_start, today, sort_param)
        page['unbudgeted_spent'] = page['unbudgeted_spent'] or 0

        # the Smart move banner needs three months of history, so the page asks for it after it paints
        from ..budget_suggestions import may_have_suggestion
        suggestion_wanted = page['phase'] == 'current' and may_have_suggestion(page['categories'])
        if suggestion_wanted:
            cache.set(f'budget_suggestion_rows_{user.id}', page['categories'], 60)   # the banner reuses them

        # search and Status narrow the list; the hero, chips and totals stay about the whole month
        wanted_status = set(state['filters'].get('status', []))
        search = state['search']
        filters_active = bool(search or wanted_status)
        all_rows = page['categories']
        if filters_active:
            visible_ids = set(matched.values_list('id', flat=True)) if search else None
            rows = [r for r in all_rows
                    if (visible_ids is None or r['id'] in visible_ids)
                    and (not wanted_status or r['status'] in wanted_status)]
        else:
            rows = all_rows

        # the status summary doubles as quick filters: one click sets the toolbar's Status filter,
        # a second click on the same one clears it
        only = list(wanted_status) if len(wanted_status) == 1 else []
        counts = page['counts']
        status_tiles = []
        for key, count_key, css, icon, label in (
            ('over', 'over', 'over', 'bi-exclamation-octagon-fill', _('over budget')),
            ('limit', 'at_limit', 'limit', 'bi-exclamation-triangle-fill', _('at limit')),
            ('ontrack', 'on_track', 'ok', 'bi-check-circle-fill', _('on track')),
            ('nolimit', 'no_limit', 'none', 'bi-dash-circle', _('no limit set')),
        ):
            params = self.request.GET.copy()
            params.pop('status', None)
            active = only == [key]
            if not active:
                params['status'] = key
            query = params.urlencode()
            status_tiles.append({
                'key': key, 'count': counts[count_key], 'css': css, 'icon': icon, 'label': label,
                'active': active, 'url': self.request.path + (f'?{query}' if query else ''),
            })

        profile = getattr(user, 'profile', None)
        context.update({
            'page': page,
            'status_tiles': status_tiles,
            'suggestion_url': (reverse('budgets-api-suggestion') + f'?month={month_start:%Y-%m}') if suggestion_wanted else '',
            'phase': page['phase'],
            'filter_config': BUDGET_FILTERS,
            'applied_state': state,
            'filters_active': filters_active,
            'month_start': month_start,
            'cycle_start': date.fromisoformat(page['start']),
            'cycle_end': date.fromisoformat(page['end']),
            'current_month': month,
            'current_year': year,
            'current_sort': sort_param,
            'currency_symbol': profile.currency if profile and profile.currency else '₹',
            'warning_percent': BUDGET_WARNING_PERCENT,
            'can_add_category': profile.can_add_category() if profile else False,
            'new_budget_possible': (profile.can_add_category() if profile else False)
                                   or any(r['limit'] is None for r in all_rows),
            'every_month_allowed': month_start >= current_key,
            'rows': rows,
            'needs_attention': [r for r in rows if r['status'] in ('over', 'limit')],
            'on_track': [r for r in rows if r['status'] == 'ontrack'],
            'no_limit': [r for r in rows if r['status'] == 'nolimit'],
            'untouched_count': sum(1 for r in rows if r['status'] == 'ontrack' and not r['spent']),
            'has_any_limit': page['total'] > 0,
            'today_day': max(1, (today - date.fromisoformat(page['start'])).days + 1),
            'pace_abs': abs(page['pace_delta'] or 0),
            'pace_marker_pct': min(100.0, (page['expected_pace'] or 0) / page['total'] * 100) if page['total'] else 0,
        })
        return context


class YearInReviewView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/year_in_review.html'
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        _, year = parse_month_year(self.request)
        # /year-in-review/<year>/ (the link on Analytics) names the year in the path
        path_year = self.kwargs.get('year')
        if path_year is not None and 2000 <= path_year <= timezone.localdate().year + 5:
            year = path_year
        context['year'] = year
        context['review_data'] = generate_year_in_review_data(self.request.user, year)
        from ..wrapped import build_wrapped
        context['wrapped'] = build_wrapped(self.request.user, year, context['review_data'])
        context['user_first_name'] = (self.request.user.first_name or self.request.user.username or '')[:20]
        return context

    def dispatch(self, request, *args, **kwargs):
        from django.shortcuts import redirect
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
            
        if not request.user.profile.is_plus:
            messages.info(request, _("Year in Review is a Premium feature. Upgrade to Plus or Pro to unlock your personalized financial story!"))
            return redirect('pricing')
        return super().dispatch(request, *args, **kwargs)
