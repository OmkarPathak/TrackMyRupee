"""The Spending tab: income against spending, where it went, how it adds up, and what is growing."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.utils.formats import date_format
from django.utils.translation import gettext as _

from ..utils import translate_digits
from .common import Page
from .links import CAPITAL_EVENT, EXPENSE
from .money import change, percent
from .periods import sample

ZERO = Decimal('0')
TOP_CATEGORIES = 6
TOP_TREND = 5
CUMULATIVE_POINTS = 120


def _delta(current, previous, higher_is_good: bool):
    pct, direction = change(current, previous)
    if pct is None:
        return {'text': '', 'tone': 'flat'}
    arrow = '▲' if direction == 'up' else '▼' if direction == 'down' else '•'
    tone = 'flat' if direction == 'flat' else ('good' if (direction == 'up') == higher_is_good else 'bad')
    return {'text': f'{arrow} {percent(abs(pct))}', 'tone': tone}


def in_out_chart(page: Page) -> dict:
    """Income and spending per bar. Income lands in lumps (a monthly salary), so short bars show spending only."""
    lumpy = page.period.granularity in ('day', 'week')
    labels, income, spending, tips = [], [], [], []
    money, cta = page.money, _('View transactions')
    for bucket in page.bucket_list():
        s = page.facts.summarize(bucket.start, bucket.end)
        labels.append(page.bucket_label(bucket))
        income.append(float(s.income))
        spending.append(float(s.spending))
        lines = [{'k': _('Spending'), 'v': money.full(s.spending), 'c': 'spend'}]
        if not lumpy:
            lines = [{'k': _('Income'), 'v': money.full(s.income), 'c': 'income'}, lines[0],
                     {'k': _('Kept'), 'v': money.full(s.saved), 'c': 'muted'}]
        tips.append({'title': page.bucket_label(bucket, long=True), 'lines': lines, 'cta': cta,
                     'href': page.link_for(bucket)})
    return {'labels': labels, 'income': None if lumpy else income, 'spending': spending, 'tips': tips,
            'granularity': page.period.granularity}


def category_rows(page: Page) -> list[dict]:
    """Top categories by spending, each with its share, its change against the comparison, and a link."""
    now, before = page.summary, page.compare
    ranked = sorted(now.categories.items(), key=lambda item: item[1], reverse=True)
    total = sum((amount for _key, amount in ranked), ZERO) or Decimal(1)
    start, end = page.period.range
    rows = []
    for key, amount in ranked[:TOP_CATEGORIES]:
        delta = _delta(amount, before.categories.get(key, ZERO) if before else None, False) if before else \
            {'text': '', 'tone': 'flat'}
        name = page.category_name(key)
        rows.append({'name': name, 'amount': page.money.full(amount), 'share': float(amount / total * 100),
                     'share_text': percent(amount / total * 100), 'delta': delta,
                     'href': page.links.transactions(start, end, (EXPENSE, CAPITAL_EVENT),
                                                     category=page.facts.names.get(key, key))})
    rest = sum((amount for _key, amount in ranked[TOP_CATEGORIES:]), ZERO)
    if rest > 0:
        rows.append({'name': _('Everything else'), 'amount': page.money.full(rest), 'share': float(rest / total * 100),
                     'share_text': percent(rest / total * 100), 'delta': {'text': '', 'tone': 'flat'},
                     'href': page.links.transactions(start, end, (EXPENSE, CAPITAL_EVENT)), 'rest': True})
    biggest = max((row['share'] for row in rows), default=0) or 1
    for row in rows:
        row['width'] = row['share'] / biggest * 100
    return rows


def cumulative_chart(page: Page) -> dict:
    """Spending adding up day by day, against the comparison period lined up by day (and the budget's pace)."""
    period, facts, money = page.period, page.facts, page.money

    def running(values):
        total, out = ZERO, []
        for value in values:
            total += value
            out.append(total)
        return out

    days = [period.start + timedelta(days=i) for i in range(period.days)]
    current = running(facts.daily_spending(period.start, period.end))
    previous = running(facts.daily_spending(*period.compare)) if period.compare else []
    if previous and previous[-1] == 0:
        previous = []                              # nothing was spent then, so there is nothing to compare with
    budget_total = _budget_total(page) if period.key == 'this_month' else ZERO
    pace = _budget_pace(page, budget_total) if budget_total > 0 else []

    picked = sample(list(range(len(days))), CUMULATIVE_POINTS)
    cta, tips = _('View transactions'), []
    for i in picked:
        lines = [{'k': _('Spent so far'), 'v': money.full(current[i]), 'c': 'income'}]
        if i < len(previous):
            lines.append({'k': _('Comparison'), 'v': money.full(previous[i]), 'c': 'muted'})
        if i < len(pace):
            lines.append({'k': _('Budget pace'), 'v': money.full(pace[i]), 'c': 'budget'})
        tips.append({'title': page.range_text(days[i], days[i]), 'lines': lines, 'cta': cta,
                     'href': page.links.transactions(days[i], days[i])})

    def pick(series):
        return [float(series[i]) if i < len(series) else None for i in picked]

    gap = None
    if current and previous:
        at = min(len(current), len(previous)) - 1
        if previous[at] > 0:                       # nothing to say against a comparison with no spending in it
            gap = current[at] - previous[at]
    return {'labels': [translate_digits(date_format(days[i], 'j M')) for i in picked],
            'current': pick(current), 'compare': pick(previous) if previous else None,
            'budget': pick(pace) if pace else None, 'budget_total': money.full(budget_total) if pace else '',
            'tips': tips,
            'spent': money.full(current[-1] if current else 0), 'days': period.days,
            'gap': {'amount': money.full(abs(gap)), 'less': gap <= 0} if gap is not None else None}


def _budget_total(page: Page) -> Decimal:
    """The month's budget: the limits of every category that has one."""
    from ..budget_resolver import plan_allowed_categories, total_budget

    month_start = page.period.start.replace(day=1)
    return Decimal(str(total_budget(page.user, month_start, plan_allowed_categories(page.user)) or 0))


def _budget_pace(page: Page, budget: Decimal) -> list[Decimal]:
    """The budget spread evenly over the days of the cycle, as a running total."""
    full_days = page.period.full_days
    return [budget / full_days * (i + 1) for i in range(page.period.days)]


def top_category_chart(page: Page) -> dict:
    """The biggest categories, bar by bar, to show what is rising."""
    top = sorted(page.summary.categories.items(), key=lambda item: item[1], reverse=True)[:TOP_TREND]
    keys = [key for key, _amount in top]
    buckets = page.bucket_list()
    series = [[float(page.facts.category_series(b.start, b.end, key)) for b in buckets] for key in keys]
    cta = _('View transactions')
    tips = []
    for i, bucket in enumerate(buckets):
        tips.append({'title': page.bucket_label(bucket, long=True),
                     'lines': [{'k': page.category_name(key), 'v': page.money.full(series[n][i]), 'c': f'cat{n}'}
                               for n, key in enumerate(keys)],
                     'cta': cta, 'href': page.link_for(bucket, (EXPENSE, CAPITAL_EVENT))})
    return {'labels': [page.bucket_label(b) for b in buckets], 'names': [page.category_name(k) for k in keys],
            'series': series, 'tips': tips}


def build(page: Page) -> dict:
    return {
        'tiles': page.tiles(),
        'insight': page.insight(),
        'in_out': in_out_chart(page),
        'categories': category_rows(page),
        'cumulative': cumulative_chart(page),
        'trend': top_category_chart(page),
        'has_spending': page.summary.spending > 0,
    }
