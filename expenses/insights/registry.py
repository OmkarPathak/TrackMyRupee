"""The rule registry. A rule is a pure function of an InsightContext, registered with @rule.

    @rule(id='budgets.limit_nearing', module=Module.BUDGETS, surfaces={Surface.DASHBOARD}, needs={'budgets'},
          cooldown_days=7, sample=sample_limit_nearing)
    def limit_nearing(ctx): ...
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .types import Insight, Module, Plan, Surface

COSTS = ('light', 'heavy')
MODES = ('stored', 'inline')
# the loaders an InsightContext offers
NEEDS = frozenset({'facts', 'budgets', 'accounts', 'recurring', 'recent', 'rows', 'budget_history', 'inventory',
                   'recurring_all', 'postings', 'pending', 'cash', 'accounts_all', 'cards', 'loans', 'loan_paid',
                   'reconciliations', 'income_rows', 'goals', 'holdings', 'networth'})
INVENTORY = frozenset({'loans', 'cards', 'recurring', 'goals', 'holdings'})     # what a person may not have at all


@dataclass(frozen=True)
class InsightRule:
    id: str
    module: Module
    surfaces: frozenset[Surface]
    min_plan: Plan
    cooldown_days: int
    needs: frozenset[str]
    evaluate: Callable
    min_history: int = 0          # completed cycles with any activity before the rule may speak
    cost: str = 'light'           # 'light': cheap arithmetic over loaded data; 'heavy': history scans, command only
    mode: str = 'stored'          # 'stored': kept and dismissible; 'inline': computed per page view, never stored
    push: bool = False            # time-sensitive and helpful enough to be pushed (never for sensitive insights)
    preview: Callable | None = None      # a figure-free line shown to plans below min_plan
    sample: Callable | None = None       # builds a synthetic context so tests can run the rule everywhere
    pool: str | None = None              # 'weekly': on the Free plan the pool's rules share one observation a week
    requires: frozenset[str] = frozenset()   # 'loans', 'cards', 'recurring', 'goals' or 'holdings': skipped, costing nothing, when the person has none


REGISTRY: dict[str, InsightRule] = {}
_loaded = False


def rule(*, id: str, module: Module, surfaces: Iterable[Surface], needs: Iterable[str] = (), min_plan: Plan = Plan.FREE,
         cooldown_days: int = 7, min_history: int = 0, cost: str = 'light', mode: str = 'stored', push: bool = False,
         preview: Callable | None = None, sample: Callable | None = None, pool: str | None = None,
         requires: Iterable[str] = ()):
    surfaces, needs, requires = frozenset(surfaces), frozenset(needs), frozenset(requires)
    if id in REGISTRY:
        raise ValueError(f'duplicate rule id {id}')
    if cost not in COSTS or mode not in MODES:
        raise ValueError(f'{id}: bad cost or mode')
    if not needs <= NEEDS:
        raise ValueError(f'{id}: unknown needs {sorted(needs - NEEDS)}')
    if not requires <= INVENTORY:
        raise ValueError(f'{id}: unknown requires {sorted(requires - INVENTORY)}')
    if push and Surface.PUSH not in surfaces:
        raise ValueError(f'{id}: push rules list Surface.PUSH')
    if sample is None:
        raise ValueError(f'{id}: every rule ships a sample() so the tone and translation tests can cover it')

    def register(fn):
        REGISTRY[id] = InsightRule(
            id=id, module=module, surfaces=surfaces, min_plan=min_plan, cooldown_days=cooldown_days, needs=needs,
            evaluate=fn, min_history=min_history, cost=cost, mode=mode, push=push, preview=preview, sample=sample, pool=pool, requires=requires)
        return fn
    return register


def load() -> dict[str, InsightRule]:
    """Import every module under insights.rules once, so the decorators run."""
    global _loaded
    if not _loaded:
        from . import rules
        for info in pkgutil.iter_modules(rules.__path__):
            importlib.import_module(f'{rules.__name__}.{info.name}')
        _loaded = True
    return REGISTRY


def all_rules() -> list[InsightRule]:
    return sorted(load().values(), key=lambda r: r.id)


def get(rule_id: str) -> InsightRule | None:
    return load().get(rule_id)


def modules_with_rules() -> list[Module]:
    seen = {r.module for r in all_rules()}
    return [m for m in Module if m in seen]


def insights_of(rule_: InsightRule, ctx) -> list[Insight]:
    return rule_.evaluate(ctx)
