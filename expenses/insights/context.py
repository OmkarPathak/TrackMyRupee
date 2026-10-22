"""The InsightContext: everything a rule may read, loaded lazily and once.

Rules never query the database. They read `ctx.facts` (the app's daily money facts), `ctx.summary(key)` (a budget
month through the savings definition), `ctx.budgets` (the Budget page payload), `ctx.maturing` and `ctx.bills_due`.
Each loader must be declared in the rule's `needs`; reading an undeclared one raises, so a rule's query cost is
visible in its decorator.

A "cycle" here is the app's budget month (budget_cycle.py): the calendar month, or the salary cycle when the
person has a salary day. A cycle is named by `key`, the 1st of the month it belongs to.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from django.utils import timezone
from django.utils.translation import get_language

from .. import budget_cycle
from ..analytics.facts import ExpenseRow, Facts, Summary, load_expense_rows, load_facts
from ..analytics.links import LinkBuilder
from .format import Fmt
from .gating import plan_of

HISTORY_CYCLES = 5          # earlier cycles the streak looks at; the other rules look at fewer
BILL_DAYS = 7
RECENT_DAYS = 3
ROW_DAYS = 180
MATURITY_DAYS = 30
ZERO = Decimal('0')


class BudgetHistory(NamedTuple):
    """The three budget months before this one: each category's limit and what was spent (the Budget page's
    definition of spend), keyed by budget month then normalised category name."""
    months: list
    limits: dict
    spend: dict


class UndeclaredNeed(Exception):
    """A rule read a loader it did not list in `needs`."""


class InsightContext:
    def __init__(self, user, *, today: date | None = None, profile=None):
        self.user = user
        self.profile = profile if profile is not None else user.profile
        self.today = today or timezone.localdate()
        self.language = get_language() or self.profile.language or 'en'
        self.fmt = Fmt(self.profile.currency)
        self.plan = plan_of(self.profile)
        self.salary_day = budget_cycle.salary_day_of(user)
        self.key = budget_cycle.budget_month_of(self.salary_day, self.today)
        self.links = LinkBuilder()
        self._cache: dict = {}
        self._allowed: frozenset[str] | None = None

    # ---- declared access ----

    @contextmanager
    def declared(self, needs):
        previous, self._allowed = self._allowed, frozenset(needs)
        try:
            yield self
        finally:
            self._allowed = previous

    def _get(self, name: str, load, need: str | None = None):
        need = need or name
        if self._allowed is not None and need not in self._allowed:
            raise UndeclaredNeed(need)
        if name not in self._cache:
            self._cache[name] = load()
        return self._cache[name]

    # ---- cycles (pure date arithmetic) ----

    @property
    def is_cycle(self) -> bool:
        """True when the person's months are salary cycles, not calendar months."""
        return self.salary_day > 1

    def cycle_of(self, key: date) -> tuple[date, date]:
        return budget_cycle.budget_range(self.salary_day, key)

    @property
    def cycle(self) -> tuple[date, date]:
        return self.cycle_of(self.key)

    def keys_back(self, count: int) -> list[date]:
        """The `count` budget months before the current one, nearest first."""
        keys, key = [], self.key
        for _ in range(count):
            key = budget_cycle.previous_month(key)
            keys.append(key)
        return keys

    @property
    def elapsed_days(self) -> int:
        start, end = self.cycle
        return max(1, min((self.today - start).days + 1, (end - start).days + 1))

    @property
    def cycle_days(self) -> int:
        start, end = self.cycle
        return (end - start).days + 1

    # ---- loaders ----

    @property
    def facts(self) -> Facts:
        return self._get('facts', self._load_facts)

    def _load_facts(self) -> Facts:
        oldest = min(self.cycle_of(self.keys_back(HISTORY_CYCLES)[-1])[0],
                     self.cycle_of(date(self.key.year, 1, 1))[0])
        return load_facts(self.user, oldest, self.cycle[1])

    def summary(self, key: date) -> Summary:
        """One budget month through the savings definition (income, spending, saved, rate, categories)."""
        cache = self._cache.setdefault('summaries', {})
        if key not in cache:
            cache[key] = self.facts.summarize(*self.cycle_of(key))
        return cache[key]

    def history_cycles(self) -> int:
        """Completed cycles, out of the last few, in which anything was recorded."""
        counted = 0
        for key in self.keys_back(HISTORY_CYCLES):
            summary = self.summary(key)
            counted += 1 if (summary.income or summary.spending or summary.invested) else 0
        return counted

    @property
    def budgets(self) -> dict:
        return self._get('budgets', self._load_budgets)

    def _load_budgets(self) -> dict:
        from ..budget_page import build_budget_page
        return build_budget_page(self.user, self.key, self.today)

    @property
    def maturing(self) -> list:
        return self._get('accounts', self._load_maturing)

    def _load_maturing(self) -> list:
        from ..models import Account
        return list(Account.objects.filter(
            user=self.user, is_active=True, deposit_maturity_date__isnull=False,
            deposit_maturity_date__gte=self.today, deposit_maturity_date__lte=self.today + timedelta(days=MATURITY_DAYS),
        ).order_by('deposit_maturity_date'))

    @property
    def bills_due(self) -> list:
        return self._get('recurring', self._load_bills)

    def _load_bills(self) -> list:
        from ..models import RecurringTransaction
        return list(RecurringTransaction.objects.filter(
            user=self.user, is_active=True, next_due_date__gte=self.today,
            next_due_date__lte=self.today + timedelta(days=BILL_DAYS),
        ).exclude(transaction_type='INCOME').order_by('next_due_date', 'id'))

    @property
    def recent_expenses(self) -> list[ExpenseRow]:
        """Entries from the last few days: where a possible duplicate would be."""
        return self._get('recent', lambda: load_expense_rows(self.user, self.today - timedelta(days=RECENT_DAYS), self.today))

    @property
    def expense_rows(self) -> list[ExpenseRow]:
        """Roughly six cycles of single entries, for what is usual for a category. Heavy: nightly rules only."""
        return self._get('rows', lambda: load_expense_rows(self.user, self.today - timedelta(days=ROW_DAYS), self.today))

    @property
    def budget_history(self) -> BudgetHistory:
        return self._get('budget_history', self._load_budget_history)

    def _load_budget_history(self) -> BudgetHistory:
        from .. import budget_resolver
        from ..budgets import monthly_spend_by_category_months
        months = sorted(self.keys_back(3))
        return BudgetHistory(
            months=months,
            limits=budget_resolver.effective_limits_range_by_name(self.user, months[0], months[-1]),
            spend=monthly_spend_by_category_months(self.user, months),
        )

    @property
    def dismissed_suggestions(self) -> frozenset[str]:
        """Budget suggestions the person dismissed on the Budget page (the banner keeps its own record)."""
        return self._get('dismissed_suggestions', self._load_dismissed, need='budget_history')

    def _load_dismissed(self) -> frozenset[str]:
        from ..models import BudgetSuggestionDismissal
        return frozenset(BudgetSuggestionDismissal.objects.filter(user=self.user).values_list('key', flat=True))
