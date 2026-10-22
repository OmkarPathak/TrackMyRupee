"""Every translatable string the Insights Engine owns, found by reading its source (not by running it), so a branch
no test happens to reach is still checked for tone and for Hindi and Marathi translations.

Python: gettext / gettext_lazy / `_`, ngettext, pgettext calls with literal arguments, in this package.
Templates: {% trans "..." %} in the templates that belong to insights.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent.parent
TEMPLATES = [ROOT / 'templates/expenses/partials' / name for name in
             ('_insight_cards.html', '_insight_body.html', '_insight_action.html', '_insight_action_link.html', '_insight_options.html', '_insight_strip.html', '_insight_thanks.html',
             '_insights_settings.html')]
TRANS_TAG = re.compile(r'''\{%\s*trans\s+(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')\s*%\}''')


def _literal(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def python_strings() -> list[tuple[str, str, str, str | None]]:
    """[(path, context, msgid, plural)] for every call in the package."""
    found = []
    for path in sorted(PACKAGE.rglob('*.py')):
        if path.name in ('msgids.py',):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            name, args = node.func.id, node.args
            if name in ('_', 'gettext', 'gettext_lazy') and args and _literal(args[0]) is not None:
                found.append((str(path), '', _literal(args[0]), None))
            elif name == 'ngettext' and len(args) >= 2 and _literal(args[0]) and _literal(args[1]):
                found.append((str(path), '', _literal(args[0]), _literal(args[1])))
            elif name == 'pgettext' and len(args) >= 2 and _literal(args[0]) and _literal(args[1]):
                found.append((str(path), _literal(args[0]), _literal(args[1]), None))
    return found


def template_strings() -> list[tuple[str, str, str, str | None]]:
    found = []
    for path in TEMPLATES:
        for match in TRANS_TAG.finditer(path.read_text(encoding='utf-8')):
            text = match.group(1) if match.group(1) is not None else match.group(2)
            found.append((str(path), '', text.replace('\\"', '"').replace("\\'", "'"), None))
    return found


def all_strings():
    return python_strings() + template_strings()
