"""Expense rules: the person's own categories against their own recent past."""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.utils.dates import WEEKDAYS
from django.utils.translation import gettext as _

from ... import budget_cycle
from ...analytics.links import EXPENSE
from .. import sampling
from ..registry import rule
from ..types import Action, Evidence, Insight, Kind, Module, Plan, Surface, Tone

D = Decimal
ABOVE_USUAL = D('1.1')          # a category this far above its recent average is worth a mention
SHIFT = D('0.10')               # a category this far from where it was at this point last time is mentioned...
MATERIAL = D('0.05')            # ...when it also moved by at least this share of last time's whole spending
MIN_DAYS_IN = 7                 # and not before a week of the period has passed
WEEKS = 8                       # the weekday pattern looks at this many weeks
WEEKDAY_FACTOR = D('1.4')       # the busiest weekday is this much above the other days' average
EARLY_DAYS = 10                 # "early in the cycle" is the first ten days
EARLY_SHARE = D('0.5')          # at least this share of spending in them, three cycles running
BIG_FACTOR = D('3')             # an entry this many times the usual amount for its category
MIN_PRIOR_ENTRIES = 10          # needs this many earlier entries in the category to know what is usual
DUPLICATE_GAP = timedelta(hours=24)


@rule(id='expenses.category_above_usual', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'facts'}, cooldown_days=14, min_history=1, sample=sampling.default_sample)
def category_above_usual(ctx) -> list[Insight]:
    """The biggest category of the cycle, against its average over up to three earlier cycles."""
    totals = ctx.summary(ctx.key).categories
    if not totals:
        return []
    top_key = max(totals, key=totals.get)
    top = totals[top_key]
    earlier = [v for v in (ctx.facts.category_series(*ctx.cycle_of(k), top_key) for k in ctx.keys_back(3)) if v > 0]
    if not earlier:
        return []
    average = sum(earlier, D(0)) / len(earlier)
    if not top > average * ABOVE_USUAL:
        return []
    pct = int((top - average) / average * 100)
    name = ctx.facts.names.get(top_key, top_key)
    fmt, (start, end) = ctx.fmt, ctx.cycle
    link = ctx.links.transactions(start, ctx.today, types=(EXPENSE,), category=name)
    return [Insight(
        rule_id='expenses.category_above_usual', module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM,
        priority=45, icon='bar-chart-line',
        title=_('%(category)s is a little above usual') % {'category': fmt.short(name)},
        message=_('%(category)s is about %(pct)s above your recent average so far, %(now)s against %(usual)s. '
                  'One higher stretch is common, especially around festivals and travel.') % {
            'category': name, 'pct': fmt.percent(pct), 'now': fmt.approx(top), 'usual': fmt.approx(average)},
        detail=_('If it is on purpose, you can dismiss this.'),
        evidence=(
            Evidence(_('This period'), fmt.money(top), link),
            Evidence(_('Recent average'), fmt.money(average)),
        ),
        action=Action(_('See the entries'), link),
        dedupe_key=f'{top_key[:60]}:{ctx.key:%Y-%m}', valid_until=end,
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'category': top_key, 'pct': pct},
    )]


def _is_posting(description: str) -> bool:
    """A recurring payment posted by the app, not typed by the person."""
    return '(recurring)' in description.lower()


def duplicate_groups(rows) -> list[list]:
    """Entries typed twice: same account, amount and description, added within a day of each other. Entries the app
    posted itself (recurring) and statement imports (which have their own duplicate check) are left out."""
    buckets = defaultdict(list)
    for row in rows:
        if row.from_import or _is_posting(row.description) or row.amount <= 0:
            continue
        buckets[(row.account_id, row.amount, row.description.strip().lower())].append(row)
    groups = []
    for entries in buckets.values():
        entries.sort(key=lambda r: r.created_at)
        cluster = [entries[0]]
        for row in entries[1:]:
            if row.created_at - cluster[-1].created_at <= DUPLICATE_GAP:
                cluster.append(row)
            else:
                if len(cluster) > 1:
                    groups.append(cluster)
                cluster = [row]
        if len(cluster) > 1:
            groups.append(cluster)
    return groups


@rule(id='expenses.possible_duplicate', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'recent'}, cooldown_days=0, sample=sampling.duplicate_sample)
def possible_duplicate(ctx) -> list[Insight]:
    """Two entries that look the same, added close together in the last few days."""
    out = []
    for group in duplicate_groups(ctx.recent_expenses):
        first = group[0]
        ids = sorted(r.id for r in group)
        link = ctx.links.transactions(first.date, first.date, types=(EXPENSE,))
        what = ctx.fmt.short(first.description, words=6, chars=40) or first.category
        out.append(Insight(
            rule_id='expenses.possible_duplicate', module=Module.EXPENSES, kind=Kind.OBSERVATION, tone=Tone.CALM,
            priority=58, icon='files',
            title=_('Two entries look alike'),
            message=_('%(n)s entries of %(amount)s for %(what)s were added close together on %(date)s. '
                      'If both are right, you can dismiss this; otherwise you could remove one.') % {
                'n': ctx.fmt.number(len(group)), 'amount': ctx.fmt.approx(first.amount), 'what': what,
                'date': ctx.fmt.day(first.date)},
            evidence=(Evidence(_('Amount'), ctx.fmt.money(first.amount), link),),
            action=Action(_('See the entries'), link),
            dedupe_key=f'dup:{ids[0]}:{ids[-1]}', valid_until=first.date + timedelta(days=7),
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'ids': ids},
        ))
    return out[:2]


