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
from types import SimpleNamespace
from typing import NamedTuple

from django.utils import timezone
from django.utils.translation import get_language

from .. import budget_cycle
from ..analytics.facts import (
    ExpenseRow,
    Facts,
    IncomeRow,
    LoanPaid,
    Summary,
    load_expense_rows,
    load_facts,
    load_income_rows,
    load_loan_paid,
)
from ..analytics.links import LinkBuilder
from .format import Fmt
from .gating import plan_of

HISTORY_CYCLES = 5          # earlier cycles the streak looks at; the other rules look at fewer
BILL_DAYS = 7
RECENT_DAYS = 3
ROW_DAYS = 180
PENDING_DAYS = 14
POSTING_DAYS = 400
MATURITY_DAYS = 30
INCOME_DAYS = 240
SNAPSHOT_SLACK_DAYS = 3         # how far before the cycle began a net worth snapshot may be and still stand for it
ZERO = Decimal('0')


class BudgetHistory(NamedTuple):
    """The three budget months before this one: each category's limit and what was spent (the Budget page's
    definition of spend), keyed by budget month then normalised category name."""
    months: list
    limits: dict
    spend: dict


class GoalFacts(NamedTuple):
    """A savings goal with what has gone into it, and where it stands for the person's plan."""
    goal: object
    contributions: list              # [(date, amount)] oldest first, in the goal's own currency
    locked: bool                     # beyond the number of goals the plan includes


