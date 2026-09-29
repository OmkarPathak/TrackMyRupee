import base64
import calendar
import csv
import io
import json
import re
import traceback
from datetime import date, datetime
from decimal import Decimal

import dns.flags
import dns.message
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import openpyxl
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Q, Sum, When
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

import logging

from ..account_types import investment_codes
from ..forms import ContactForm
from ..models import (
    CURRENCY_CHOICES,
    Account,
    Expense,
    Income,
    RecurringTransaction,
    Transfer,
)
from ..posthog_utils import ph_capture
from .mixins import HtmxPartialTemplateMixin

logger = logging.getLogger(__name__)

# Iteration safety cap; should stay consistent with MAX_CATCHUP_PER_RUN in expenses/views/mixins.py
MAX_CATCHUP_PER_RUN = 100


class CalendarView(HtmxPartialTemplateMixin, LoginRequiredMixin, TemplateView):
    template_name = 'expenses/calendar.html'
    htmx_template_name = 'expenses/partials/_calendar_content.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = datetime.now()
        
        # Get year/month from URL or default to current
        year = self.kwargs.get('year', today.year)
        month = self.kwargs.get('month', today.month)
        
        # Validate year/month
        try:
            year = int(year)
            month = int(month)
            if month < 1 or month > 12:
                raise ValueError
        except ValueError:
            year = today.year
            month = today.month

        # Calculate prev/next month for navigation
        if month == 1:
            prev_month_date = date(year - 1, 12, 1)
        else:
            prev_month_date = date(year, month - 1, 1)
            
        if month == 12:
            next_month_date = date(year + 1, 1, 1)
        else:
            next_month_date = date(year, month + 1, 1)

        # Get search query
        search_query = self.request.GET.get('search', '')

        # Base filters
        expense_filters = Q(user=self.request.user, date__year=year, date__month=month)
        income_filters = Q(user=self.request.user, date__year=year, date__month=month)
        
        if search_query:
            # Filter expenses by description or category
            expense_filters &= (Q(description__icontains=search_query) | Q(category__icontains=search_query))
            # Filter income by source or description
            income_filters &= (Q(source__icontains=search_query) | Q(description__icontains=search_query))

        # Get Expense Data for the month
        expenses = Expense.objects.filter(expense_filters).values('date').annotate(
            total=Sum('base_amount'),
            count=Count('id')
        )
        
        # Get Income Data for the month via DB aggregation
        income_qs = Income.objects.filter(income_filters).values('date').annotate(
            total=Sum('base_amount'),
            count=Count('id'),
            salary_count=Count(Case(
                When(Q(source_type='Salary') | Q(description__icontains='salary') | Q(source__icontains='salary'), then=1),
                default=None
            ))
        )
        from collections import defaultdict
        income_map = defaultdict(lambda: {'total': 0, 'count': 0, 'has_salary': False})
        for item in income_qs:
            day = item['date'].day
            income_map[day] = {
                'total': item['total'] or 0,
                'count': item['count'],
                'has_salary': (item['salary_count'] or 0) > 0
            }

        # Get investment data (Transfers to investment/FD accounts)
        investment_filters = Q(user=self.request.user, date__year=year, date__month=month, to_account__account_type__in=list(investment_codes()))
        if search_query:
            investment_filters &= (Q(description__icontains=search_query) | Q(to_account__name__icontains=search_query))
        
        investments = Transfer.objects.filter(investment_filters).values('date').annotate(
            total=Sum('converted_amount'),
            count=Count('id')
        )
        
        # Map data for easy lookup by day
        # Keys are integers (day of month)
        expense_map = {e['date'].day: {'total': e['total'], 'count': e['count']} for e in expenses}
        investment_map = {inv['date'].day: {'total': inv['total'], 'count': inv['count']} for inv in investments}
        
        # Calculate average daily expense for non-zero expense days
        expense_days = [float(e['total']) for e in expenses if float(e['total']) > 0]
        avg_expense = sum(expense_days) / len(expense_days) if expense_days else 0
        
        # Get pending recurring transactions for the month
        pending_recurring_map = defaultdict(list)
        recurring_configs = RecurringTransaction.objects.filter(user=self.request.user, is_active=True)
        
        view_month_start = date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        view_month_end = date(year, month, last_day)
        
        for rt in recurring_configs:
            check_date = rt.next_due_date
            if not check_date:
                continue
            
            # Project forward if the next due date is before the viewing month
            forward_iterations = 0
            while check_date < view_month_start:
                if forward_iterations >= MAX_CATCHUP_PER_RUN:
                    logger.warning(
                        "RecurringTransaction %s exceeded iteration cap (%s) during forward projection to %s.",
                        rt.id, MAX_CATCHUP_PER_RUN, view_month_start,
                    )
                    break
                if rt.end_date and check_date > rt.end_date:
                    break
                check_date = rt.get_next_date(check_date, rt.frequency)
                forward_iterations += 1
            
            # Collect all occurrences within the month
            month_iterations = 0
            while check_date <= view_month_end:
                if month_iterations >= MAX_CATCHUP_PER_RUN:
                    logger.warning(
                        "RecurringTransaction %s exceeded iteration cap (%s) during monthly occurrence collection for %s-%s.",
                        rt.id, MAX_CATCHUP_PER_RUN, year, month,
                    )
                    break
                if rt.end_date and check_date > rt.end_date:
                    break
                # Only show if not yet processed
                if check_date > (rt.last_processed_date or date(1900, 1, 1)):
                    pending_recurring_map[check_date.day].append({
                        'description': rt.description,
                        'amount': float(rt.amount),
                        'type': rt.transaction_type,
                        'currency': rt.currency
                    })
                check_date = rt.get_next_date(check_date, rt.frequency)
                month_iterations += 1

        # Build Calendar Grid
        cal = calendar.Calendar(firstweekday=6) # Start on Sunday
        month_days = cal.monthdayscalendar(year, month)
        
        # Transform into a list of weeks, where each day is an object
        calendar_data = []
        for week in month_days:
            week_data = []
            for day in week:
                if day == 0:
                    week_data.append(None) # Empty slot
                else:
                    expense_info = expense_map.get(day, {'total': 0, 'count': 0})
                    income_info = income_map.get(day, {'total': 0, 'count': 0, 'has_salary': False})
                    investment_info = investment_map.get(day, {'total': 0, 'count': 0})
                    
                    total_activity = float(income_info['total'] or 0) + float(expense_info['total'] or 0) + float(investment_info['total'] or 0)
                    pending_recurring = pending_recurring_map.get(day, [])
                    pending_total = sum(pr['amount'] for pr in pending_recurring)
                    
                    # Highlight salary day
                    has_pending_salary = any(pr['type'] == 'INCOME' and 'salary' in pr['description'].lower() for pr in pending_recurring)
                    is_salary_day = income_info['has_salary'] or has_pending_salary
                    
                    # Add is_high_spend flag
                    is_high_spend = False
                    if expense_info['total'] > 0:
                        if avg_expense > 0:
                            is_high_spend = float(expense_info['total']) >= avg_expense and float(expense_info['total']) >= 500
                        else:
                            is_high_spend = float(expense_info['total']) >= 500
                            
                    # Check if this day is a subscription date with a 3-day warning
                    subscription_warning = False
                    if pending_recurring:
                        try:
                            cell_date = date(year, month, day)
                            today_date = date.today()
                            if 0 <= (cell_date - today_date).days <= 3:
                                subscription_warning = True
                        except ValueError:
                            pass
                    
                    week_data.append({
                        'day': day,
                        'income': income_info['total'],
                        'income_count': income_info['count'],
                        'expense': expense_info['total'],
                        'expense_count': expense_info['count'],
                        'investment': investment_info['total'],
                        'investment_count': investment_info['count'],
                        'total_count': income_info['count'] + expense_info['count'] + investment_info['count'],
                        'total_activity': total_activity,
                        'pending_recurring': pending_recurring,
                        'pending_total': pending_total,
                        'is_salary_day': is_salary_day,
                        'is_high_spend': is_high_spend,
                        'subscription_warning': subscription_warning
                    })
            calendar_data.append(week_data)
        
        # Find max activity for heatmap normalization
        all_activities = [d['total_activity'] for week in calendar_data for d in week if d]
        max_activity = max(all_activities) if all_activities else 0
        
        # Assign intensity (0-4)
        for week in calendar_data:
            for day_data in week:
                if day_data:
                    if max_activity > 0:
                        ratio = day_data['total_activity'] / max_activity
                        if ratio == 0: intensity = 0
                        elif ratio <= 0.25: intensity = 1
                        elif ratio <= 0.5: intensity = 2
                        elif ratio <= 0.75: intensity = 3
                        else: intensity = 4
                    else:
                        intensity = 0
                    day_data['intensity'] = intensity

        context['calendar_data'] = calendar_data
        context['current_year'] = year
        context['current_month'] = month
        context['month_name'] = date_format(date(year, month, 1), 'F')
        context['prev_year'] = prev_month_date.year
        context['prev_month'] = prev_month_date.month
        context['next_year'] = next_month_date.year
        context['next_month'] = next_month_date.month
        context['search_query'] = search_query
        
        return context

