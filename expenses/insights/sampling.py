"""Synthetic contexts, so every rule can be run without a database.

Each rule's `sample` builds one of these. The tone linter, the translation test and the link test then run every
rule in English, Hindi and Marathi without any fixture of their own. `scenario()` is a plausible person in the
middle of October 2026 on whom all the first rules have something to say; `overrides` bend it for a rule that needs
a different situation.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.utils import timezone

from ..analytics.facts import ExpenseRow, Facts
from ..budgets import budget_status, normalize_category, percent_used
from .context import BudgetHistory, InsightContext
from .format import Fmt
from .types import Plan

D = Decimal
TODAY = date(2026, 10, 22)


class SampleContext(InsightContext):
    """An InsightContext whose loaders were filled in by hand."""

    def __init__(self, *, today: date = TODAY, salary_day: int = 1, currency: str = '₹', language: str = 'en',
                 plan: Plan = Plan.PRO, **loaded):
        from .. import budget_cycle
        from ..analytics.links import LinkBuilder
        self.user = None
        self.profile = SimpleNamespace(currency=currency, language=language)
        self.today = today
        self.language = language
        self.fmt = Fmt(currency)
        self.plan = plan
        self.salary_day = salary_day
        self.key = budget_cycle.budget_month_of(salary_day, today)
        self.links = LinkBuilder()
        self._cache = dict(loaded)
        self._allowed = None


def _fill(facts: Facts, day: date, *, income=0, category=None, amount=0):
    i = facts.index(day)
    if income:
        facts.income[i] += D(income)
    if category:
        key = normalize_category(category)
        facts.names.setdefault(key, category)
        facts._add(facts.expense_cats, i, key, D(amount))


def budget_rows(rows) -> list[dict]:
    out = []
    for name, limit, spent in rows:
        limit, spent = D(limit), D(spent)
        pct = percent_used(spent, limit)
        out.append({
            'id': len(out) + 1, 'uuid': str(uuid.uuid5(uuid.NAMESPACE_URL, name)), 'name': name, 'icon': None,
            'limit': float(limit), 'spent': float(spent), 'remaining': float(max(limit - spent, 0)),
            'over_by': float(max(spent - limit, 0)), 'percent': round(pct, 1), 'status': budget_status(spent, limit),
        })
    return out


def scenario(*, budgets: list[tuple] | None = None, salary_day: int = 1, language: str = 'en', maturing=None,
             bills=None, history_spend=None, recent=(), rows=(), dismissed=(), **overrides) -> SampleContext:
    """Six months of steady life, then an October that runs a little higher: dining is up, income had a bonus."""
    today = overrides.pop('today', TODAY)
    ctx = SampleContext(today=today, salary_day=salary_day, language=language, **overrides)
    start = ctx.cycle_of(ctx.keys_back(5)[-1])[0]
    start = min(start, ctx.cycle_of(date(ctx.key.year, 1, 1))[0])
    facts = Facts(first=start, last=ctx.cycle[1])
    keys = [date(ctx.key.year, m, 1) for m in range(1, ctx.key.month)]
    for key in keys:
        first, last = ctx.cycle_of(key)
        _fill(facts, first, income=60000)
        _fill(facts, first, category='Rent', amount=20000)
        _fill(facts, first, category='Dining', amount=9000)
        _fill(facts, first, category='Groceries', amount=12000)
        if key == ctx.keys_back(1)[0]:
            _fill(facts, first, category='Travel', amount=9000)
    first, _last = ctx.cycle
    _fill(facts, first, income=70000)
    _fill(facts, first, category='Rent', amount=20000)
    _fill(facts, first, category='Groceries', amount=12000)
    _fill(facts, first, category='Dining', amount=22000)
    ctx._cache['facts'] = facts
    ctx._cache['budgets'] = {
        'phase': 'current', 'start': first.isoformat(), 'end': ctx.cycle[1].isoformat(), 'days_left': 9,
        'categories': budget_rows(budgets if budgets is not None else [
            ('Groceries', 10000, 12000), ('Dining', 30000, 22000), ('Travel', 8000, 0)]),
    }
    rows_ = ctx._cache['budgets']['categories']
    months = sorted(ctx.keys_back(3))
    ctx._cache['budget_history'] = BudgetHistory(
        months=months,
        limits={m: {normalize_category(r['name']): D(str(r['limit'])) for r in rows_ if r['limit']} for m in months},
        spend={m: {normalize_category(r['name']): D(str((history_spend or {}).get(r['name'], r['limit'] * 0.5)))
                   for r in rows_ if r['limit']} for m in months})
    ctx._cache['dismissed_suggestions'] = frozenset(dismissed)
    ctx._cache['recent'] = list(recent)
    ctx._cache['rows'] = list(rows)
    ctx._cache['accounts'] = list(maturing) if maturing is not None else [SimpleNamespace(
        uuid=uuid.uuid5(uuid.NAMESPACE_URL, 'fd'), name='Bank FD', currency='₹', balance=D('210000'),
        deposit_maturity_date=today + timedelta(days=12))]
    ctx._cache['recurring'] = list(bills) if bills is not None else [
        SimpleNamespace(description='Netflix', amount=D('499'), base_amount=D('499'), currency='₹',
                        next_due_date=today + timedelta(days=2)),
        SimpleNamespace(description='Home loan EMI', amount=D('18200'), base_amount=D('18200'), currency='₹',
                        next_due_date=today + timedelta(days=4))]
    return ctx


def default_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language)


def over_limit_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language)


def near_limit_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, budgets=[('Dining', 25000, 22000), ('Groceries', 20000, 12000)])


def runout_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, budgets=[('Transport', 5000, 4000), ('Dining', 40000, 22000)],
                    history_spend={'Transport': 3000})


def often_over_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, budgets=[('Dining', 8000, 6000)], history_spend={'Dining': 9000})


def often_under_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, budgets=[('Travel', 8000, 100)], history_spend={'Travel': 3000})


def room_to_share_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, budgets=[('Dining', 10000, 10000), ('Travel', 8000, 100)],
                    history_spend={'Dining': 9000, 'Travel': 200})


def _row(id, day, category, amount, account=1, description=None, made=None, batch=False):
    made = made or timezone.make_aware(datetime(day.year, day.month, day.day, 12, 0))
    return ExpenseRow(id, day, category, D(amount), account, made, description or category, batch)


def duplicate_sample(language: str = 'en') -> SampleContext:
    day = TODAY - timedelta(days=1)
    first = timezone.make_aware(datetime(day.year, day.month, day.day, 12, 0))
    return scenario(language=language, recent=[
        _row(101, day, 'Entertainment', 499, description='Netflix', made=first),
        _row(102, day, 'Entertainment', 499, description='Netflix', made=first + timedelta(minutes=4))])


def unusual_sample(language: str = 'en') -> SampleContext:
    prior = [_row(i, TODAY - timedelta(days=20 + 9 * i), 'Groceries', 1400 + 40 * (i % 4)) for i in range(1, 13)]
    return scenario(language=language, rows=prior + [_row(200, TODAY - timedelta(days=2), 'Groceries', 6400)])


def weekday_sample(language: str = 'en') -> SampleContext:
    ctx = scenario(language=language)
    facts = ctx.facts
    for back in range(1, 71):         # everyday spending with no monthly lumps: a heavier Saturday, a lighter rest
        day = TODAY - timedelta(days=back)
        facts.expense_cats[facts.index(day)] = {'dining': D(2000 if day.weekday() == 5 else 700)}
    ctx._cache.pop('summaries', None)
    return ctx