@rule(id='expenses.unusual_amount', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'rows'}, cooldown_days=14, min_history=2, min_plan=Plan.PLUS, cost='heavy',
      preview=lambda: _('An entry larger than your usual'), sample=sampling.unusual_sample)
def unusual_amount(ctx) -> list[Insight]:
    """An entry from the last week that is several times what the person usually spends in that category (needs ten
    earlier entries over two cycles, so "usual" means something). Duplicates and app-posted entries are skipped."""
    rows = ctx.expense_rows
    week_ago = ctx.today - timedelta(days=7)
    skip = {r.id for group in duplicate_groups(rows) for r in group}
    prior = defaultdict(list)
    for row in rows:
        if row.date < week_ago and row.amount > 0 and not _is_posting(row.description):
            prior[row.category.strip().lower()].append(row)
    found = []
    for row in rows:
        key = row.category.strip().lower()
        history = prior.get(key, [])
        if row.date < week_ago or row.id in skip or _is_posting(row.description) or len(history) < MIN_PRIOR_ENTRIES:
            continue
        if len({budget_cycle.budget_month_of(ctx.salary_day, r.date) for r in history}) < 2:
            continue
        amounts = sorted(r.amount for r in history)
        usual = D(statistics.median(amounts))
        p90 = amounts[int((len(amounts) - 1) * 0.9)]
        if usual > 0 and row.amount >= max(BIG_FACTOR * usual, D('1.5') * p90):
            found.append((row.amount / usual, row, usual))
    out = []
    for ratio, row, usual in sorted(found, key=lambda t: -t[0])[:2]:
        fmt = ctx.fmt
        link = ctx.links.transactions(row.date, row.date, types=(EXPENSE,), category=row.category)
        out.append(Insight(
            rule_id='expenses.unusual_amount', module=Module.EXPENSES, kind=Kind.OBSERVATION, tone=Tone.CALM,
            priority=48, icon='receipt',
            title=_('A larger %(category)s entry') % {'category': fmt.short(row.category)},
            message=_('An entry of %(amount)s on %(date)s is about %(times)s times your usual %(category)s amount '
                      'of %(usual)s. If it was planned, you can dismiss this.') % {
                'amount': fmt.approx(row.amount), 'date': fmt.day(row.date), 'times': fmt.number(round(ratio)),
                'category': row.category, 'usual': fmt.approx(usual)},
            evidence=(Evidence(_('This entry'), fmt.money(row.amount), link), Evidence(_('Usual amount'), fmt.money(usual))),
            action=Action(_('See the entry'), link),
            dedupe_key=f'big:{row.id}', valid_until=row.date + timedelta(days=14),
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'id': row.id, 'times': round(ratio)},
        ))
    return out


@rule(id='expenses.busiest_weekday', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'facts'}, cooldown_days=28, min_history=2, pool='weekly', sample=sampling.weekday_sample)
def busiest_weekday(ctx) -> list[Insight]:
    """One day of the week that, over the last eight weeks, averages well above the others (everyday spending only)."""
    end = ctx.today - timedelta(days=1)
    start = end - timedelta(days=WEEKS * 7 - 1)
    series = ctx.facts.daily_expenses(start, end)
    if len(series) != WEEKS * 7 or sum(1 for v in series if v > 0) < 20:
        return []
    totals = [D(0)] * 7
    for offset, value in enumerate(series):
        totals[(start + timedelta(days=offset)).weekday()] += value
    averages = [t / WEEKS for t in totals]
    top = max(range(7), key=lambda i: averages[i])
    others = sum((a for i, a in enumerate(averages) if i != top), D(0)) / 6
    if others <= 0 or averages[top] < WEEKDAY_FACTOR * others:
        return []
    fmt = ctx.fmt
    link = ctx.links.transactions(start, end, types=(EXPENSE,))
    day = str(WEEKDAYS[top])
    return [Insight(
        rule_id='expenses.busiest_weekday', module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM,
        priority=30, icon='calendar-week',
        title=_('%(day)s tends to be your busiest day') % {'day': day},
        message=_('Over the last 8 weeks, %(day)s averaged about %(top)s, against about %(other)s on other days. '
                  'Plans and outings often gather around the same day, so this is usually just your routine.') % {
            'day': day, 'top': fmt.approx(averages[top]), 'other': fmt.approx(others)},
        evidence=(Evidence(day, fmt.money(averages[top]), link), Evidence(_('Other days'), fmt.money(others))),
        dedupe_key=f'weekday:{top}:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'weekday': top},
    )]


