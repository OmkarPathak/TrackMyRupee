"""The Cashflow tab: where income went (a Sankey), four plain health numbers, and the period against its comparison."""
from __future__ import annotations

from decimal import Decimal

from django.utils.translation import gettext as _

from .common import Page
from .links import CAPITAL_EVENT, EXPENSE, INCOME, LOAN, TRANSFER
from .money import change, percent
from .networth import net_worth_points
from .periods import buckets

ZERO = Decimal('0')
SANKEY_CATEGORIES = 5
SANKEY_ACCOUNTS = 3


def _node(node_id, label, value, level, tone, page: Page, income, href=None, before=None, bad_when_up=True):
    pct, direction = change(value, before) if before is not None else (None, 'flat')
    tone_change = 'flat' if direction == 'flat' else ('bad' if (direction == 'up') == bad_when_up else 'good')
    return {
        'id': node_id, 'label': label, 'value': float(value), 'value_text': page.money.full(value), 'level': level,
        'tone': tone, 'href': href, 'share': float(value / income * 100) if income else 0.0,
        'share_text': percent(value / income * 100) if income else '',
        'delta': {'text': (('▲ ' if direction == 'up' else '▼ ') + percent(abs(pct))) if pct is not None else '',
                  'tone': tone_change},
    }


def sankey(page: Page) -> dict:
    """Income flowing into what it paid for: costs, interest, investing and what was kept; costs and investing
    split one level further into the biggest categories and the accounts the money went to."""
    now, before = page.summary, page.compare
    start, end = page.period.range
    links = page.links
    income = now.income
    living = now.spending - now.savings.loan_interest
    interest, invested = now.savings.loan_interest, now.invested
    needs = living + interest + invested
    kept = max(ZERO, income - needs)
    shortfall = max(ZERO, needs - income)
    before_living = (before.spending - before.savings.loan_interest) if before else None
    base = income + shortfall

    nodes: list[dict] = []
    flows: list[dict] = []

    def add(node):
        nodes.append(node)
        return node

    if income > 0:
        add(_node('income', _('Income'), income, 0, 'income', page, base,
                  links.transactions(start, end, (INCOME,), accounts=page.account_ids), before and before.income, False))
    if shortfall > 0:
        add(_node('shortfall', _('Paid from savings'), shortfall, 0, 'shortfall', page, base))
    if living > 0:
        add(_node('living', _('Living costs'), living, 1, 'spend', page, base,
                  links.transactions(start, end, (EXPENSE, CAPITAL_EVENT)), before_living))
    if interest > 0:
        add(_node('interest', _('Loan interest'), interest, 1, 'interest', page, base,
                  links.transactions(start, end, (LOAN,)), before and before.savings.loan_interest))
    if invested > 0:
        add(_node('invested', _('Invested'), invested, 1, 'invest', page, base,
                  links.transactions(start, end, (TRANSFER,), accounts=page._investment_account_ids()),
                  before and before.invested, False))
    if kept > 0:
        add(_node('kept', _('Kept as cash'), kept, 1, 'kept', page, base, links.transactions(start, end)))

    # income pays for costs first, in this order; whatever it cannot cover is the shortfall
    available = income
    for node_id, amount in (('living', living), ('interest', interest), ('invested', invested)):
        if amount <= 0:
            continue
        from_income = min(amount, available)
        available -= from_income
        if from_income > 0:
            flows.append({'source': 'income', 'target': node_id, 'value': float(from_income)})
        if amount - from_income > 0:
            flows.append({'source': 'shortfall', 'target': node_id, 'value': float(amount - from_income)})
    if kept > 0:
        flows.append({'source': 'income', 'target': 'kept', 'value': float(kept)})

    if living > 0:
        ranked = sorted(now.categories.items(), key=lambda item: item[1], reverse=True)
        for key, amount in ranked[:SANKEY_CATEGORIES]:
            name = page.facts.names.get(key, key)
            add(_node(f'cat:{key}', page.category_name(key), amount, 2, 'cat', page, base,
                      links.transactions(start, end, (EXPENSE, CAPITAL_EVENT), category=name),
                      before.categories.get(key, ZERO) if before else None))
            flows.append({'source': 'living', 'target': f'cat:{key}', 'value': float(amount)})
        rest = sum((amount for _k, amount in ranked[SANKEY_CATEGORIES:]), ZERO)
        if rest > 0:
            add(_node('cat:rest', _('Other costs'), rest, 2, 'cat', page, base,
                      links.transactions(start, end, (EXPENSE, CAPITAL_EVENT))))
            flows.append({'source': 'living', 'target': 'cat:rest', 'value': float(rest)})
    if invested > 0 and now.invested_to:
        ranked = sorted(now.invested_to.items(), key=lambda item: item[1], reverse=True)
        for (account_id, name), amount in ranked[:SANKEY_ACCOUNTS]:
            add(_node(f'acc:{account_id}', name, amount, 2, 'invest', page, base,
                      links.transactions(start, end, (TRANSFER,), accounts=[account_id])))
            flows.append({'source': 'invested', 'target': f'acc:{account_id}', 'value': float(amount)})
        rest = sum((amount for _k, amount in ranked[SANKEY_ACCOUNTS:]), ZERO)
        if rest > 0:
            add(_node('acc:rest', _('Other accounts'), rest, 2, 'invest', page, base,
                      links.transactions(start, end, (TRANSFER,), accounts=page._investment_account_ids())))
            flows.append({'source': 'invested', 'target': 'acc:rest', 'value': float(rest)})

    by_id = {node['id']: node for node in nodes}
    rows = []
    for node in nodes:
        if node['level'] == 1:
            rows.append(node)
            rows.extend(by_id[f['target']] for f in flows if f['source'] == node['id'] and by_id[f['target']]['level'] == 2)
    for flow in flows:
        flow['value_text'] = page.money.full(flow['value'])
    return {'nodes': nodes, 'links': flows, 'rows': rows, 'total': float(base), 'symbol': page.money.symbol,
            'labels': {'amount': _('Amount'), 'share': _('Share of income'), 'change': _('Change'),
                       'cta': _('View transactions')},
            'takeaways': takeaways(page, income, kept, shortfall, invested, before)}


