"""What the Year in Review ("Wrapped") says about a year, beyond the totals in utils.generate_year_in_review_data.

Everything here is worked out from the person's own records by fixed rules, so the same data always gives the
same story and every claim can be explained. The cards split in two:

  * the Spending story is built only from what was bought (no income, no net worth), so it is safe to share;
  * the Money story (savings rate, net worth) is income-based and is shared only when the person chooses.

`build_wrapped` is pure aggregation: one pass over the year's expenses in Python after a single query.
"""
from __future__ import annotations

import datetime
import statistics
from collections import Counter, defaultdict
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

ZERO = Decimal('0')

# A personality needs enough of a year behind it to mean something.
MIN_EXPENSES = 20
MIN_MONTHS = 3
FOOD_WORDS = ('food', 'dining', 'restaurant', 'cafe', 'coffee', 'swiggy', 'zomato', 'snack', 'eating')
TRAVEL_WORDS = ('travel', 'trip', 'flight', 'hotel', 'vacation', 'holiday', 'cab', 'taxi')


def _period(year: int, today: datetime.date) -> tuple[datetime.date, datetime.date]:
    end = min(datetime.date(year, 12, 31), today) if year == today.year else datetime.date(year, 12, 31)
    return datetime.date(year, 1, 1), end


def _pct(part, whole) -> int:
    return int(round(float(part) / float(whole) * 100)) if whole else 0


def _contains(name: str, words) -> bool:
    name = (name or '').lower()
    return any(w in name for w in words)


def _longest_gap(days: set, start: datetime.date, end: datetime.date) -> tuple[int, datetime.date | None]:
    """The longest run of days in [start, end] with no entry: (length, first day of the run)."""
    best, best_start, run, run_start = 0, None, 0, None
    day = start
    while day <= end:
        if day in days:
            run, run_start = 0, None
        else:
            if run == 0:
                run_start = day
            run += 1
            if run > best:
                best, best_start = run, run_start
        day += datetime.timedelta(days=1)
    return best, best_start


