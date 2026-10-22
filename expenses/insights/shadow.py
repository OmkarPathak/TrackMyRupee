"""Shadow mode: while the dashboard still shows its own insights, run the engine's migrated rules for a sample of
people and record whether they agree. Only the rule id, whether the two matched and how far apart they were are
recorded (a log line and an analytics event), never the numbers. The golden tests compare the values."""
from __future__ import annotations

import logging
from decimal import Decimal

from . import engine_mode, registry, telemetry
from .context import InsightContext

logger = logging.getLogger(__name__)

MIGRATED = ('cashflow.pace_vs_usual', 'budgets.category_runout', 'accounts.deposit_maturing',
            'expenses.category_above_usual', 'budgets.limit_passed', 'budgets.limit_nearing',
            'cashflow.saving_streak', 'cashflow.savings_momentum', 'cashflow.year_pace', 'recurring.bills_soon')


def should_compare(user) -> bool:
    from django.conf import settings
    return engine_mode(user) == 'shadow' and user.pk % 100 < settings.INSIGHTS_SHADOW_PERCENT


def engine_view(rule_id: str, insights) -> object:
    """The engine's answer in the same shape as the legacy one (see LEGACY below)."""
    if not insights:
        return None
    n = insights[0].numbers
    if rule_id in ('budgets.category_runout', 'budgets.limit_passed', 'budgets.limit_nearing'):
        return set(n['categories'])
    if rule_id == 'accounts.deposit_maturing':
        return {i.numbers['name'] for i in insights}
    if rule_id == 'cashflow.pace_vs_usual':
        return {'pct': n['pct']}
    if rule_id == 'expenses.category_above_usual':
        return {'category': n['category'], 'pct': n['pct']}
    if rule_id == 'cashflow.saving_streak':
        return n['streak']
    if rule_id == 'cashflow.savings_momentum':
        return True
    if rule_id == 'cashflow.year_pace':
        return Decimal(n['projected'])
    if rule_id == 'recurring.bills_soon':
        return [tuple(item) for item in n['items']]
    return None


def _empty(value) -> bool:
    return value is None or value is False or value == set() or value == []


def delta(legacy, engine) -> str:
    """'exact', 'close' (numbers within 5%), 'far' or 'shape' (one side said nothing)."""
    if _empty(legacy) and _empty(engine):
        return 'exact'
    if _empty(legacy) != _empty(engine):
        return 'shape'
    if legacy == engine:
        return 'exact'
    pair = [legacy, engine]
    if isinstance(legacy, dict) and isinstance(engine, dict) and legacy.get('category') == engine.get('category'):
        pair = [legacy.get('pct'), engine.get('pct')]
    if all(isinstance(v, (int, float, Decimal)) for v in pair) and pair[0]:
        return 'close' if abs(Decimal(str(pair[1])) - Decimal(str(pair[0]))) <= abs(Decimal(str(pair[0]))) * Decimal('0.05') else 'far'
    return 'far'


def compare(user, legacy: dict) -> dict[str, str]:
    """`legacy` maps a rule id to what the dashboard's own code concluded (only for rules whose legacy block ran).
    Returns {rule_id: delta}. Never raises."""
    out: dict[str, str] = {}
    try:
        ctx = InsightContext(user)
        for rule_id in MIGRATED:
            if rule_id not in legacy:
                continue
            rule = registry.get(rule_id)
            with ctx.declared(rule.needs):
                mine = engine_view(rule_id, rule.evaluate(ctx))
            out[rule_id] = delta(legacy[rule_id], mine)
            logger.info('insight shadow rule_id=%s delta=%s', rule_id, out[rule_id])
            telemetry.event(user, 'shadow', rule_id=rule_id, matched=out[rule_id] == 'exact', delta=out[rule_id])
    except Exception as exc:        # noqa: BLE001 - the dashboard must not notice
        logger.warning('insight shadow failed type=%s', type(exc).__name__)
    return out