def takeaways(page: Page, income, kept, shortfall, invested, before) -> list[str]:
    """Up to three plain sentences about the flow: the biggest cost, what happened to the money, the biggest mover."""
    now, out = page.summary, []
    ranked = sorted(now.categories.items(), key=lambda item: item[1], reverse=True)
    if ranked and income > 0:
        key, amount = ranked[0]
        out.append(_('%(name)s is your biggest cost: %(amount)s, %(pct)s of your income.') % {
            'name': page.category_name(key), 'amount': page.money.full(amount), 'pct': percent(amount / income * 100)})
    overspent = now.spending - income
    if overspent > 0:
        out.append(_('You spent %(amount)s more than you earned in this period.') % {'amount': page.money.full(overspent)})
    elif shortfall > 0:
        out.append(_('Your costs and investing came to %(amount)s more than your income, paid from savings you already had.')
                   % {'amount': page.money.full(shortfall)})
    elif income > 0:
        out.append(_('You invested %(invested)s and kept %(kept)s of your income as cash.') % {
            'invested': percent(invested / income * 100), 'kept': percent(kept / income * 100)})
    if before is not None:
        movers = [(key, amount - before.categories.get(key, ZERO)) for key, amount in now.categories.items()]
        movers = [m for m in movers if before.categories.get(m[0], ZERO) > 0]
        if movers:
            key, diff = max(movers, key=lambda m: abs(m[1]))
            if diff != 0:
                out.append((_('%(name)s went up by %(amount)s compared with %(then)s.') if diff > 0
                            else _('%(name)s went down by %(amount)s compared with %(then)s.')) % {
                    'name': page.category_name(key), 'amount': page.money.full(abs(diff)), 'then': page.compare_name()})
    return out[:3]