@login_required
def upload_view(request):
    """
    Robust expense upload view supporting Excel and CSV.
    """
    results = None
    
    accounts_qs = Account.objects.filter(user=request.user, is_active=True).order_by('name')

    if request.method == 'POST' and request.FILES.get('file'):
        uploaded_file = request.FILES['file']
        selected_currency = request.POST.get('currency', request.user.profile.currency)
        selected_account_id = request.POST.get('account')

        selected_account = None
        if not selected_account_id and accounts_qs.exists():
            selected_account_id = str(accounts_qs.first().id)

        if not selected_account_id:
            messages.error(request, _("Please create an account before importing expenses."))
            return render(request, 'upload.html', {
                'results': None,
                'currencies': CURRENCY_CHOICES,
                'default_currency': request.user.profile.currency,
                'accounts': accounts_qs,
                'selected_account_id': None,
            })

        try:
            selected_account = accounts_qs.get(pk=selected_account_id)
        except Account.DoesNotExist:
            messages.error(request, _("Selected account is invalid. Please choose one of your active accounts."))
            return render(request, 'upload.html', {
                'results': None,
                'currencies': CURRENCY_CHOICES,
                'default_currency': request.user.profile.currency,
                'accounts': accounts_qs,
                'selected_account_id': None,
            })
        
        from . import predict_category_ai

        summary = {
            'total_rows': 0,
            'created_count': 0,
            'duplicate_count': 0,
            'error_count': 0,
            'errors': [], # Detail log
            'total_amount': 0,
            'currency_symbol': selected_currency
        }

        def get_column_mapping(headers):
            """Intelligently map header names to target fields."""
            patterns = {
                'date': ['date', 'time', 'day', 'transaction', 'txn', 'dated'],
                'amount': ['amount', 'total', 'cost', 'price', 'value', 'debit', 'spent', 'withdraw'],
                'description': ['description', 'details', 'memo', 'remarks', 'particulars', 'narration', 'payee', 'merchant'],
                'category': ['category', 'type', 'tag', 'label', 'expense type']
            }
            mapping = {}
            header_l = [str(h).lower().strip() for h in headers if h is not None]
            
            for field, keywords in patterns.items():
                for idx, h in enumerate(header_l):
                    if field == 'amount' and any(d_kw in h for d_kw in ['date', 'dt']):
                        # Avoid mapping columns like "Value Dt" or "Value Date" to "amount"
                        continue
                    if any(kw in h for kw in keywords):
                        mapping[field] = idx
                        break
            return mapping

        def parse_robust_date(val):
            if isinstance(val, (date, datetime)):
                return val.date() if isinstance(val, datetime) else val
            
            if not val or not str(val).strip():
                return None
            
            date_str = str(val).strip()
            formats = [
                '%d %b %Y', '%Y-%m-%d', '%d-%m-%Y', '%d/%m/%Y', '%m/%d/%Y', 
                '%d %B %Y', '%d %b', '%d-%b', '%d %B', '%d/%m', '%Y/%m/%d',
                '%b %d, %Y', '%B %d, %Y', '%b %d', '%B %d',
                '%d/%m/%y', '%m/%d/%y', '%d-%m-%y', '%y/%m/%d'
            ]
            for fmt in formats:
                try:
                    parsed = datetime.strptime(date_str, fmt).date()
                    # If year is not in format, strptime defaults to 1900
                    if parsed.year == 1900:
                        return parsed.replace(year=datetime.now().year)
                    return parsed
                except ValueError:
                    continue
            return None

        def infer_column_mapping(rows):
            """Try to guess columns based on data types in the first non-empty row."""
            mapping = {}
            for row in rows:
                if not row or not any(row): continue
                
                # We need at least 2 or 3 columns to guess
                cols = [str(c).strip() if c is not None else "" for c in row]
                
                # 1. Identify Date
                for i, val in enumerate(cols):
                    if parse_robust_date(val):
                        mapping['date'] = i
                        break
                
                # 2. Identify Amount (looking for numbers in other columns)
                for i, val in enumerate(cols):
                    if i == mapping.get('date'): continue
                    if parse_robust_date(val):
                        # Avoid identifying other date columns as amount
                        continue
                    # Remove symbols and try to parse
                    try:
                        clean_v = re.sub(r'[^\d\.\-]', '', val)
                        if clean_v and float(clean_v):
                            mapping['amount'] = i
                            break
                    except (ValueError, TypeError): continue
                
                # 3. Identify Description (longest remaining non-empty string)
                best_desc_idx = -1
                max_len = -1
                for i, val in enumerate(cols):
                    if i in mapping.values(): continue
                    if len(val) > max_len:
                        max_len = len(val)
                        best_desc_idx = i
                
                if best_desc_idx != -1:
                    mapping['description'] = best_desc_idx
                
                if len(mapping) >= 2: # Success if we have at least Date and Amount
                    return mapping
            return {}

        try:
            data_rows = []
            if uploaded_file.name.endswith(('.xlsx', '.xls')):
                sheets_data = []
                try:
                    uploaded_file.seek(0)
                    wb = openpyxl.load_workbook(uploaded_file, data_only=True)
                    for sheet in wb.worksheets:
                        rows = list(sheet.iter_rows(values_only=True))
                        if rows:
                            sheets_data.append(rows)
                except Exception:
                    # Fallback for files that have .xls extension but are actually HTML tables or CSVs
                    uploaded_file.seek(0)
                    file_bytes = uploaded_file.read()
                    
                    decoded = None
                    for encoding in ['utf-8', 'latin-1', 'cp1252']:
                        try:
                            decoded = file_bytes.decode(encoding)
                            break
                        except UnicodeDecodeError:
                            continue
                    
                    if decoded:
                        decoded_stripped = decoded.strip()
                        # Detect if HTML table
                        if '<table' in decoded_stripped.lower() or '<html' in decoded_stripped.lower():
                            try:
                                from bs4 import BeautifulSoup
                                soup = BeautifulSoup(decoded, 'html.parser')
                                for table in soup.find_all('table'):
                                    rows = []
                                    for tr in table.find_all('tr'):
                                        cells = []
                                        for td in tr.find_all(['td', 'th']):
                                            cells.append(td.get_text(strip=True))
                                        if any(cells):
                                            rows.append(cells)
                                    if rows:
                                        sheets_data.append(rows)
                            except Exception as html_err:
                                raise ValueError(_("Failed to parse XLS as HTML table: ") + str(html_err))
                        else:
                            # Try parsing as CSV
                            try:
                                reader = csv.reader(io.StringIO(decoded))
                                rows = list(reader)
                                if rows:
                                    sheets_data.append(rows)
                            except Exception as csv_err:
                                raise ValueError(_("Failed to parse XLS as CSV: ") + str(csv_err))
                    
                    if not sheets_data:
                        raise ValueError(_(
                            "This file appears to be in the older Excel (.xls) format, which is not supported directly. "
                            "Please open it in Microsoft Excel, Google Sheets, or LibreOffice and save it as a newer .xlsx format, "
                            "then try uploading again."
                        ))

                for rows in sheets_data:
                    mapping = {}
                    start_idx = 0
                    for i, row in enumerate(rows[:20]):
                        if not row or not any(row): continue
                        temp_mapping = get_column_mapping(row)
                        if len(temp_mapping) >= 3:
                            mapping = temp_mapping
                            start_idx = i + 1
                            break
                    
                    if not mapping:
                        mapping = infer_column_mapping(rows[:20])
                        start_idx = 0
                    
                    if mapping:
                        for idx, row in enumerate(rows[start_idx:], start=start_idx + 1):
                            if row and any(v is not None for v in row):
                                data_rows.append((row, mapping, idx))

            elif uploaded_file.name.endswith('.csv'):
                uploaded_file.seek(0)
                content = uploaded_file.read()
                decoded = None
                for encoding in ['utf-8', 'latin-1', 'cp1252']:
                    try:
                        decoded = content.decode(encoding)
                        break
                    except UnicodeDecodeError: continue
                
                if decoded:
                    reader = csv.reader(io.StringIO(decoded))
                    rows = list(reader)
                    if rows:
                        mapping = {}
                        start_idx = 0
                        for i, row in enumerate(rows[:20]):
                            if not row or not any(row): continue
                            temp_mapping = get_column_mapping(row)
                            if len(temp_mapping) >= 3:
                                mapping = temp_mapping
                                start_idx = i + 1
                                break
                        if not mapping:
                            mapping = infer_column_mapping(rows[:20])
                            start_idx = 0

                        if mapping:
                            for idx, row in enumerate(rows[start_idx:], start=start_idx + 1):
                                if row and any(v and str(v).strip() for v in row):
                                    data_rows.append((row, mapping, idx))

            if not data_rows:
                messages.warning(request, _("Could not detect required columns (Date, Amount, Description). Please check your file."))
            else:
                import hashlib
                from collections import defaultdict
                from django.conf import settings

                # 1. Pre-fetch existing dedup keys
                existing_keys = set(Expense.objects.filter(user=request.user).values_list('client_dedup_key', flat=True))
                seen_in_file_keys = set()

                # 2. Pre-fetch description-to-category history map once in a single bulk query
                history_records = (
                    Expense.objects.filter(user=request.user)
                    .values('description', 'category')
                    .annotate(cnt=Count('id'))
                    .order_by('-cnt')
                )
                exact_counts = defaultdict(lambda: defaultdict(int))
                prefix_counts = defaultdict(lambda: defaultdict(int))
                for r in history_records:
                    desc_val = (r['description'] or '').strip()
                    cat_val = r['category']
                    cnt = r['cnt']
                    if desc_val and cat_val:
                        desc_lower = desc_val.lower()
                        exact_counts[desc_lower][cat_val] += cnt
                        words = desc_lower.split()
                        if words and len(words[0]) > 3:
                            prefix_counts[words[0]][cat_val] += cnt

                exact_map = {d: max(cats.items(), key=lambda x: x[1])[0] for d, cats in exact_counts.items()}
                prefix_map = {w: max(cats.items(), key=lambda x: x[1])[0] for w, cats in prefix_counts.items()}
                history_map = {'exact': exact_map, 'prefix': prefix_map}

                # Precompute currency conversion rates
                base_currency = request.user.profile.currency
                if selected_currency == base_currency:
                    exchange_rate = Decimal('1.0')
                else:
                    from ..models import get_exchange_rate
                    exchange_rate = get_exchange_rate(selected_currency, base_currency)

                account_rate = Decimal('1.0')
                if selected_account and selected_currency != selected_account.currency:
                    from ..models import get_exchange_rate
                    account_rate = get_exchange_rate(selected_currency, selected_account.currency)

                batch = []
                BATCH_SIZE = 200

                def flush_batch(batch_items):
                    if not batch_items:
                        return
                    try:
                        with transaction.atomic():
                            created_expenses = Expense.objects.bulk_create(batch_items)
                            summary['created_count'] += len(created_expenses)
                            summary['total_amount'] += sum(float(e.amount) for e in created_expenses)

                            if selected_account:
                                total_deduct = sum(
                                    (e.amount * account_rate).quantize(Decimal('0.01')) if account_rate != Decimal('1.0') else e.amount
                                    for e in created_expenses
                                )
                                locked_account = Account.objects.select_for_update().get(pk=selected_account.id)
                                locked_account.balance -= total_deduct
                                locked_account.save(update_fields=['balance', 'updated_at'])

                            if getattr(settings, 'LEDGER_WRITE_ENABLED', False) and selected_account:
                                from ..models import _run_ledger_shadow, _build_ledger_version
                                from ..ledger_service import LedgerPostingService

                                for exp in created_expenses:
                                    def _post_shadow_entry(e=exp):
                                        version_token = _build_ledger_version(e, 'CREATE')
                                        LedgerPostingService.shadow_post_expense_create(
                                            expense=e,
                                            version_token=version_token,
                                        )
                                    _run_ledger_shadow(
                                        _post_shadow_entry,
                                        source_type='EXPENSE',
                                        source_id=exp.id,
                                        action='CREATE',
                                        payload={
                                            'handler': 'expense_create',
                                            'version_token': _build_ledger_version(exp, 'CREATE'),
                                            'expense': {
                                                'user_id': exp.user_id,
                                                'amount': str(exp.amount),
                                                'currency': exp.currency,
                                                'category': exp.category,
                                                'description': exp.description,
                                                'account_id': exp.account_id,
                                                'source_id': exp.id,
                                            },
                                            'previous_expense': None,
                                        },
                                    )
                    except IntegrityError:
                        # Edge-case fallback for individual item insertion if batch conflicts
                        for item in batch_items:
                            try:
                                with transaction.atomic():
                                    item.save()
                                    summary['created_count'] += 1
                                    summary['total_amount'] += float(item.amount)
                            except IntegrityError:
                                summary['duplicate_count'] += 1
                            except Exception as item_err:
                                if len(summary['errors']) < 15:
                                    summary['errors'].append({'row': getattr(item, '_row_idx', '?'), 'reason': str(item_err)})
                                summary['error_count'] += 1
                    except Exception as batch_err:
                        for item in batch_items:
                            if len(summary['errors']) < 15:
                                summary['errors'].append({'row': getattr(item, '_row_idx', '?'), 'reason': str(batch_err)})
                            summary['error_count'] += 1

                for row, mapping, row_idx in data_rows:
                    summary['total_rows'] += 1
                    try:
                        # Extract and Parse
                        date_idx = mapping.get('date')
                        amount_idx = mapping.get('amount')
                        desc_idx = mapping.get('description')
                        
                        if date_idx is None or amount_idx is None or desc_idx is None:
                            raise ValueError(_("Missing required columns in row"))

                        raw_amount = row[amount_idx]
                        if raw_amount is None or str(raw_amount).strip() in ('', '0', '0.0', '0.00'):
                            # Skip silently - it's a deposit or empty row, not an expense
                            continue

                        date_val = parse_robust_date(row[date_idx])
                        if not date_val:
                            raise ValueError(_("Invalid date format: ") + str(row[date_idx]))

                        amount_str = str(raw_amount).replace(',', '').strip()
                        # Handle cases like "$ 1,200.00" or "(100.00)"
                        cleaned_amount_str = re.sub(r'[^\d\.\-]', '', amount_str)
                        if not cleaned_amount_str:
                            raise ValueError(_("Invalid amount format: ") + str(raw_amount))
                        amount = abs(Decimal(cleaned_amount_str))
                        
                        desc = str(row[desc_idx]).strip() if row[desc_idx] is not None else ""
                        if not desc:
                            raise ValueError(_("Missing description"))

                        # Category Logic
                        category_name = None
                        cat_idx = mapping.get('category')
                        if cat_idx is not None and row[cat_idx]:
                            category_name = str(row[cat_idx]).strip()
                        
                        if not category_name:
                            category_name = predict_category_ai(desc, user=request.user, skip_genai=True, history_map=history_map) or 'Food'

                        # Dedup check in Python
                        raw_str = f"{date_val}_{amount}_{selected_currency}_{desc}_{category_name}"
                        dedup_key = hashlib.md5(raw_str.encode('utf-8')).hexdigest()

                        if dedup_key in existing_keys or dedup_key in seen_in_file_keys:
                            summary['duplicate_count'] += 1
                            continue

                        seen_in_file_keys.add(dedup_key)

                        if selected_currency == base_currency:
                            base_amount = amount
                        else:
                            base_amount = (amount * exchange_rate).quantize(Decimal('0.01'))

                        expense_item = Expense(
                            user=request.user,
                            date=date_val,
                            amount=amount,
                            base_amount=base_amount,
                            exchange_rate=exchange_rate,
                            description=desc,
                            category=category_name,
                            currency=selected_currency,
                            account=selected_account,
                            client_dedup_key=dedup_key,
                        )
                        expense_item._row_idx = row_idx
                        batch.append(expense_item)

                        if len(batch) >= BATCH_SIZE:
                            flush_batch(batch)
                            batch = []

                    except Exception as e:
                        if len(summary['errors']) < 15:
                            summary['errors'].append({'row': row_idx, 'reason': str(e)})
                        summary['error_count'] += 1

                if batch:
                    flush_batch(batch)
                    batch = []

                if summary['created_count'] > 0:
                    cache.delete(f"filter_merchants:{request.user.id}")
                    cache.delete(f"filter_categories:{request.user.id}")

                results = summary
                if summary['created_count'] > 0:
                    messages.success(request, _("Processing complete! See summary below."))
                    ph_capture(request.user, 'expenses_imported', {
                        'created_count': summary['created_count'],
                        'duplicate_count': summary['duplicate_count'],
                        'error_count': summary['error_count'],
                        'total_rows': summary['total_rows'],
                    })
                else:
                    messages.info(request, _("Processing complete. No new expenses were added."))


        except Exception as e:
            traceback.print_exc()
            messages.error(request, f"Error processing file: {e}")

    return render(request, 'upload.html', {
        'results': results,
        'currencies': CURRENCY_CHOICES,
        'default_currency': request.user.profile.currency,
        'accounts': accounts_qs,
        'selected_account_id': request.POST.get('account') if request.method == 'POST' else None,
    })