@rule(id='expenses.spending_timing', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'facts'}, cooldown_days=45, min_history=3, pool='weekly', sample=sampling.default_sample)
def spending_timing(ctx) -> list[Insight]:
    """Most of the spending lands in the first ten days of the cycle, in each of the last three."""
    shares = []
    for key in ctx.keys_back(3):
        start, end = ctx.cycle_of(key)
        series = ctx.facts.daily_expenses(start, end)
        total = sum(series, D(0))
        if total <= 0:
            return []
        shares.append(sum(series[:EARLY_DAYS], D(0)) / total)
    if min(shares) < EARLY_SHARE:
        return []
    average = sum(shares, D(0)) / len(shares) * 100
    message = (_('In each of your last 3 cycles, about %(pct)s of spending landed in the first %(days)s days. '
                 'That is common when rent and bills fall early.') if ctx.is_cycle else
               _('In each of your last 3 months, about %(pct)s of spending landed in the first %(days)s days. '
                 'That is common when rent and bills fall early.')) % {
        'pct': ctx.fmt.percent(average), 'days': ctx.fmt.number(EARLY_DAYS)}
    first, last = ctx.cycle_of(ctx.keys_back(3)[-1])[0], ctx.cycle_of(ctx.keys_back(1)[0])[1]
    return [Insight(
        rule_id='expenses.spending_timing', module=Module.EXPENSES, kind=Kind.PATTERN, tone=Tone.CALM,
        priority=28, icon='hourglass-top',
        title=_('Most spending comes early'), message=message,
        evidence=(Evidence(_('Share in the first days'), ctx.fmt.percent(average),
                           ctx.links.transactions(first, last, types=(EXPENSE,))),),
        dedupe_key=f'timing:{ctx.key:%Y-%m}', valid_until=ctx.cycle[1],
        surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
        numbers={'pct': int(average)},
    )]


@rule(id='expenses.category_change', module=Module.EXPENSES, surfaces={Surface.DASHBOARD, Surface.MODULE},
      needs={'facts'}, cooldown_days=14, min_history=1, sample=sampling.default_sample)
def category_change(ctx) -> list[Insight]:
    """The category that is furthest up, and the one furthest down, against the same point last time (the same number
    of days into the previous cycle), when the move is at least 10% of the category and a meaningful share of last
    time's whole spending. Comparing like with like means a quiet first week is not called a saving, and a category with
    nothing spent yet (a bill not paid yet) is not called lower."""
    elapsed = ctx.elapsed_days
    if elapsed < MIN_DAYS_IN:
        return []
    start, _end = ctx.cycle
    before_start = ctx.cycle_of(ctx.keys_back(1)[0])[0]
    before_end = before_start + timedelta(days=elapsed - 1)
    now = ctx.facts.summarize(start, ctx.today).categories
    before = ctx.facts.summarize(before_start, before_end).categories
    whole_before = sum(before.values(), D(0))
    if whole_before <= 0:
        return []
    moves = []
    for key in set(now) | set(before):
        was, is_now = before.get(key, D(0)), now.get(key, D(0))
        # A category with nothing yet this cycle is a bill not paid yet, not a saving: only a category still in use is "lower"
        if was > 0 and is_now > 0 and abs(is_now - was) / was >= SHIFT and abs(is_now - was) >= MATERIAL * whole_before:
            moves.append((is_now - was, key, was, is_now))
    out = []
    fmt, end = ctx.fmt, ctx.cycle[1]
    for delta, key, was, is_now in ([max(moves, key=lambda m: m[0])] if moves and max(moves)[0] > 0 else []) + \
            ([min(moves, key=lambda m: m[0])] if moves and min(moves)[0] < 0 else []):
        name = ctx.facts.names.get(key, key)
        pct = abs(delta) / was * 100
        link = ctx.links.transactions(start, ctx.today, types=(EXPENSE,), category=name)
        up = delta > 0
        args = {'category': name, 'pct': fmt.percent(pct), 'now': fmt.approx(is_now), 'before': fmt.approx(was)}
        out.append(Insight(
            rule_id='expenses.category_change', module=Module.EXPENSES, kind=Kind.PATTERN,
            tone=Tone.CALM if up else Tone.POSITIVE, priority=40 if up else 35,
            icon='arrow-up-right' if up else 'arrow-down-right',
            title=(_('%(category)s is a bit higher') if up else _('%(category)s is a bit lower')) % {
                'category': fmt.short(name)},
            message=(_('%(category)s is about %(pct)s higher than at this point last time, %(now)s so far against '
                       '%(before)s. Spending moves in waves, so this may be nothing to act on.') if up else
                     _('%(category)s is about %(pct)s lower than at this point last time, %(now)s so far against '
                       '%(before)s. Quieter stretches like this can leave room for other things.')) % args,
            evidence=(Evidence(_('So far'), fmt.money(is_now), link), Evidence(_('Same point last time'), fmt.money(was))),
            action=Action(_('See the entries'), link),
            dedupe_key=f"{'up' if up else 'down'}:{key[:50]}:{ctx.key:%Y-%m}", valid_until=end,
            surfaces=frozenset({Surface.DASHBOARD, Surface.MODULE}),
            numbers={'category': key, 'direction': 'up' if up else 'down', 'pct': int(pct)},
        ))
    return out
