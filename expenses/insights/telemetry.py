"""The only place insights talk to analytics. An event carries a rule id, a surface, an action and a count, and
nothing else: never an amount, a description, an account name or a category."""
from __future__ import annotations

from ..posthog_utils import ph_capture

ALLOWED = ('rule_id', 'surface', 'action', 'count', 'matched', 'delta')


def event(user, name: str, **props) -> None:
    clean = {k: v for k, v in props.items() if k in ALLOWED and isinstance(v, (str, int, bool))}
    ph_capture(user, f'insight_{name}', clean)