def ping(request):
    return JsonResponse({'status': 'ok'})

@login_required
def predict_category_view(request):
    # Important: use the one from the package to allow mocking in tests
    from . import predict_category_ai
    
    if not request.user.profile.has_ai_access:
        return JsonResponse({'category': None, 'error': _('AI Insights is a paid feature.')}, status=403)

    description = request.GET.get('description', '').strip()
    if not description:
        return JsonResponse({'category': None})
    
    category = predict_category_ai(description, user=request.user) or 'Food'
    return JsonResponse({'success': True, 'category': category})

class ContactView(View):
    template_name = 'contact.html'
    RATE_LIMIT_HOURLY = 3
    RATE_LIMIT_DAILY = 10
    MIN_MESSAGE_LENGTH = 10
    SPAM_KEYWORDS = ['viagra', 'casino', 'lottery', 'prize', 'make money fast']
    DISPOSABLE_DOMAINS = ['tempmail.com', 'guerrillamail.com']

    def get(self, request):
        form = ContactForm()
        return render(request, self.template_name, {'form': form})

    def post(self, request):
        form = ContactForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        
        data = form.cleaned_data
        if data.get('website'): # Honeypot
            messages.success(request, _("Your message has been sent!"))
            return redirect('contact')
            
        ip = self._get_client_ip(request)
        allowed, rate_limit_msg = self._check_rate_limit(ip)
        if not allowed:
            messages.error(request, _(rate_limit_msg or "Submission limit reached."))
            return render(request, self.template_name, {'form': form})

        is_spam, spam_msg = self._is_spam_content(data['message'])
        if is_spam:
            messages.error(request, _(spam_msg or "Your message was flagged as potential spam."))
            return render(request, self.template_name, {'form': form})

        if self._is_disposable_email(data['email']):
            messages.error(request, _("Please use a permanent email address."))
            return render(request, self.template_name, {'form': form})

        messages.success(request, _("Your message has been sent! We'll get back to you shortly."))
        return redirect('contact')

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def _check_rate_limit(self, ip):
        hourly_key = f'contact_hourly_{ip}'
        daily_key = f'contact_daily_{ip}'
        
        hourly_count = cache.get(hourly_key, 0)
        daily_count = cache.get(daily_key, 0)
        
        if hourly_count >= getattr(self, 'RATE_LIMIT_HOURLY', 5):
            return False, "Too many submissions. Please try again in an hour."
        
        if daily_count >= getattr(self, 'RATE_LIMIT_DAILY', 20):
            return False, "Daily submission limit reached. Please try again tomorrow."
        
        cache.set(hourly_key, hourly_count + 1, 3600)  # 1 hour
        cache.set(daily_key, daily_count + 1, 86400)   # 24 hours
        
        return True, None

    def _is_spam_content(self, text):
        text_lower = text.lower()
        if 'http://' in text_lower or 'https://' in text_lower or 'www.' in text_lower:
            return True, "Messages with URLs are not allowed."
        
        spam_keywords = getattr(self, 'SPAM_KEYWORDS', ['seo', 'marketing', 'guarantee', 'crypto', 'bitcoin'])
        for keyword in spam_keywords:
            if keyword in text_lower:
                return True, "Your message was flagged as potential spam."
        
        if len(text) > 20:
            caps_count = sum(1 for c in text if c.isupper())
            if caps_count / len(text) > 0.5:
                return True, "Please don't use excessive capitalization."
        
        if len(text.strip()) < getattr(self, 'MIN_MESSAGE_LENGTH', 10):
            return True, "Please provide a more detailed message."
        
        return False, None

    def _is_disposable_email(self, email):
        domain = email.split('@')[-1].lower()
        return domain in getattr(self, 'DISPOSABLE_DOMAINS', ['mailinator.com', '10minutemail.com', 'tempmail.com'])

