"""The data an insight is made of. All immutable: a rule returns Insights, nothing edits them afterwards."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import IntEnum, StrEnum
from typing import Any


class Module(StrEnum):
    EXPENSES = 'expenses'
    INCOME = 'income'
    BUDGETS = 'budgets'
    ACCOUNTS = 'accounts'
    LOANS = 'loans'
    GOALS = 'goals'
    RECURRING = 'recurring'
    TRANSFERS = 'transfers'
    CAPITAL_EVENTS = 'capital_events'
    HOLDINGS = 'holdings'
    CALENDAR = 'calendar'
    ANALYTICS = 'analytics'
    IMPORT = 'import'
    CASHFLOW = 'cashflow'


class Kind(StrEnum):
    OBSERVATION = 'observation'
    PATTERN = 'pattern'
    UPCOMING = 'upcoming'
    PROGRESS = 'progress'
    MILESTONE = 'milestone'
    OPPORTUNITY = 'opportunity'


class Tone(StrEnum):
    """There is no alarm level on purpose."""
    CALM = 'calm'
    POSITIVE = 'positive'
    GENTLE_ATTENTION = 'gentle_attention'


class Surface(StrEnum):
    DASHBOARD = 'dashboard'
    ANALYTICS = 'analytics'
    MODULE = 'module'
    BELL = 'bell'
    PUSH = 'push'
    DIGEST = 'digest'
    MONTHLY = 'monthly'
    YEAR_IN_REVIEW = 'year_in_review'


class Plan(IntEnum):
    FREE = 0
    PLUS = 1
    PRO = 2

    @classmethod
    def of(cls, tier: str | None) -> Plan:
        return cls.__members__.get((tier or 'FREE').upper(), cls.FREE)


TITLE_WORDS = 8
MESSAGE_SENTENCES = 2
_SENTENCE_BREAK = re.compile(r'[.?।]\s+(?=\S)')


def sentence_count(text: str) -> int:
    text = (text or '').strip()
    return len(_SENTENCE_BREAK.split(text)) if text else 0


@dataclass(frozen=True, slots=True)
class Evidence:
    """One number behind the sentence, already formatted, with a link the person can click through to."""
    label: str
    value: str
    link: str | None = None


@dataclass(frozen=True, slots=True)
class Action:
    """The single optional next step. A link (`url`) or an HTMX POST (`hx_post`)."""
    label: str
    url: str | None = None
    hx_post: str | None = None


@dataclass(frozen=True, slots=True)
class Insight:
    rule_id: str
    module: Module
    kind: Kind
    tone: Tone
    priority: int
    title: str
    message: str
    dedupe_key: str
    valid_until: date
    surfaces: frozenset[Surface]
    detail: str | None = None
    evidence: tuple[Evidence, ...] = ()
    action: Action | None = None
    plan: Plan | None = None
    share_text: str | None = None
    sensitive: bool = False          # debt, a shortfall, a month in the red: extra gentle, no share, no push
    icon: str = 'lightbulb'          # a Bootstrap icon name from the icon set the app already ships
    # The plain values behind the words (JSON-friendly). Used by tests and the shadow comparison, never shown.
    numbers: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self):
        problems = self.problems()
        if problems:
            raise ValueError(f'{self.rule_id}: ' + '; '.join(problems))

    def problems(self) -> list[str]:
        """Structural problems only (wording is checked by insights.tone)."""
        found = []
        if not 0 <= self.priority <= 100:
            found.append('priority must be 0 to 100')
        if not self.title or len(self.title.split()) > TITLE_WORDS:
            found.append(f'title must be 1 to {TITLE_WORDS} words')
        if not self.message or sentence_count(self.message) > MESSAGE_SENTENCES:
            found.append(f'message must be 1 to {MESSAGE_SENTENCES} sentences')
        if not self.dedupe_key:
            found.append('dedupe_key is required')
        links = [e.link for e in self.evidence if e.link]
        if self.action and self.action.url:
            links.append(self.action.url)
        if any(not link.startswith('/') or link.startswith('//') for link in links):
            found.append('links must be site-relative')
        if self.sensitive and (self.share_text or Surface.PUSH in self.surfaces):
            found.append('a sensitive insight has no share text and is never pushed')
        return found

    def to_payload(self) -> dict[str, Any]:
        """JSON for InsightState.payload. `numbers` stays out: it is not needed to show the insight."""
        return {
            'rule_id': self.rule_id, 'module': self.module.value, 'kind': self.kind.value, 'tone': self.tone.value,
            'priority': self.priority, 'title': self.title, 'message': self.message, 'detail': self.detail,
            'evidence': [[e.label, e.value, e.link] for e in self.evidence],
            'action': [self.action.label, self.action.url, self.action.hx_post] if self.action else None,
            'dedupe_key': self.dedupe_key, 'valid_until': self.valid_until.isoformat(),
            'surfaces': sorted(s.value for s in self.surfaces), 'plan': int(self.plan) if self.plan is not None else None,
            'share_text': self.share_text, 'sensitive': self.sensitive, 'icon': self.icon,
        }

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> Insight:
        action = data.get('action')
        return cls(
            rule_id=data['rule_id'], module=Module(data['module']), kind=Kind(data['kind']), tone=Tone(data['tone']),
            priority=data['priority'], title=data['title'], message=data['message'], detail=data.get('detail'),
            evidence=tuple(Evidence(*e) for e in data.get('evidence', ())),
            action=Action(*action) if action else None,
            dedupe_key=data['dedupe_key'], valid_until=date.fromisoformat(data['valid_until']),
            surfaces=frozenset(Surface(s) for s in data['surfaces']),
            plan=Plan(data['plan']) if data.get('plan') is not None else None,
            share_text=data.get('share_text'), sensitive=data.get('sensitive', False), icon=data.get('icon', 'lightbulb'),
        )


@dataclass(frozen=True, slots=True)
class Presented:
    """What an adapter receives: the insight, the state row it lives in (for dismiss and snooze), and whether the
    person's plan only unlocks a preview of it."""
    insight: Insight
    state_uuid: str
    locked: bool = False
