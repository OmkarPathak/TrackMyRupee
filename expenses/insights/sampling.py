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

from ..analytics.common import Page
from ..analytics.facts import ExpenseRow, Facts, IncomeRow, LoanPaid
from ..analytics.money import Money
from ..budgets import budget_status, normalize_category, percent_used
from .context import BudgetHistory, GoalFacts, HoldingValue, InsightContext
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
             bills=None, history_spend=None, recent=(), rows=(), dismissed=(), recurring_all=None, postings=None,
             pending=None, cash=None, cards=(), loans=(), accounts_all=(), reconciliations=None, income_rows=(),
             goals=(), holdings=(), networth=None, **overrides) -> SampleContext:
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
    ctx._cache['inventory'] = {'loans': bool(loans), 'cards': bool(cards), 'recurring': True, 'goals': bool(goals),
                               'holdings': bool(holdings)}
    ctx._cache['income_rows'] = list(income_rows)
    ctx._cache['goals'] = list(goals)
    ctx._cache['holdings'] = list(holdings)
    ctx._cache['networth'] = networth or (SimpleNamespace(total=D(0)), SimpleNamespace(total=D(0)))
    ctx._cache['recurring_all'] = list(recurring_all) if recurring_all is not None else [
        _recurring('Rent', 'EXPENSE', 20000), _recurring('Home loan EMI', 'LOAN', 18200), _recurring('Netflix', 'EXPENSE', 499)]
    ctx._cache['postings'] = dict(postings or {})
    ctx._cache['pending'] = dict(pending or {})
    ctx._cache['cash'] = cash or SimpleNamespace(idle=D('150000'))
    ctx._cache['cards'] = list(cards)
    ctx._cache['loans'] = list(loans)
    ctx._cache['accounts_all'] = list(accounts_all)
    ctx._cache['reconciliations'] = dict(reconciliations or {})
    ctx._cache['loan_paid'] = LoanPaid(D('84000'), D('160000'), 6)
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


def _recurring(description, kind, amount, frequency='MONTHLY'):
    return SimpleNamespace(uuid=uuid.uuid5(uuid.NAMESPACE_URL, description), description=description,
                           transaction_type=kind, frequency=frequency, amount=D(amount), base_amount=D(amount))


def price_change_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, postings={'Netflix (Recurring)': [
        (TODAY - timedelta(days=35), D('499')), (TODAY - timedelta(days=4), D('649'))]})


def looks_recurring_sample(language: str = 'en') -> SampleContext:
    gym = [_row(300 + i, TODAY - timedelta(days=days), 'Health', 1200, description='Gym fee')
           for i, days in enumerate((70, 40, 10))]
    return scenario(language=language, rows=gym)


class _Rates:
    def __init__(self, *rates):
        self._rates = rates

    def all(self):
        return list(self._rates)


def _loan(name, initial, remaining, rate, months=240, started=40, extra_rates=()):
    start = TODAY - timedelta(days=started * 30)
    paid = D(initial) - D(remaining)
    loan = SimpleNamespace(
        uuid=uuid.uuid5(uuid.NAMESPACE_URL, name), name=name, currency='₹', initial_principal=D(initial),
        paid_principal=paid, capital_prepaid=D(0), opening_paid_principal=D(0), duration_months=months, start_date=start,
        repayment_type='EMI', interest_rates=_Rates(SimpleNamespace(effective_date=start, interest_rate=D(rate)),
                                                     *extra_rates))
    loan._cached_summary = {'remaining_principal': float(remaining)}
    return loan


def loans_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, loans=[_loan('Home Loan', 5000000, 4200000, '8.5'),
                                              _loan('Car Loan', 800000, 390000, '9.9', months=60, started=30)])


def cards_sample(language: str = 'en') -> SampleContext:
    from ..models import Account
    card = Account(id=7, name='Travel Card', account_type='CREDIT_CARD', currency='₹', credit_limit=D('80000'),
                   credit_card_billing_day=(TODAY + timedelta(days=6)).day)
    return scenario(language=language, cards=[(card, D('50000'))])


