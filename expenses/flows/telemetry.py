"""The only place Guided Setup talks to analytics. An event carries which setup it is, how far the person got and what
kind of problem Confirm found, and nothing else: never an amount, an account name or anything they typed."""
from __future__ import annotations

from ..posthog_utils import ph_capture

ALLOWED = ('flow', 'kind', 'field_count', 'first_field', 'configured', 'total')


def event(user, name: str, **props) -> None:
    clean = {k: v for k, v in props.items() if k in ALLOWED and isinstance(v, (str, int, bool))}
    ph_capture(user, f'guided_setup_{name}', clean)
