from __future__ import annotations

from collections import OrderedDict


CATEGORY_ORDER = ["debt", "income", "bills", "savings", "assets"]
CATEGORY_LABELS = {
    "debt": "Debt",
    "income": "Income",
    "bills": "Bills",
    "savings": "Savings & investments",
    "assets": "Assets",
}

_FLOW_REGISTRY = {}


def register_flow(cls):
    """Decorator that registers a Flow subclass globally."""
    _FLOW_REGISTRY[cls.key] = cls()
    return cls


class FlowRegistry:
    @staticmethod
    def get(key: str):
        return _FLOW_REGISTRY[key]

    @staticmethod
    def all():
        return _FLOW_REGISTRY.copy()

    @staticmethod
    def by_category() -> OrderedDict[str, dict]:
        grouped = OrderedDict((category, {"label": CATEGORY_LABELS[category], "flows": []}) for category in CATEGORY_ORDER)
        for flow in _FLOW_REGISTRY.values():
            grouped.setdefault(flow.category, {"label": CATEGORY_LABELS.get(flow.category, flow.category.title()), "flows": []})
            grouped[flow.category]["flows"].append(flow)
        for bucket in grouped.values():
            bucket["flows"].sort(key=lambda flow: flow.label)
        return grouped