class HealthCheckView(View):
    def get(self, request):
        return JsonResponse({"status": "healthy", "timestamp": datetime.now().isoformat()})


@login_required
def dpdp_consent_view(request):
    """
    Standalone DPDPA consent page for users who haven't granted consent yet.
    """
    try:
        profile = request.user.profile
    except Exception:
        from ..models import UserProfile
        profile, created = UserProfile.objects.get_or_create(user=request.user)

    if profile.consent_granted:
        return redirect('home')

    if request.method == 'POST':
        consent_email = request.POST.get('consent_email') == 'on'
        consent_transactions = request.POST.get('consent_transactions') == 'on'
        consent_device = request.POST.get('consent_device') == 'on'

        if consent_email and consent_transactions and consent_device:
            from django.utils import timezone
            profile.consent_granted = True
            profile.consent_timestamp = timezone.now()
            profile.consent_version = 'v1.0'
            profile.save()

            from ..models import ConsentEvent
            ConsentEvent.objects.create(
                user=request.user,
                action='GRANTED',
                purpose='Terms and Data Collection',
                consent_version='v1.0',
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )

            messages.success(request, _("Thank you for your consent. You can now use TrackMyRupee."))
            return redirect('home')
        else:
            messages.error(request, _("You must agree to all data collection terms to use TrackMyRupee."))

    return render(request, 'expenses/dpdp_consent.html', {
        'page_title': _('Data Consent Required')
    })


