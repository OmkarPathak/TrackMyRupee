"""The tone linter. Insight copy is a calm friend's voice: no alarm words, no shouting, no exclamation marks.

Used by the unit test that scans every rule's sample output and by the test that scans every gettext string in this
package (the engine does not lint at run time). See docs/insights-tone.md.
"""
from __future__ import annotations

import re

BANNED = re.compile(
    r"\b(warning|danger(ous(ly)?)?|overspent|you failed|wast(ed|ing)|bad|careless|you must|you need to|"
    r"urgent(ly)?|immediately)\b",
    re.IGNORECASE,
)
SHOUTING = re.compile(r'\b[A-Z]{4,}\b')          # short acronyms such as EMI, SIP, FD and UPI are fine
EMOJI = re.compile('[\U0001F000-\U0001FAFF⌀-⏿☀-➿⬀-⯿️]')


def check(text: str | None) -> list[str]:
    """What is wrong with one string, in words a rule author can act on. Empty when it is fine."""
    if not text:
        return []
    found = []
    match = BANNED.search(text)
    if match:
        found.append(f'banned word "{match.group(0)}"')
    if '!' in text or '！' in text:
        found.append('exclamation mark')
    if SHOUTING.search(text):
        found.append('ALL CAPS word')
    if EMOJI.search(text):
        found.append('emoji')
    return found


def check_insight(insight) -> list[str]:
    parts = [insight.title, insight.message, insight.detail, insight.share_text]
    if insight.action:
        parts.append(insight.action.label)
    for item in insight.evidence:
        parts += [item.label, item.value]
    problems = []
    for part in parts:
        problems += check(part)
    return problems
