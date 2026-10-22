"""Synthetic contexts, so every rule can be run without a database.

Each rule's `sample` builds one of these. The tone linter, the translation test and the link test then run every
rule in English, Hindi and Marathi without any fixture of their own. `scenario()` is a plausible person in the
middle of October 2026 on whom all the first rules have something to say; `overrides` bend it for a rule that needs
a different situation.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from ..analytics.facts import Facts
from ..budgets import budget_status, normalize_category, percent_used
from .context import InsightContext
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
             bills=None, **overrides) -> SampleContext:
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
    return scenario(language=language, budgets=[('Transport', 5000, 4000), ('Dining', 40000, 22000)])
