"""Everything the Calendar page shows, computed in one place.

Money is always in the user's own currency (the ``base_amount`` columns), so a day's total matches
what All Transactions lists for that day. Every kind of entry is counted:

    income          Income
    expense         Expense + loan repayments (the whole EMI left your account) + capital events
    investment      Transfers into investment accounts
    pending         scheduled (recurring) entries that have not been posted yet

Scheduled entries are projected with the same rules the posting engine uses (start date, month-end
and last-working-day schedules, end date), so the calendar shows the day each one will really post.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from .account_types import investment_codes
from .models import CapitalEvent, Expense, Income, LoanRepayment, RecurringTransaction, Transfer

MIN_YEAR = 2000
MAX_YEARS_AHEAD = 50
DUE_SOON_DAYS = 3
COMING_UP_DAYS = 45
COMING_UP_LIMIT = 3
MAX_STEPS = 2000          # safety cap on stepping through one schedule

OUT_TYPES = {'EXPENSE', 'INSURANCE_PREMIUM', 'LOAN', 'CAPITAL'}
FIXED_STEP_DAYS = {'DAILY': 1, 'WEEKLY': 7, 'BIWEEKLY': 14}


def clamp_month(year, month, today: date) -> tuple[int, int]:
    """A usable (year, month): anything outside 2000..today+50 years or 1..12 falls back to this month."""
    try:
        year, month = int(year), int(month)
    except (TypeError, ValueError):
        return today.year, today.month
    if not (1 <= month <= 12) or not (MIN_YEAR <= year <= today.year + MAX_YEARS_AHEAD):
        return today.year, today.month
    return year, month


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def _dec(value) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal('0')


# ── Scheduled (pending) entries ──────────────────────────────────────────────────────────────

def _next_unposted(rt: RecurringTransaction) -> date | None:
    """The first occurrence the engine has not posted yet (what it would post next)."""
    if rt.last_processed_date and rt.last_processed_date >= rt.start_date:
        return rt.get_next_date(rt.last_processed_date, rt.frequency, rt.start_date,
                                rt.is_last_day_of_month, rt.is_last_working_day)
    return rt.first_due_date()


def occurrences(rt: RecurringTransaction, start: date, end: date) -> list[date]:
    """Dates in [start, end] on which ``rt`` is due and still unposted."""
    current = _next_unposted(rt)
    if current is None:
        return []
    step = FIXED_STEP_DAYS.get(rt.frequency)
    if step and current < start:
        # fixed-length steps: jump straight to the first one on or after ``start``
        current += timedelta(days=-(-(start - current).days // step) * step)
    found, steps = [], 0
    while current is not None and current <= end and steps < MAX_STEPS:
        if rt.end_date and current > rt.end_date:
            break
        if current >= start:
            found.append(current)
        current = rt.get_next_date(current, rt.frequency, rt.start_date,
                                   rt.is_last_day_of_month, rt.is_last_working_day)
        steps += 1
    return found


def _matches(search: str, *values) -> bool:
    needle = search.lower()
    return any(needle in str(v or '').lower() for v in values)


def pending_by_day(user, start: date, end: date, search: str = '') -> dict[date, list[dict]]:
    """{date: [pending item, ...]} for scheduled entries due in [start, end]."""
    invest = set(investment_codes())
    result: dict[date, list[dict]] = defaultdict(list)
    schedules = (RecurringTransaction.objects
                 .filter(user=user, is_active=True)
                 .select_related('account', 'from_account', 'to_account', 'loan'))
    for rt in schedules:
        # the posting engine switches a schedule off when one of its accounts is inactive
        if any(a is not None and not a.is_active for a in (rt.account, rt.from_account, rt.to_account)):
            continue
        if search and not _matches(search, rt.description, rt.category, rt.source):
            continue
        if rt.transaction_type == 'INCOME':
            kind = 'in'
        elif rt.transaction_type == 'TRANSFER':
            kind = 'invest' if rt.to_account and rt.to_account.account_type in invest else 'transfer'
        else:
            kind = 'out'
        text = (rt.description or '').lower()
        is_salary = kind == 'in' and ('salary' in text or 'salary' in (rt.source or '').lower())
        amount = _dec(rt.base_amount) or _dec(rt.amount)
        for due in occurrences(rt, start, end):
            result[due].append({
                'description': rt.description, 'amount': amount, 'kind': kind,
                'type': rt.transaction_type, 'is_salary': is_salary, 'date': due,
            })
    return result


# ── Month grid ───────────────────────────────────────────────────────────────────────────────

def _by_day(rows) -> dict[int, dict]:
    return {r['date'].day: {'total': _dec(r['total']), 'count': r['count']} for r in rows}


def _merge(*maps) -> dict[int, dict]:
    merged: dict[int, dict] = {}
    for one in maps:
        for day, info in one.items():
            slot = merged.setdefault(day, {'total': Decimal('0'), 'count': 0})
            slot['total'] += info['total']
            slot['count'] += info['count']
    return merged


def _month_filters(user, year, month, search):
    """The querysets for one month, with the search applied the same way everywhere."""
    window = {'date__year': year, 'date__month': month}
    expenses = Expense.objects.filter(user=user, **window)
    incomes = Income.objects.filter(user=user, **window)
    loans = LoanRepayment.objects.filter(loan__user=user, **window)
    capital = CapitalEvent.objects.filter(user=user, **window)
    invest = Transfer.objects.filter(user=user, to_account__account_type__in=list(investment_codes()), **window)
    if search:
        expenses = expenses.filter(Q(description__icontains=search) | Q(category__icontains=search))
        incomes = incomes.filter(Q(source__icontains=search) | Q(description__icontains=search)
                                 | Q(source_type__icontains=search))
        loans = loans.filter(loan__name__icontains=search)
        capital = capital.filter(Q(note__icontains=search) | Q(subtype__icontains=search))
        invest = invest.filter(Q(description__icontains=search) | Q(to_account__name__icontains=search))
    return expenses, incomes, loans, capital, invest


def build_month(user, year: int, month: int, today: date, search: str = '') -> list[list[dict | None]]:
    """Weeks (Sunday first) of day dicts; ``None`` for the blank cells around the month."""
    expenses, incomes, loans, capital, invest = _month_filters(user, year, month, search)
    agg = {'total': Sum('base_amount'), 'count': Count('id')}

    expense_map = _by_day(expenses.values('date').annotate(**agg))
    loan_map = _by_day(loans.values('date').annotate(**agg))
    capital_map = _by_day(capital.values('date').annotate(**agg))
    out_map = _merge(expense_map, loan_map, capital_map)

    income_map = _by_day(incomes.values('date').annotate(**agg))
    invest_map = _by_day(invest.values('date').annotate(total=Sum('converted_amount'), count=Count('id')))

    salary_days = {
        d.day for d in incomes.filter(
            Q(source_type='Salary') | Q(description__icontains='salary') | Q(source__icontains='salary')
        ).values_list('date', flat=True)
    }

    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    pending = pending_by_day(user, first, last, search)

    weeks, cells = [], []
    for week in calendar.Calendar(firstweekday=6).monthdayscalendar(year, month):
        row = []
        for day in week:
            if not day:
                row.append(None)
                continue
            the_date = date(year, month, day)
            income = income_map.get(day, {'total': Decimal('0'), 'count': 0})
            out = out_map.get(day, {'total': Decimal('0'), 'count': 0})
            invested = invest_map.get(day, {'total': Decimal('0'), 'count': 0})
            items = sorted(pending.get(the_date, []), key=lambda p: (p['kind'], p['description'].lower()))
            days_away = (the_date - today).days
            cell = {
                'day': day,
                'date': the_date,
                'iso': the_date.isoformat(),
                'is_today': the_date == today,
                'income': income['total'], 'income_count': income['count'],
                'expense': out['total'], 'expense_count': out['count'],
                'investment': invested['total'], 'investment_count': invested['count'],
                'total_count': income['count'] + out['count'] + invested['count'],
                'total_activity': income['total'] + out['total'] + invested['total'],
                'pending': items,
                'pending_in': sum((p['amount'] for p in items if p['kind'] == 'in'), Decimal('0')),
                'pending_out': sum((p['amount'] for p in items if p['kind'] != 'in'), Decimal('0')),
                'is_salary_day': day in salary_days or any(p['is_salary'] for p in items),
                'due_soon': bool(items) and 0 <= days_away <= DUE_SOON_DAYS,
            }
            row.append(cell)
            cells.append(cell)
        weeks.append(row)

    peak = max((c['total_activity'] for c in cells), default=Decimal('0'))
    for cell in cells:
        ratio = (cell['total_activity'] / peak) if peak > 0 else Decimal('0')
        cell['intensity'] = 0 if ratio == 0 else 1 if ratio <= Decimal('0.25') else 2 if ratio <= Decimal('0.5') \
            else 3 if ratio <= Decimal('0.75') else 4
    return weeks


# ── One day ──────────────────────────────────────────────────────────────────────────────────

def day_detail(user, day: date, today: date, search: str = '') -> dict:
    """The transactions of one day, what is still scheduled for it, and what is coming up next."""
    window = {'date': day}
    expenses = Expense.objects.filter(user=user, **window).select_related('account')
    incomes = Income.objects.filter(user=user, **window).select_related('account')
    loans = LoanRepayment.objects.filter(loan__user=user, **window).select_related('loan', 'from_account')
    capital = CapitalEvent.objects.filter(user=user, **window).select_related('account')
    transfers = Transfer.objects.filter(user=user, **window).select_related('from_account', 'to_account')
    if search:
        expenses = expenses.filter(Q(description__icontains=search) | Q(category__icontains=search))
        incomes = incomes.filter(Q(source__icontains=search) | Q(description__icontains=search)
                                 | Q(source_type__icontains=search))
        loans = loans.filter(loan__name__icontains=search)
        capital = capital.filter(Q(note__icontains=search) | Q(subtype__icontains=search))
        transfers = transfers.filter(Q(description__icontains=search) | Q(to_account__name__icontains=search)
                                     | Q(from_account__name__icontains=search))

    invest = set(investment_codes())
    items = []
    for e in expenses:
        items.append({'kind': 'expense', 'title': e.description or e.category, 'detail': e.category,
                      'account': e.account.name if e.account else '', 'amount': _dec(e.base_amount),
                      'flow': 'out', 'created': e.created_at, 'url_name': 'expense-edit', 'key': e.uuid})
    for i in incomes:
        items.append({'kind': 'income', 'title': i.description or i.source, 'detail': i.source_type,
                      'account': i.account.name if i.account else '', 'amount': _dec(i.base_amount),
                      'flow': 'in', 'created': i.created_at, 'url_name': 'income-edit', 'key': i.uuid})
    for r in loans:
        items.append({'kind': 'loan', 'title': r.loan.name, 'detail': '', 'account':
                      r.from_account.name if r.from_account else '', 'amount': _dec(r.base_amount),
                      'flow': 'out', 'created': r.created_at, 'url_name': 'loan-detail', 'key': r.loan.uuid})
    for c in capital:
        items.append({'kind': 'capital', 'title': c.note or c.get_subtype_display(),
                      'detail': c.get_subtype_display(), 'account': c.account.name if c.account else '',
                      'amount': _dec(c.base_amount), 'flow': 'out', 'created': c.created_at,
                      'url_name': 'capital-event-edit', 'key': c.uuid})
    for t in transfers:
        into_investment = t.to_account.account_type in invest
        items.append({'kind': 'invest' if into_investment else 'transfer',
                      'title': t.description or f'{t.from_account.name} → {t.to_account.name}',
                      'detail': '', 'account': f'{t.from_account.name} → {t.to_account.name}',
                      'amount': _dec(t.converted_amount), 'flow': 'invest' if into_investment else 'move',
                      'created': t.created_at, 'url_name': 'transfer-edit', 'key': t.uuid})
    items.sort(key=lambda x: x['created'])

    scheduled = pending_by_day(user, day, day + timedelta(days=COMING_UP_DAYS), search)
    coming = []
    for due in sorted(d for d in scheduled if d > day)[:COMING_UP_LIMIT]:
        group = scheduled[due]
        names = [p['description'] for p in group]
        coming.append({
            'date': due,
            'title': names[0] if len(names) == 1 else f"{', '.join(names[:2])}" + (f' +{len(names) - 2} more' if len(names) > 2 else ''),
            'amount': sum((p['amount'] for p in group), Decimal('0')),
            'kind': group[0]['kind'] if len({p['kind'] for p in group}) == 1 else 'out',
        })

    return {
        'date': day,
        'is_today': day == today,
        'items': items,
        'pending': scheduled.get(day, []),
        'coming_up': coming,
        'income': sum((i['amount'] for i in items if i['flow'] == 'in'), Decimal('0')),
        'outflow': sum((i['amount'] for i in items if i['flow'] == 'out'), Decimal('0')),
        'invested': sum((i['amount'] for i in items if i['flow'] == 'invest'), Decimal('0')),
    }