def _longest_week_streak(days: set) -> int:
    """The most weeks in a row (Monday to Sunday) with at least one entry."""
    weeks = sorted({d.isocalendar()[:2] for d in days})
    if not weeks:
        return 0
    ordinals = sorted({datetime.date.fromisocalendar(y, w, 1).toordinal() // 7 for y, w in weeks})
    best = run = 1
    for a, b in zip(ordinals, ordinals[1:], strict=False):
        run = run + 1 if b == a + 1 else 1
        best = max(best, run)
    return best


def _personality(f: dict) -> dict:
    """A name and the one-line reason for it, from the first rule that fits. `f` holds the facts."""
    if f['expense_count'] < MIN_EXPENSES or f['months_with_spend'] < MIN_MONTHS:
        return {'key': 'getting_started', 'name': _('The Getting-Started Tracker'),
                'reason': _('Your year of tracking has just begun. The story gets richer with every entry.'),
                'traits': [_('Early days')]}
    rules = []
    if f['biggest_share'] >= 20:
        rules.append(('big_ticket', _('The Big-Ticket Buyer'),
                      _('One purchase made up %(pct)s%% of everything you spent.') % {'pct': f['biggest_share']},
                      [_('Planned splurges'), _('Few, large purchases')]))
    if f['weekend_share'] >= 45:
        rules.append(('weekend', _('The Weekend Splurger'),
                      _('%(pct)s%% of your spending happened on a Saturday or Sunday.') % {'pct': f['weekend_share']},
                      [_('Weekend-heavy'), _('Weekday-light')]))
    if f['food_share'] >= 25:
        rules.append(('foodie', _('The Foodie'),
                      _('Food and dining took %(pct)s%% of everything you spent.') % {'pct': f['food_share']},
                      [_('Good taste'), _('Eats out')]))
    if f['travel_share'] >= 15:
        rules.append(('explorer', _('The Explorer'),
                      _('Travel took %(pct)s%% of your spending, more than most things.') % {'pct': f['travel_share']},
                      [_('Always going somewhere')]))
    if f['no_spend_share'] >= 25:
        rules.append(('minimalist', _('The Minimalist'),
                      _('You spent nothing on %(pct)s%% of the days.') % {'pct': f['no_spend_share']},
                      [_('Intentional'), _('Quiet days')]))
    if f['per_day'] >= 3:
        rules.append(('everyday', _('The Everyday Spender'),
                      _('About %(n)s purchases a day: it is the small ones that add up.') % {'n': f['per_day_text']},
                      [_('Many small purchases'), _('Detail-minded')]))
    if f['steadiness'] is not None and f['steadiness'] <= 0.25:
        rules.append(('steady', _('The Steady Planner'),
                      _('Your monthly spending stayed within a narrow band all year.'),
                      [_('Consistent'), _('Predictable')]))
    if rules:
        key, name, reason, traits = rules[0]
        return {'key': key, 'name': name, 'reason': reason, 'traits': traits}
    return {'key': 'balanced', 'name': _('The Balanced Spender'),
            'reason': _('No single habit ran the year: your spending was spread evenly.'),
            'traits': [_('Well-rounded')]}


def build_wrapped(user, year: int, review: dict) -> dict:
    """The extra story for `year`. `review` is the dict from generate_year_in_review_data (needs has_data)."""
    from .models import Expense, Income

    out: dict = {'has_story': False}
    if not review.get('has_data'):
        return out

    today = timezone.localdate()
    start, end = _period(year, today)
    rows = list(Expense.objects.filter(user=user, date__gte=start, date__lte=end)
                .values_list('date', 'base_amount', 'description', 'category'))
    if not rows:
        return out
    total = sum((r[1] or ZERO for r in rows), ZERO)
    days_with_spend = {r[0] for r in rows}
    income_days = set(Income.objects.filter(user=user, date__gte=start, date__lte=end).values_list('date', flat=True))
    days_logged = days_with_spend | income_days
    span = (end - start).days + 1

    by_month: dict[int, Decimal] = defaultdict(lambda: ZERO)
    by_weekday_count: Counter = Counter()
    weekend_total = ZERO
    by_category: dict[str, Decimal] = defaultdict(lambda: ZERO)
    stops: Counter = Counter()
    stop_names: dict[str, str] = {}
    biggest = ZERO
    for day, amount, description, category in rows:
        amount = amount or ZERO
        by_month[day.month] += amount
        by_weekday_count[day.weekday()] += 1
        if day.weekday() >= 5:
            weekend_total += amount
        by_category[category or ''] += amount
        biggest = max(biggest, amount)
        key = (description or '').strip().lower() if isinstance(description, str) else ''
        if key:
            stops[key] += 1
            stop_names.setdefault(key, description.strip())

    # ---- the Spending story ----
    ranked = sorted(by_category.items(), key=lambda kv: -kv[1])
    top = [{'name': name or _('Uncategorised'), 'total': amount, 'pct': _pct(amount, total)}
           for name, amount in ranked[:3]]
    if top:
        top[0]['width'] = 100
        for item in top[1:]:
            item['width'] = max(6, int(round(float(item['total']) / float(top[0]['total']) * 100)))

    weekday_names = [_('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'), _('Sunday')]
    busy_day, busy_count = by_weekday_count.most_common(1)[0]
    gap, gap_start = _longest_gap(days_with_spend, start, end)
    stop_key, stop_count = stops.most_common(1)[0] if stops else ('', 0)
    facts = {
        'busiest_weekday': {'name': weekday_names[busy_day], 'pct': _pct(busy_count, len(rows))},
        'no_spend_streak': ({'days': gap, 'start': gap_start, 'end': gap_start + datetime.timedelta(days=gap - 1)}
                            if gap >= 3 and gap_start else None),
        'favourite_stop': ({'name': stop_names[stop_key], 'count': stop_count} if stop_count >= 3 else None),
        'purchases': len(rows),
        'per_day': round(len(rows) / span, 1),
    }

    glow = []
    prev_rows = Expense.objects.filter(user=user, date__year=year - 1)
    if prev_rows.exists():
        previous: dict[str, Decimal] = defaultdict(lambda: ZERO)
        from django.db.models import Sum
        for name, amount in prev_rows.values_list('category').annotate(t=Sum('base_amount')):
            previous[name or ''] = amount or ZERO
        for name, amount in ranked[:8]:
            before = previous.get(name, ZERO)
            if before > 0:
                glow.append({'name': name or _('Uncategorised'),
                             'change': int(round((float(amount) - float(before)) / float(before) * 100))})
        glow = sorted((g for g in glow if g['change'] != 0), key=lambda g: -abs(g['change']))[:4]

    month_names = [_('January'), _('February'), _('March'), _('April'), _('May'), _('June'), _('July'),
                   _('August'), _('September'), _('October'), _('November'), _('December')]
    months = sorted(by_month.items(), key=lambda kv: kv[1])
    best_month = month_names[months[0][0] - 1] if len(months) >= 2 else None
    priciest_month = month_names[months[-1][0] - 1] if len(months) >= 2 else None

    milestones = {
        'days_logged': len(days_logged),
        'week_streak': _longest_week_streak(days_logged),
        'goals': review.get('goals_completed') or 0,
    }

    month_values = [float(v) for v in by_month.values()]
    steadiness = None
    if len(month_values) >= MIN_MONTHS and statistics.mean(month_values) > 0:
        steadiness = statistics.pstdev(month_values) / statistics.mean(month_values)
    facts_for_rules = {
        'expense_count': len(rows),
        'months_with_spend': len(by_month),
        'biggest_share': _pct(biggest, total),
        'weekend_share': _pct(weekend_total, total),
        'food_share': _pct(sum((a for n, a in by_category.items() if _contains(n, FOOD_WORDS)), ZERO), total),
        'travel_share': _pct(sum((a for n, a in by_category.items() if _contains(n, TRAVEL_WORDS)), ZERO), total),
        'no_spend_share': _pct(span - len(days_with_spend), span),
        'per_day': len(rows) / span,
        'per_day_text': f'{len(rows) / span:.1f}',
        'steadiness': steadiness,
    }

    out.update({
        'has_story': True,
        'personality': _personality(facts_for_rules),
        'top': top,
        'facts': facts,
        'glow': glow,
        'best_month': best_month,
        'priciest_month': priciest_month,
        'milestones': milestones,
        'prev_year': year - 1,
    })

    # ---- the Money story (income based; shared only on request) ----
    money = {'savings_rate': review.get('savings_rate'), 'net_saved': review.get('net_saved'),
             'earned': review.get('total_earned'), 'invested': review.get('total_invested'),
             'has_income': bool(review.get('total_earned'))}
    rate = money['savings_rate']
    money['ring_pct'] = max(0, min(100, int(round(float(rate))))) if rate is not None else 0
    money['invested_pct'] = _pct(review.get('total_invested') or 0, review.get('total_earned') or 0)
    try:
        from .ledger_read_service import LedgerReadService
        before = LedgerReadService.get_net_worth(user, as_of=start - datetime.timedelta(days=1))
        now = LedgerReadService.get_net_worth(user, as_of=end)
        money['net_worth_change'] = now - before
        money['net_worth_pct'] = int(round(float(now - before) / abs(float(before)) * 100)) if before else None
    except Exception:    # a missing rate or valuation must not take the whole story down
        money['net_worth_change'] = None
        money['net_worth_pct'] = None
    out['money'] = money
    from .insights import composers
    out['insights'] = [{'title': i.title, 'message': i.message} for i in composers.year_lines(user, year)]
    return out