def idle_cash_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, cash=SimpleNamespace(idle=D('420000')))


def reconcile_sample(language: str = 'en') -> SampleContext:
    from ..models import Account
    account = Account(id=5, name='Savings Account', account_type='SAVINGS_ACCOUNT', currency='₹')
    return scenario(language=language, accounts_all=[account], reconciliations={5: TODAY - timedelta(days=74)})


def low_point_sample(language: str = 'en') -> SampleContext:
    payday = TODAY + timedelta(days=14)

    def item(day, kind, amount, salary=False):
        return {'description': 'x', 'amount': D(amount), 'kind': kind, 'type': 'EXPENSE', 'is_salary': salary, 'date': day}
    pending = {TODAY + timedelta(days=2): [item(TODAY + timedelta(days=2), 'out', 499)],
               TODAY + timedelta(days=6): [item(TODAY + timedelta(days=6), 'out', 18200)],
               payday: [item(payday, 'in', 60000, salary=True)]}
    rows = [_row(400 + i, TODAY - timedelta(days=i), 'Food', 700) for i in range(1, 28)]
    return scenario(language=language, pending=pending, rows=rows, cash=SimpleNamespace(idle=D('30000')))


# ---- Phase D: income, goals, transfers and investing, holdings, capital events, calendar, net worth ----

def _income(day, amount, kind='Salary'):
    return IncomeRow(day, kind, D(amount))


def income_sample(language: str = 'en') -> SampleContext:
    rows = [_income(date(2026, m, 1), 60000) for m in (7, 8, 9)]
    rows += [_income(date(2026, 7, 9), 6000, 'Freelance / Consulting'), _income(date(2026, 8, 14), 21000, 'Freelance / Consulting')]
    return scenario(language=language, income_rows=sorted(rows))


def salary_late_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, income_rows=[_income(date(2026, 8, 15), 60000), _income(date(2026, 9, 15), 60000)])


def income_gap_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, income_rows=[_income(date(2026, m, 1), 60000) for m in (4, 5, 6, 7)])


def _goal(name, target, contributions, *, today=TODAY, target_date=None):
    """A GoalFacts for a goal called `name` whose contributions are [(days ago, amount)]."""
    rows = sorted((today - timedelta(days=ago), D(amount)) for ago, amount in contributions)
    saved = sum((a for _d, a in rows), D(0))
    goal = SimpleNamespace(
        pk=1, uuid=uuid.uuid5(uuid.NAMESPACE_URL, name), name=name, currency='₹', target_amount=D(target),
        current_amount=saved, target_date=target_date, is_completed=saved >= D(target),
        progress_percentage=round(float(saved / D(target) * 100), 1))
    return GoalFacts(goal, rows, False)


def goals_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, goals=[_goal('Goa trip', 100000, [(100, 20000), (90, 5000), (60, 5000), (30, 5000), (3, 5000)])])


def goal_stalled_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, goals=[_goal('Goa trip', 40000, [(100, 5000), (80, 5000), (60, 5000)])])


def goal_milestone_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, goals=[_goal('Goa trip', 100000, [(60, 30000), (5, 22000)])])


def _put(facts: Facts, day: date, *, invested=0, extra=0, extra_name=None):
    i = facts.index(day)
    if invested:
        facts.invested[i] += D(invested)
    if extra:
        key = normalize_category(extra_name)
        facts.names.setdefault(key, extra_name)
        facts.capital_extra[i] += D(extra)
        facts._add(facts.capital_cats_extra, i, key, D(extra))


def invested_share_sample(language: str = 'en') -> SampleContext:
    ctx = scenario(language=language)
    for m in range(1, ctx.key.month):
        _put(ctx.facts, ctx.cycle_of(date(ctx.key.year, m, 1))[0], invested=9000)
    _put(ctx.facts, ctx.cycle[0], invested=4900)
    ctx._cache.pop('summaries', None)
    return ctx


