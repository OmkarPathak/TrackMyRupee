"""Recurring rules. The dashboard has always listed payments due in the next week; this is that, as one insight."""
from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Surface, Tone

D = Decimal
EVIDENCE_ROWS = 3


def _amount(payment) -> Decimal:
    return D(str(payment.base_amount if payment.base_amount is not None else payment.amount))


@rule(id='recurring.bills_soon', module=Module.RECURRING, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recurring'}, cooldown_days=3, sample=sampling.default_sample)
def bills_soon(ctx) -> list[Insight]:
    """Active recurring payments (anything that is not income) due in the next seven days."""
    due = ctx.bills_due
    if not due:
        return []
    fmt, count = ctx.fmt, len(due)
    total = sum((_amount(p) for p in due), D(0))
    first = due[0]
    link = reverse('recurring-list')
    args = {'description': fmt.short(first.description, words=6, chars=40), 'n': fmt.number(count),
            'total': fmt.approx(total), 'date': fmt.day(first.next_due_date)}
    if count == 1:
        message = _('%(description)s, about %(total)s, is due on %(date)s. '
                    'It is scheduled, so there is nothing to do unless plans have changed.') % args
    else:
        message = _('%(n)s payments totalling about %(total)s are due in the next 7 days, the first on %(date)s. '
                    'They are scheduled, so there is nothing to do unless plans have changed.') % args
    return [Insight(
        rule_id='recurring.bills_soon', module=Module.RECURRING, kind=Kind.UPCOMING, tone=Tone.CALM,
        priority=62, icon='calendar-event',
        title=_('Bills coming up this week'), message=message,
        evidence=tuple(Evidence(fmt.short(p.description, words=6, chars=40), fmt.money(_amount(p)), link)
                       for p in due[:EVIDENCE_ROWS]),
        action=Action(_('View recurring payments'), link),
        dedupe_key=f'{first.next_due_date:%Y-%m-%d}', valid_until=due[-1].next_due_date,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'items': [[p.description.strip(), p.next_due_date.isoformat()] for p in due[:EVIDENCE_ROWS]]},
    )]