@csrf_exempt
def doh_handler_view(request):
    """
    DNS-over-HTTPS (DoH) handler supporting both RFC 8484 binary wire format
    and JSON DNS formats.
    """
    query_bytes = None
    is_json = False
    data = {}

    if request.method == 'GET':
        if 'dns' in request.GET:
            dns_param = request.GET.get('dns', '')
            # Add base64url padding
            padding_len = len(dns_param) % 4
            if padding_len:
                dns_param += '=' * (4 - padding_len)
            try:
                # Use urlsafe_b64decode to decode base64url
                query_bytes = base64.urlsafe_b64decode(dns_param)
            except Exception:
                return HttpResponse("Invalid base64 in dns parameter", status=400)
        elif 'name' in request.GET:
            is_json = True
        else:
            return HttpResponse("Missing dns or name parameter", status=400)
    elif request.method == 'POST':
        # Default to binary body if request has content-type 'application/dns-message'
        # or doesn't start with json curly brace
        body = request.body
        if body.startswith(b'{'):
            try:
                data = json.loads(body.decode('utf-8'))
                if 'name' in data:
                    is_json = True
            except Exception:
                pass
        
        if not is_json:
            query_bytes = body
    else:
        return HttpResponse("Method Not Allowed", status=405)

    if is_json:
        name = request.GET.get('name', '') if request.method == 'GET' else data.get('name', '')
        qtype_str = request.GET.get('type', 'SVCB') if request.method == 'GET' else data.get('type', 'SVCB')

        try:
            if qtype_str.isdigit():
                qtype_val = int(qtype_str)
                qtype = qtype_val
            else:
                qtype = dns.rdatatype.from_text(qtype_str.upper())
        except Exception:
            qtype = dns.rdatatype.SVCB

        # Format name properly for lookup
        name_lower = name.lower().strip()
        if not name_lower.endswith('.'):
            name_lower += '.'

        answers = []
        labels = name_lower.rstrip('.').split('.')

        is_dns_aid = False
        domain = ""
        if '_agents' in labels:
            idx = labels.index('_agents')
            if idx > 0 and labels[idx - 1].startswith('_'):
                is_dns_aid = True
                domain = '.'.join(labels[idx + 1:])

        if is_dns_aid:
            # We return SVCB/HTTPS parameters
            types_to_return = []
            try:
                target_rdtype = dns.rdatatype.from_text(qtype_str.upper()) if not qtype_str.isdigit() else int(qtype_str)
            except Exception:
                target_rdtype = dns.rdatatype.SVCB

            if target_rdtype == dns.rdatatype.SVCB or target_rdtype == 255:
                types_to_return.append((64, 'SVCB'))
            if target_rdtype == dns.rdatatype.HTTPS or target_rdtype == 255:
                types_to_return.append((65, 'HTTPS'))

            for rdtype_val, rdtype_name in types_to_return:
                target_host = domain if domain.endswith('.') else f"{domain}."
                answers.append({
                    "name": name,
                    "type": rdtype_val,
                    "TTL": 3600,
                    "data": f"1 {target_host} mandatory=alpn,port alpn=a2a port=443"
                })

        response_data = {
            "Status": 0,  # NOERROR
            "TC": False,
            "RD": True,
            "RA": True,
            "AD": True,   # Authenticated Data (DNSSEC validated)
            "CD": False,
            "Question": [
                {
                    "name": name,
                    "type": qtype
                }
            ],
            "Answer": answers
        }
        return JsonResponse(response_data, content_type='application/dns-json')

    if query_bytes:
        try:
            query_msg = dns.message.from_wire(query_bytes)
        except Exception as e:
            return HttpResponse(f"Invalid DNS binary message: {e}", status=400)

        response_msg = dns.message.make_response(query_msg)
        response_msg.flags |= dns.flags.AD

        for question in query_msg.question:
            qname = question.name
            qtype = question.rdtype
            qclass = question.rdclass

            qname_str = qname.to_text().lower().rstrip('.')
            labels = qname_str.split('.')

            is_dns_aid = False
            domain = ""
            if '_agents' in labels:
                idx = labels.index('_agents')
                if idx > 0 and labels[idx - 1].startswith('_'):
                    is_dns_aid = True
                    domain = '.'.join(labels[idx + 1:])

            if is_dns_aid:
                types_to_return = []
                if qtype == dns.rdatatype.SVCB or qtype == 255:
                    types_to_return.append(dns.rdatatype.SVCB)
                if qtype == dns.rdatatype.HTTPS or qtype == 255:
                    types_to_return.append(dns.rdatatype.HTTPS)

                target_host = domain if domain.endswith('.') else f"{domain}."
                for rdtype in types_to_return:
                    rrset = response_msg.find_rrset(
                        response_msg.answer,
                        qname,
                        qclass,
                        rdtype,
                        create=True
                    )
                    rdata_text = f"1 {target_host} mandatory=alpn,port alpn=a2a port=443"
                    rdata = dns.rdata.from_text(qclass, rdtype, rdata_text)
                    rrset.add(rdata, ttl=3600)

        response_wire = response_msg.to_wire()
        return HttpResponse(response_wire, content_type='application/dns-message')

    return HttpResponse("Bad Request", status=400)