def sip_missed_sample(language: str = 'en') -> SampleContext:
    from ..account_types import investment_codes
    start = TODAY - timedelta(days=17)
    sip = SimpleNamespace(
        uuid=uuid.uuid5(uuid.NAMESPACE_URL, 'sip'), description='Nifty fund SIP', transaction_type='TRANSFER', frequency='MONTHLY',
        amount=D(5000), base_amount=D(5000), end_date=None, start_date=start, last_processed_date=None,
        to_account=SimpleNamespace(account_type=sorted(investment_codes())[0], is_active=True), first_due_date=lambda: start)
    return scenario(language=language, recurring_all=[sip])


def concentration_sample(language: str = 'en') -> SampleContext:
    def held(name, kind, value):
        return HoldingValue(SimpleNamespace(instrument_name=name, instrument_type=kind), D(value))
    return scenario(language=language, holdings=[held('Nifty fund', 'MF', 400000), held('Index fund', 'MF', 150000),
                                                  held('Tata Motors', 'STOCK', 50000)])


def rate_effect_sample(language: str = 'en') -> SampleContext:
    ctx = scenario(language=language)
    _put(ctx.facts, ctx.cycle[0], extra=12000, extra_name='Roof repair')
    ctx._cache.pop('summaries', None)
    return ctx


def heavy_week_sample(language: str = 'en') -> SampleContext:
    ctx = scenario(language=language)
    for back in range(1, 71):         # a plain 4,900 a week of everyday spending
        day = TODAY - timedelta(days=back)
        ctx.facts.expense_cats[ctx.facts.index(day)] = {'dining': D(700)}
    ctx._cache.pop('summaries', None)

    def item(day, amount):
        return {'description': 'x', 'amount': D(amount), 'kind': 'out', 'type': 'EXPENSE', 'is_salary': False, 'date': day}
    ctx._cache['pending'] = {TODAY + timedelta(days=2): [item(TODAY + timedelta(days=2), 6000)],
                             TODAY + timedelta(days=5): [item(TODAY + timedelta(days=5), 4000)]}
    return ctx


def net_worth_sample(language: str = 'en') -> SampleContext:
    return scenario(language=language, networth=(SimpleNamespace(total=D('900000')), SimpleNamespace(total=D('926000'))))


# ---- Phase E: the inline sentences on the Analytics tabs ----

class SamplePage:
    """What an inline rule reads from an Analytics Page, built from a scenario: the cycle against the one before."""

    def __init__(self, ctx, *, compare=True):
        self.facts = ctx.facts
        self.period = SimpleNamespace(range=ctx.cycle, compare=ctx.cycle_of(ctx.keys_back(1)[0]), compare_kind='before')
        self.summary = ctx.facts.summarize(*self.period.range)
        self.compare = ctx.facts.summarize(*self.period.compare) if compare else None
        self.money = Money(ctx.fmt.symbol)
        self.links = ctx.links
        self.categories, self.account_ids, self.filters_active = [], [], False

    def category_name(self, key):
        return self.facts.names.get(key, key)

    compare_name = Page.compare_name          # the real wording, from the Analytics Page itself


def _with_page(ctx, **values):
    ctx._cache['page'] = SamplePage(ctx)
    ctx.values = values
    return ctx


def page_sample(language: str = 'en') -> SampleContext:
    return _with_page(scenario(language=language))


def page_overspent_sample(language: str = 'en') -> SampleContext:
    ctx = scenario(language=language)
    _fill(ctx.facts, ctx.cycle[0], category='Rent', amount=30000)
    ctx._cache.pop('summaries', None)
    return _with_page(ctx)


def creeping_sample(language: str = 'en') -> SampleContext:
    ctx = _with_page(scenario(language=language))
    ranges = [ctx.cycle_of(key) for key in reversed(ctx.keys_back(3))]
    ctx._cache['category_cycles'] = (ranges, {'Food': [D(100), D(150), D(200)], 'Rent': [D(100), D(90), D(200)]})
    return ctx


def net_worth_period_sample(language: str = 'en') -> SampleContext:
    return _with_page(scenario(language=language), change=D('26000'), kept=D('16000'))
