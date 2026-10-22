"""What every Analytics tab starts from: the toolbar's choices, the period, the facts, and the totals tiles."""
from __future__ import annotations

import hashlib
from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone, translation
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from ..filters.definitions import ANALYTICS_FILTERS
from ..filters.engine import apply_filter_config
from ..models import Account, Expense
from ..utils import translate_digits
from .facts import Facts, Summary, earliest_date, load_facts
from .links import INCOME, SPENDING_TYPES, TRANSFER, LinkBuilder
from .money import Money, change, percent
from .periods import AnalyticsPeriod, Bucket, buckets, build_period

TABS = ('spend', 'cashflow', 'networth')
CAPITAL_INCLUDE = 'include'


class Page:
    """The shared inputs of one Analytics request. Heavy parts (facts, summaries) load on first use."""

    def __init__(self, request, tab: str):
        self.request = request
        self.user = request.user
        self.tab = tab
        self.today = timezone.localdate()
        profile = getattr(self.user, 'profile', None)
        self.money = Money(getattr(profile, 'currency', None) or '₹')

        # the toolbar's own parser: the period, the chips, and the state the toolbar redraws itself from
        _, self.state = apply_filter_config(Expense.objects.none(), request, ANALYTICS_FILTERS)
        chosen = self.state['filters']
        self.categories: list[str] = list(chosen.get('category', []))
        wanted = [int(a) for a in chosen.get('account', []) if str(a).isdigit()]
        self.account_ids: list[int] = list(
            Account.objects.filter(user=self.user, id__in=wanted).values_list('id', flat=True)) if wanted else []
        self.include_capital_events = CAPITAL_INCLUDE in chosen.get('capital_events', [])

        needs_start = self.state['time_period'] == 'all' or (
            self.state['time_period'] == 'custom' and not self.state['start_date'])
        self.period: AnalyticsPeriod = build_period(
            self.user, self.state, self.today, earliest=earliest_date(self.user) if needs_start else None)
        self.links = LinkBuilder(self.categories, self.account_ids)
        self._facts: Facts | None = None
        self._summary: Summary | None = None
        self._compare: Summary | None = None

    # ---- data ----
    @property
    def facts(self) -> Facts:
        if self._facts is None:
            first = self.period.compare[0] if self.period.compare else self.period.start
            first = min(first, self.period.start)
            self._facts = load_facts(self.user, first, self.period.end, categories=self.categories,
                                     account_ids=self.account_ids, include_capital_events=self.include_capital_events)
        return self._facts

    @property
    def summary(self) -> Summary:
        if self._summary is None:
            self._summary = self.facts.summarize(*self.period.range)
        return self._summary

    @property
    def compare(self) -> Summary | None:
        if self.period.compare and self._compare is None:
            self._compare = self.facts.summarize(*self.period.compare)
        return self._compare

    @property
    def filters_active(self) -> bool:
        return bool(self.categories or self.account_ids or self.include_capital_events)

    # ---- naming ----
    def category_name(self, key: str) -> str:
        raw = self.facts.names.get(key, key)
        return _(raw)

    def compare_name(self) -> str:
        return {'last_year': _('the same dates last year'), 'last_month': _('the same days last month'),
                'before': _('the period before')}.get(self.period.compare_kind or '', '')

    def range_text(self, start: date, end: date) -> str:
        if start == end:
            return translate_digits(date_format(start, 'j M Y'))
        if start.year == end.year:
            return translate_digits(f"{date_format(start, 'j M')} – {date_format(end, 'j M Y')}")
        return translate_digits(f"{date_format(start, 'j M Y')} – {date_format(end, 'j M Y')}")

    # ---- bars ----
    def bucket_list(self, start: date | None = None, end: date | None = None) -> list[Bucket]:
        return buckets(start or self.period.start, end or self.period.end, self.period.granularity)

    def bucket_label(self, bucket: Bucket, long: bool = False) -> str:
        g, spans_years = self.period.granularity, self.period.start.year != self.period.end.year
        if g == 'day':
            text = date_format(bucket.start, 'j M Y' if long else 'j')
        elif g == 'week':
            text = (f"{date_format(bucket.start, 'j M')} – {date_format(bucket.end, 'j M')}" if long
                    else date_format(bucket.start, 'j M'))
        elif g == 'month':
            text = date_format(bucket.start, 'F Y' if long else ('M y' if spans_years else 'M'))
        else:
            quarter = (bucket.start.month - 1) // 3 + 1
            text = f'Q{quarter} {bucket.start.year}' if long else f"Q{quarter} '{str(bucket.start.year)[2:]}"
        return translate_digits(text)

    def link_for(self, bucket: Bucket, types=(), **kwargs) -> str:
        return self.links.transactions(bucket.start, bucket.end, types, **kwargs)

    # ---- the totals tiles shared by Spending and Cashflow ----
    def tiles(self) -> list[dict]:
        now, before = self.summary, self.compare
        start, end = self.period.range
        money = self.money

        def delta(current, previous, higher_is_good: bool):
            pct, direction = change(current, previous)
            if before is None or pct is None:
                return '', 'flat'
            arrow = '▲' if direction == 'up' else '▼' if direction == 'down' else '•'
            good = (direction == 'up') == higher_is_good if direction != 'flat' else None
            return f'{arrow} {percent(abs(pct))}', ('flat' if good is None else 'good' if good else 'bad')

        income_text, income_class = delta(now.income, before and before.income, True)
        spend_text, spend_class = delta(now.spending, before and before.spending, False)
        rate_text, rate_class = '', 'flat'
        if before is not None:
            diff = now.rate - before.rate
            rate_text = f"{'▲' if diff > 0 else '▼' if diff < 0 else '•'} {translate_digits(f'{abs(float(diff)):.0f}')} {_('pts')}"
            rate_class = 'flat' if diff == 0 else 'good' if diff > 0 else 'bad'
        invested_share = (now.invested / now.income * 100) if now.income else None
        investment_accounts = self._investment_account_ids()
        return [
            {'key': 'income', 'label': _('Income'), 'value': money.full(now.income), 'delta': income_text,
             'delta_class': income_class, 'href': self.links.transactions(start, end, [INCOME], accounts=self.account_ids)},
            {'key': 'spending', 'label': _('Spending'), 'value': money.full(now.spending), 'delta': spend_text,
             'delta_class': spend_class, 'href': self.links.transactions(start, end, SPENDING_TYPES)},
            {'key': 'invested', 'label': _('Invested'), 'value': money.full(now.invested),
             'delta': (_('%(pct)s of income') % {'pct': percent(invested_share)}) if invested_share is not None else '',
             'delta_class': 'flat', 'href': self.links.transactions(start, end, [TRANSFER], accounts=investment_accounts)},
            {'key': 'rate', 'label': _('Savings rate'), 'value': percent(now.rate, 1), 'delta': rate_text,
             'delta_class': rate_class, 'href': self.links.transactions(start, end)},
        ]

    def insight(self) -> str:
        """The one sentence under the tiles. It is the `analytics.period_summary` rule's, worked out for this page."""
        from ..insights import engine
        from ..insights.pagectx import PageContext
        found = engine.inline(PageContext(self), ['analytics.period_summary'], limit=1)
        return found[0].message if found else ''

    def _investment_account_ids(self) -> list[int]:
        from ..account_types import investment_codes
        return list(Account.objects.filter(user=self.user, is_active=True,
                                           account_type__in=list(investment_codes())).values_list('id', flat=True))


# ---- caching of a finished tab ----

def cache_seconds() -> int:
    return int(getattr(settings, 'ANALYTICS_CACHE_SECONDS', 300))


def _version_key(user_id: int) -> str:
    return f'analytics_version_{user_id}'


def _version(user_id: int) -> str:
    key = _version_key(user_id)
    version = cache.get(key)
    if version is None:
        version = str(timezone.now().timestamp())
        cache.set(key, version, 86400)
    return version


def cache_key(page: Page) -> str:
    """One entry per user, tab, language and filter combination; a write by the user changes the version."""
    query = '&'.join(f'{k}={v}' for k, vals in sorted(page.request.GET.lists()) for v in sorted(vals) if k != '_')
    digest = hashlib.md5(f'{page.today}|{query}'.encode()).hexdigest()
    return f'analytics:{page.user.id}:{_version(page.user.id)}:{page.tab}:{translation.get_language()}:{digest}'