class HoldingValue(NamedTuple):
    """An active holding with what it is worth now, in the person's currency."""
    holding: object
    value: Decimal


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

    # ---- recurring payments, loans, cards, cash: loaded only for people who have them (see `inventory`) ----

    @property
    def inventory(self) -> dict[str, bool]:
        """Does the person have any loans, credit cards or recurring payments? One query, so a rule about loans costs
        nothing at all for someone with none."""
        return self._get('inventory', self._load_inventory)

    def _load_inventory(self) -> dict[str, bool]:
        from django.contrib.auth.models import User
        from django.db.models import Exists, OuterRef

        from ..models import Account, Holding, Loan, RecurringTransaction, SavingsGoal

        row = User.objects.filter(pk=self.user.pk).annotate(
            has_loans=Exists(Loan.objects.filter(user=OuterRef('pk'), is_active=True)),
            has_cards=Exists(Account.objects.filter(user=OuterRef('pk'), is_active=True,
                                                    account_type__in=revolving_codes(),
                                                    credit_card_billing_day__isnull=False)),
            has_recurring=Exists(RecurringTransaction.objects.filter(user=OuterRef('pk'), is_active=True)),
            has_goals=Exists(SavingsGoal.objects.filter(user=OuterRef('pk'))),
            has_holdings=Exists(Holding.objects.filter(account__user=OuterRef('pk'), is_active=True)),
        ).values('has_loans', 'has_cards', 'has_recurring', 'has_goals', 'has_holdings').first() or {}
        return {'loans': bool(row.get('has_loans')), 'cards': bool(row.get('has_cards')),
                'recurring': bool(row.get('has_recurring')), 'goals': bool(row.get('has_goals')),
                'holdings': bool(row.get('has_holdings'))}

    @property
    def recurring_all(self) -> list:
        """Every active recurring payment (the due-soon list is `bills_due`)."""
        return self._get('recurring_all', self._load_recurring_all)

    def _load_recurring_all(self) -> list:
        from ..models import RecurringTransaction
        return list(RecurringTransaction.objects.filter(user=self.user, is_active=True).select_related(
            'to_account').order_by('id'))

    @property
    def postings(self) -> dict[str, list[tuple]]:
        """{description: [(date, base amount), ...]} oldest first, for the entries the app posted from the person's
        active recurring expenses: what a payment actually cost each time. One query."""
        return self._get('postings', self._load_postings)

    def _load_postings(self) -> dict[str, list[tuple]]:
        from ..models import Expense

        names = {f'{rt.description} (Recurring)' for rt in self.recurring_all if rt.transaction_type == 'EXPENSE'}
        out: dict[str, list[tuple]] = {}
        if not names:
            return out
        rows = Expense.objects.filter(user=self.user, description__in=names,
                                      date__gte=self.today - timedelta(days=POSTING_DAYS)
                                      ).order_by('date', 'id').values_list('description', 'date', 'base_amount')
        for description, day, amount in rows:
            out.setdefault(description, []).append((day, Decimal(str(amount))))
        return out

    @property
    def pending(self) -> dict:
        """{date: [scheduled item, ...]} for the next two weeks: what the Calendar page lists as coming up."""
        return self._get('pending', self._load_pending)

    def _load_pending(self) -> dict:
        from ..calendar_data import pending_by_day
        return pending_by_day(self.user, self.today, self.today + timedelta(days=PENDING_DAYS))

    @property
    def cash(self):
        """The net worth point for today: `.idle` is the money held in cash and bank accounts."""
        return self._get('cash', self._load_cash)

    def _load_cash(self):
        from ..analytics.networth import net_worth_points
        return net_worth_points(self.user, [self.today], self.today)[0]

    @property
    def accounts_all(self) -> list:
        return self._get('accounts_all', self._load_accounts_all)

    def _load_accounts_all(self) -> list:
        from ..models import Account
        return list(Account.objects.filter(user=self.user, is_active=True).order_by('id'))

    @property
    def cards(self) -> list[tuple]:
        """[(card, owed)] for active credit cards, BNPL and overdrafts with a billing day: what is owed, positive, in the
        card's own currency, from the ledger the accounts page uses."""
        return self._get('cards', self._load_cards)

    def _load_cards(self) -> list[tuple]:
        from ..ledger_read_service import LedgerReadService
        from ..models import Account

        cards = list(Account.objects.filter(user=self.user, is_active=True, account_type__in=revolving_codes(),
                                            credit_card_billing_day__isnull=False).order_by('id'))
        balances = LedgerReadService.get_account_balances(cards) if cards else {}
        return [(card, max(-Decimal(str(balances.get(card.pk, card.balance) or 0)), ZERO)) for card in cards]

    @property
    def loans(self) -> list:
        """Active loans, each with its remaining principal already worked out (so the loan service's own maths runs
        without a query per loan) and its interest rates prefetched. Two queries."""
        return self._get('loans', self._load_loans)

    def _load_loans(self) -> list:
        from ..models import Loan, annotate_loan_principal_totals

        loans = list(annotate_loan_principal_totals(
            Loan.objects.filter(user=self.user, is_active=True).order_by('id')).prefetch_related('interest_rates'))
        for loan in loans:
            remaining = (Decimal(str(loan.initial_principal)) - Decimal(str(loan.paid_principal))
                         - Decimal(str(loan.capital_prepaid)) - Decimal(str(loan.opening_paid_principal or 0)))
            loan._cached_summary = {'remaining_principal': float(max(remaining, ZERO))}
        return loans

    @property
    def financial_year(self) -> tuple[date, date]:
        """April to March for rupee accounts (the Indian financial year), the calendar year otherwise."""
        if self.fmt.indian:
            start_year = self.today.year if self.today.month >= 4 else self.today.year - 1
            return date(start_year, 4, 1), date(start_year + 1, 3, 31)
        return date(self.today.year, 1, 1), date(self.today.year, 12, 31)

    @property
    def loan_paid(self) -> LoanPaid:
        return self._get('loan_paid', self._load_loan_paid)

    def _load_loan_paid(self) -> LoanPaid:
        first, last = self.financial_year
        return load_loan_paid(self.user, first, min(last, self.today))

    @property
    def income_rows(self) -> list[IncomeRow]:
        """About eight months of income entries (a few dozen rows): where money came from and when it arrived. One query."""
        return self._get('income_rows', lambda: load_income_rows(self.user, self.today - timedelta(days=INCOME_DAYS), self.today))

    @property
    def goals(self) -> list[GoalFacts]:
        """Every savings goal, oldest first, with its contributions. Two queries."""
        return self._get('goals', self._load_goals)

    def _load_goals(self) -> list[GoalFacts]:
        from finance_tracker.plans import get_limit

        from ..models import GoalContribution, SavingsGoal

        goals = list(SavingsGoal.objects.filter(user=self.user).order_by('created_at', 'id'))
        if not goals:
            return []
        by_goal: dict[int, list] = {g.pk: [] for g in goals}
        for goal_id, day, amount in GoalContribution.active.filter(goal_id__in=by_goal).order_by('date', 'id').values_list(
                'goal_id', 'date', 'amount'):
            by_goal[goal_id].append((day, Decimal(str(amount))))
        limit = get_limit(getattr(self.profile, 'active_tier', 'FREE'), 'savings_goals')
        return [GoalFacts(g, by_goal[g.pk], limit != -1 and i >= limit) for i, g in enumerate(goals)]

    @property
    def holdings(self) -> list[HoldingValue]:
        """Active holdings, each with its latest valuation in the person's currency (the cost when none was entered). One
        query, plus an exchange rate for each other currency."""
        return self._get('holdings', self._load_holdings)

    def _load_holdings(self) -> list[HoldingValue]:
        from django.db.models import OuterRef, Subquery

        from ..models import Holding, Valuation
        from ..utils import get_exchange_rate

        latest = Valuation.objects.filter(holding=OuterRef('pk')).order_by('-as_of_date', '-created_at')
        rows = Holding.objects.filter(account__user=self.user, is_active=True).annotate(
            last_value=Subquery(latest.values('value')[:1]), last_nav=Subquery(latest.values('unit_nav')[:1])).order_by('id')
        base, rates, out = self.profile.currency, {}, []
        for holding in rows:
            if holding.last_value is None:
                value = holding.cost_basis
            elif holding.last_nav is not None and holding.units:
                value = (Decimal(holding.units) * holding.last_nav).quantize(Decimal('0.01'))
            else:
                value = Decimal(str(holding.last_value))
            if holding.currency != base:
                if holding.currency not in rates:
                    try:
                        rates[holding.currency] = Decimal(str(get_exchange_rate(holding.currency, base)))
                    except Exception:                   # no rate: use the figure as it is rather than say nothing
                        rates[holding.currency] = Decimal(1)
                value = (value * rates[holding.currency]).quantize(Decimal('0.01'))
            out.append(HoldingValue(holding, value))
        return out

    @property
    def networth(self) -> tuple:
        """(net worth at the end of the day before this cycle began, net worth today), each with a `.total`. The earlier
        figure is the nightly snapshot nearest that day (one query; None when there is none, and the rule then says
        nothing); today's is the point the cash rules already worked out, so it costs nothing more."""
        return self._get('networth', self._load_networth)

    def _load_networth(self) -> tuple:
        from ..analytics.networth import net_worth_points
        from ..models import NetWorthSnapshot

        start = self.cycle[0] - timedelta(days=1)
        snap = NetWorthSnapshot.objects.filter(user=self.user, as_of_date__range=(start - timedelta(days=SNAPSHOT_SLACK_DAYS), start)
                                               ).order_by('-as_of_date').values_list('total_net_worth', flat=True).first()
        if snap is None:
            return None, None
        return SimpleNamespace(total=Decimal(str(snap))), net_worth_points(self.user, [self.today], self.today)[0]

    # ---- inline rules: the Analytics tab's own Page (see pagectx.PageContext, which fills these in) ----

    values: dict = {}

    @property
    def page(self):
        return self._get('page', self._load_page)

    def _load_page(self):
        raise LookupError('this rule runs on an Analytics page')

    @property
    def category_cycles(self) -> tuple[list, dict]:
        """([(first, last) of each of the last three complete budget months, oldest first], {category: [amount, ...]})."""
        return self._get('category_cycles', self._load_category_cycles)

    def _load_category_cycles(self):
        raise LookupError('this rule runs on an Analytics page')

    @property
    def reconciliations(self) -> dict[int, date]:
        """{account id: the statement date of its latest reconciliation}. One query."""
        return self._get('reconciliations', self._load_reconciliations)

    def _load_reconciliations(self) -> dict[int, date]:
        from django.db.models import Max

        from ..models import AccountReconciliation
        return dict(AccountReconciliation.objects.filter(user=self.user).values_list('account_id').annotate(
            latest=Max('statement_date')))

    @property
    def emergency_months(self) -> int:
        """The person's own target, in months of spending (a Setting; six unless they chose otherwise)."""
        from . import prefs
        return prefs.get(self.profile)['emergency_months']


def revolving_codes() -> list[str]:
    """Account types that are credit cards, buy-now-pay-later or overdrafts."""
    from ..account_types import ACCOUNT_TYPE_META, STRATEGY
    return [code for code, (_kind, strategy) in ACCOUNT_TYPE_META.items() if strategy == STRATEGY.REVOLVING_CREDIT]
