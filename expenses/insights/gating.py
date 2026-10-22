"""Plan gating, in one place. Rules declare `min_plan`; nothing else in the engine looks at a tier.

Channels follow the product rules: the in-app panel, the bell and push are open to every plan; the email digest is
Pro only. (Existing alerts keep their own plan rules in send_notifications.)
"""
from __future__ import annotations

from .types import Plan, Surface

PRO_ONLY_SURFACES = frozenset({Surface.DIGEST, Surface.MONTHLY})


def plan_of(profile) -> Plan:
    return Plan.of(getattr(profile, 'active_tier', 'FREE'))


def rule_allowed(min_plan: Plan, plan: Plan) -> bool:
    return plan >= min_plan


def surface_allowed(surface: Surface, plan: Plan) -> bool:
    return plan >= Plan.PRO if surface in PRO_ONLY_SURFACES else True