def health(page: Page) -> list[dict]:
    """Four plain numbers instead of one invented score; each says what it measures."""
    now, before = page.summary, page.compare
    start, end = page.period.range
    rows = []

    rate = float(now.rate)
    rows.append({'label': _('Savings rate'), 'hint': _('How much of your income you kept.'),
                 'value': percent(rate), 'tone': 'good' if rate >= 20 else 'warn' if rate > 0 else 'bad',
                 'href': page.links.transactions(start, end)})

    pct, _direction = change(now.spending, before.spending) if before else (None, 'flat')
    if pct is None:
        rows.append({'label': _('Spending trend'), 'hint': _('Needs an earlier period to compare with.'),
                     'value': '—', 'tone': 'none', 'href': page.links.transactions(start, end, (EXPENSE,))})
    else:
        value = float(pct)
        rows.append({'label': _('Spending trend'), 'hint': _('Against %(then)s.') % {'then': page.compare_name()},
                     'value': ('+' if value > 0 else '−' if value < 0 else '') + percent(abs(value)),
                     'tone': 'good' if value <= 0 else 'warn' if value <= 15 else 'bad',
                     'href': page.links.transactions(start, end, (EXPENSE,))})

    months = max(page.period.days / 30.4, 1)
    monthly = now.spending / Decimal(str(months)) if now.spending else ZERO
    cash = net_worth_points(page.user, [page.today], page.today)[0].groups.get('cash', ZERO)
    if monthly > 0:
        runway = float(cash / monthly)
        rows.append({'label': _('Emergency fund'), 'hint': _('Cash and bank balances, in months of your spending.'),
                     'value': _('%(n)s months') % {'n': f'{max(runway, 0):.1f}'},
                     'tone': 'good' if runway >= 6 else 'warn' if runway >= 3 else 'bad',
                     'href': page.links.accounts(['SAVINGS_ACCOUNT', 'SALARY_ACCOUNT', 'CURRENT_ACCOUNT', 'CASH_WALLET'])})
    else:
        rows.append({'label': _('Emergency fund'), 'hint': _('Needs some spending in this period.'), 'value': '—',
                     'tone': 'none', 'href': page.links.accounts()})

    active = [s for s in (page.facts.summarize(b.start, b.end) for b in buckets(start, end, 'month'))
              if s.income > 0 or s.spending > 0]
    if len(active) >= 2:
        in_black = sum(1 for s in active if s.saved > 0)
        share = in_black / len(active)
        rows.append({'label': _('Months in the black'), 'hint': _('Months you spent less than you earned.'),
                     'value': _('%(a)s of %(b)s') % {'a': in_black, 'b': len(active)},
                     'tone': 'good' if share >= 0.7 else 'warn' if share >= 0.5 else 'bad',
                     'href': page.links.transactions(start, end)})
    else:
        rows.append({'label': _('Months in the black'), 'hint': _('Needs at least two months of activity.'),
                     'value': '—', 'tone': 'none', 'href': page.links.transactions(start, end)})
    for row in rows:
        row['value'] = str(row['value'])
    return rows


def comparison_chart(page: Page) -> dict | None:
    """Spending per bar, this period against its comparison, bar for bar."""
    period = page.period
    current = page.bucket_list()
    other = buckets(period.compare[0], period.compare[1], period.granularity) if period.compare else []
    money, cta = page.money, _('View transactions')
    now_values = [page.facts.summarize(b.start, b.end).spending for b in current]
    then_values = [page.facts.summarize(b.start, b.end).spending for b in other]
    if not any(then_values):
        then_values, other = [], []                # nothing was spent then, so there is nothing to compare with
    tips = []
    for i, bucket in enumerate(current):
        lines = [{'k': _('This period'), 'v': money.full(now_values[i]), 'c': 'income'}]
        if i < len(then_values):
            lines.append({'k': _('Comparison'), 'v': money.full(then_values[i]), 'c': 'muted'})
        tips.append({'title': page.bucket_label(bucket, long=True), 'lines': lines, 'cta': cta,
                     'href': page.link_for(bucket, (EXPENSE, CAPITAL_EVENT, LOAN))})
    title = {'last_year': _('This year against last year'), 'last_month': _('This month against last month'),
             'before': _('This period against the one before')}.get(period.compare_kind or '', _('Spending over the period'))
    return {'labels': [page.bucket_label(b) for b in current], 'current': [float(v) for v in now_values],
            'compare': [float(v) for v in then_values][:len(current)] if then_values else None, 'tips': tips,
            'title': title,
            'hint': (_('%(a)s against %(b)s.') % {'a': page.range_text(*period.range),
                                                     'b': page.range_text(*period.compare)}) if then_values
            else (_('Nothing was recorded in the period to compare with.') if period.compare
                  else _('There is no earlier period to compare with.'))}


def build(page: Page) -> dict:
    flow = sankey(page)
    return {'tiles': page.tiles(), 'insight': page.insight(), 'sankey': flow, 'health': health(page),
            'comparison': comparison_chart(page), 'has_flow': bool(flow['nodes'])}
