"""Calendar: a week with more scheduled payments than the person's usual."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from statistics import median

from django.urls import reverse
from django.utils.translation import gettext as _

from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
WEEKS = 6                       # earlier weeks that make up a "typical" one
FACTOR = D('1.5')               # the coming week is this many times a typical one before it is mentioned
MIN_WEEKS = 4                   # weeks with spending needed before "typical" means anything
SPENDING_TYPES = ('EXPENSE', 'INSURANCE_PREMIUM')   # what the savings definition counts as spending when it posts (loan principal is not)


@rule(id='calendar.heavy_week', module=Module.CALENDAR, surfaces={Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH},
      needs={'pending', 'facts'}, cooldown_days=7, min_history=2, min_plan=Plan.PLUS, push=True, requires={'recurring'},
      preview=lambda: _('A busy week of payments ahead'), sample=sampling.heavy_week_sample)
def heavy_week(ctx) -> list[Insight]:
    """What is scheduled to go out over the next seven days against the person's own median week of spending."""
    end = ctx.today + timedelta(days=6)
    scheduled = sum((item['amount'] for day, items in ctx.pending.items() if ctx.today <= day <= end
                     for item in items if item['kind'] == 'out' and item['type'] in SPENDING_TYPES), D(0))
    if scheduled <= 0:
        return []
    weeks = []
    for back in range(1, WEEKS + 1):
        last = ctx.today - timedelta(days=7 * (back - 1) + 1)
        weeks.append(sum(ctx.facts.daily_spending(last - timedelta(days=6), last), D(0)))
    weeks = [w for w in weeks if w > 0]
    if len(weeks) < MIN_WEEKS:
        return []
    typical = D(str(median(weeks)))
    if scheduled < typical * FACTOR:
        return []
    times = round(float(scheduled / typical) * 2) / 2        # to the nearest half
    link = reverse('calendar')
    fmt = ctx.fmt
    return [Insight(
        rule_id='calendar.heavy_week', module=Module.CALENDAR, kind=Kind.UPCOMING, tone=Tone.CALM, priority=50, icon='calendar-week',
        title=_('A busy week of payments ahead'),
        message=_('About %(amount)s is scheduled over the next 7 days, around %(times)s times a typical week for you.') % {
            'amount': fmt.approx(scheduled), 'times': fmt.number(f'{times:g}')},
        evidence=(Evidence(_('Scheduled'), fmt.money(scheduled), link), Evidence(_('A typical week'), fmt.money(typical))),
        action=Action(_('Open the calendar'), link),
        dedupe_key=f'{ctx.today:%G-W%V}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE, Surface.BELL, Surface.PUSH}),
        numbers={'scheduled': str(scheduled), 'typical': str(typical.quantize(D('0.01')))},
    )]
