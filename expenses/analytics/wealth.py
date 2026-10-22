"""The Net worth tab: how net worth moved over the period, what it is made of, and what is creeping up."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from ..account_types import ACCOUNT_TYPES
from ..insights.types import Tone
from ..models import Expense
from ..utils import translate_digits
from .common import Page
from .links import EXPENSE
from .money import percent
from .networth import GROUPS, MAX_POINTS, OTHER_KEY, group_labels, net_worth_points
from .periods import buckets, sample

ZERO = Decimal('0')
CREEP_ROWS = 3


def history_start(page: Page) -> date | None:
    """The earliest day the user's plan lets them look back to, or None when it is unlimited."""
    profile = getattr(page.user, 'profile', None)
    months = getattr(profile, 'net_worth_history_limit', -1)
    if months is None or months == -1:
        return None
    first = page.today.replace(day=1)
    index = first.year * 12 + first.month - 1 - (months - 1)
    return date(index // 12, index % 12 + 1, 1)


def _codes(*group_names: str) -> list[str]:
    return [code for name, choices in ACCOUNT_TYPES if name in group_names for code, _display in choices]


def series(page: Page, start: date) -> dict:
    """Net worth at the end of up to MAX_POINTS steps between ``start`` and the end of the period."""
    period, money = page.period, page.money
    steps = buckets(start, period.end, period.granularity)
    steps = sample(steps, MAX_POINTS)
    points = net_worth_points(page.user, [step.end for step in steps], page.today)
    labels = group_labels()
    keys = [key for key, _title, _names in GROUPS] + [OTHER_KEY]
    present = [k for k in keys if any(p.groups.get(k, ZERO) != 0 for p in points)]
    cta = _('See what changed')
    tips, previous_day = [], None
    for point in points:
        lines = [{'k': _('Net worth'), 'v': money.full(point.total), 'c': 'income'}]
        for key in present:
            value = point.groups.get(key, ZERO)
            if value:
                lines.append({'k': labels[key], 'v': money.full(value), 'c': f'g_{key}'})
        range_start = previous_day + timedelta(days=1) if previous_day else start
        tips.append({'title': translate_digits(date_format(point.day, 'j M Y')), 'lines': lines, 'cta': cta,
                     'href': page.links.transactions(range_start, point.day)})
        previous_day = point.day
    return {
        'labels': [translate_digits(date_format(p.day, 'j M' if period.days <= 400 else "M 'y")) for p in points],
        'total': [float(p.total) for p in points],
        'groups': [{'key': f'g_{k}', 'label': labels[k], 'data': [float(p.groups.get(k, ZERO)) for p in points]} for k in present],
        'tips': tips,
        'first': points[0] if points else None, 'last': points[-1] if points else None,
    }


def allocation(last) -> dict:
    """Money sitting in bank accounts against money that is invested, as of the end of the period."""
    if last is None:
        return {'rows': []}
    idle, growing = last.idle, last.growing
    total = idle + growing
    if total <= 0:
        return {'rows': []}
    return {'idle_share': float(idle / total * 100), 'growing_share': float(growing / total * 100),
            'idle': idle, 'growing': growing}


def _legacy_rising(page: Page) -> list[dict]:
    """Categories whose monthly spending rose three complete calendar months in a row, fastest first. Used while the
    Insights Engine is off for the person; with it on the rule is `expenses.categories_creeping_up`."""
    first_of_month = page.today.replace(day=1)
    months = []
    for back in (3, 2, 1):
        index = first_of_month.year * 12 + first_of_month.month - 1 - back
        months.append(date(index // 12, index % 12 + 1, 1))
    rows = (Expense.objects.filter(user=page.user, date__gte=months[0], date__lt=first_of_month)
            .annotate(m=TruncMonth('date')).values('m', 'category').annotate(total=Sum('base_amount')))
    by_category = {}
    for row in rows:
        m = row['m'].date() if hasattr(row['m'], 'date') else row['m']
        by_category.setdefault(row['category'], {})[m] = row['total'] or ZERO
    out = []
    for name, values in by_category.items():
        e1, e2, e3 = (values.get(m, ZERO) for m in months)
        if e1 > 0 and e2 > e1 and e3 > e2:
            growth = ((e2 - e1) / e1 + (e3 - e2) / e2) / 2
            out.append((name, growth))
    out.sort(key=lambda item: item[1], reverse=True)
    start, end = months[0], first_of_month - timedelta(days=1)
    return [{'name': _(name), 'pct': percent(growth * 100), 'tone': 'bad' if growth >= Decimal('0.1') else 'warn',
             'href': page.links.transactions(start, end, (EXPENSE,), category=name)}
            for name, growth in out[:CREEP_ROWS]]


def rising_categories(page: Page) -> list[dict]:
    """Categories whose spending rose three complete budget months in a row, fastest first: with the Insights Engine
    on, the `expenses.categories_creeping_up` rule (cycle-aware, and following the toolbar's filters)."""
    from ..insights import engine, is_engine_on
    from ..insights.pagectx import PageContext
    if not is_engine_on(page.user):
        return _legacy_rising(page)
    return [{'name': i.evidence[0].label, 'pct': i.evidence[0].value, 'href': i.evidence[0].link,
             'tone': 'bad' if i.tone == Tone.GENTLE_ATTENTION else 'warn'}
            for i in engine.inline(PageContext(page), ['expenses.categories_creeping_up'])]


def moved(page: Page, start, change_amount) -> str:
    """The Net worth tab's sentence about what moved net worth (engine on only; the rule says nothing while filters apply)."""
    from ..insights import engine, is_engine_on
    from ..insights.pagectx import PageContext
    if not is_engine_on(page.user) or page.filters_active:
        return ''
    kept = page.facts.summarize(start, page.period.end).saved
    found = engine.inline(PageContext(page, change=change_amount, kept=kept), ['networth.what_moved_period'], limit=1)
    return found[0].message if found else ''


def build(page: Page) -> dict:
    limit_start = history_start(page)
    start = page.period.start
    limited = limit_start is not None and start < limit_start
    if limited:
        start = min(limit_start, page.period.end)
    s = series(page, start)
    first, last = s['first'], s['last']
    change_amount = (last.total - first.total) if first and last else ZERO
    alloc = allocation(last)
    money = page.money
    rows = []
    if alloc.get('idle_share') is not None:
        rows = [
            {'label': _('Idle in bank accounts'), 'amount': money.full(alloc['idle']), 'share': alloc['idle_share'],
             'share_text': percent(alloc['idle_share']), 'tone': 'budget',
             'href': page.links.accounts(_codes('Cash & Bank') + ['CASH', 'BANK'])},
            {'label': _('Growing (invested)'), 'amount': money.full(alloc['growing']), 'share': alloc['growing_share'],
             'share_text': percent(alloc['growing_share']), 'tone': 'income',
             'href': page.links.accounts(_codes('Fixed-Income', 'Investments') + ['INVESTMENT', 'FIXED_DEPOSIT'])},
        ]
    return {
        'series': {k: v for k, v in s.items() if k not in ('first', 'last')},
        'net_worth': money.full(last.total) if last else money.full(0),
        'change': {'text': ('▲ ' if change_amount >= 0 else '▼ ') + money.full(abs(change_amount)),
                   'tone': 'good' if change_amount >= 0 else 'bad'},
        'allocation': rows,
        'rising': rising_categories(page),
        'moved': moved(page, start, change_amount),
        'limited': limited,
        'limit_months': getattr(getattr(page.user, 'profile', None), 'net_worth_history_limit', -1),
        'shown_from': page.range_text(start, start) if limited else '',
        'has_data': bool(last and any(v for v in s['total'])),
        'filters_note': page.filters_active,
    }
