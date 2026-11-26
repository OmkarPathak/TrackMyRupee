"""The investments overview: what the person holds in market investments, deposits and schemes, and what they put into them.

One call, build_investments_overview(user), gives every screen that shows investments the same figures: the Investments
page, the printable summary report (Accounts > Summary report) and anything built on them later. Values are the converted
balances net worth uses (holdings at their latest valuation, deposits with their accrued interest), so none of them can
disagree with the net worth card:

* a holding's gain is its latest valuation against what it cost;
* a deposit's gain is its accrued value against what was put in;
* the SIPs are the active recurring transfers into investment accounts, shown per fund when the transfer is split,
  with every frequency worked out as a monthly amount;
* the allocation is the assets by account-type group.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from .account_types import STRATEGY, investment_codes, is_liability, strategy_for
from .ledger_read_service import LedgerReadService
from .models import Account, FundNAVCache, Holding, RecurringTransaction, Valuation
from .services import LoanService

ZERO = Decimal('0.00')

# A schedule's amount, as a month's worth
MONTHLY_FACTOR = {
    'DAILY': Decimal(365) / Decimal(12),
    'WEEKLY': Decimal(52) / Decimal(12),
    'BIWEEKLY': Decimal(26) / Decimal(12),
    'MONTHLY': Decimal(1),
    'BIMONTHLY': Decimal(1) / Decimal(2),
    'QUARTERLY': Decimal(1) / Decimal(3),
    'SEMIANNUALLY': Decimal(1) / Decimal(6),
    'YEARLY': Decimal(1) / Decimal(12),
}

# Account types listed under Deposits; the other deposit-strategy types (PPF, EPF, NPS, SSY...) are the Schemes
DEPOSIT_TAB_TYPES = frozenset({'FD', 'RD', 'FIXED_DEPOSIT'})
UPCOMING_LIMIT = 5

# Colours of the allocation bar, in the order of the biggest group first (see portfolio_summary.html)
PALETTE_SIZE = 6


def _pct(part: Decimal, whole: Decimal, places: str = '0.1') -> Decimal | None:
    if not whole or whole <= 0:
        return None
    return (part / whole * 100).quantize(Decimal(places))


def _group_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for group, choices in Account.ACCOUNT_TYPES:
        for code, _label in choices:
            names.setdefault(code, group)
    return names


def _type_labels() -> dict[str, str]:
    labels: dict[str, str] = {}
    for _group, choices in Account.ACCOUNT_TYPES:
        for code, label in choices:
            labels.setdefault(code, str(label))
    return labels


def holdings_with_latest_valuation(accounts):
    """The active holdings of ``accounts``, each with only its latest valuation loaded (a fund priced every day has
    thousands, and the report reads one)."""
    from django.db.models import OuterRef, Prefetch, Subquery
    latest = Valuation.objects.filter(holding=OuterRef('holding')).order_by('-as_of_date', '-created_at').values('pk')[:1]
    return (Holding.objects.filter(account__in=accounts, is_active=True)
            .select_related('account')
            .prefetch_related(Prefetch('valuations', queryset=Valuation.objects.filter(pk=Subquery(latest)))))


def _holding_rows(user, accounts, balances, rate_for):
    """[(account, block)] for the accounts valued from their holdings, and the figures for the returns."""
    holdings = holdings_with_latest_valuation(accounts)
    by_account = defaultdict(list)
    for holding in holdings:
        by_account[holding.account_id].append(holding)

    # Net worth values a holding with no recorded valuation at its fund's latest cached NAV, so the report does too
    codes = {h.scheme_code for rows in by_account.values() for h in rows if h.scheme_code}
    navs = dict(FundNAVCache.objects.filter(scheme_code__in=codes).values_list('scheme_code', 'latest_nav')) if codes else {}

    blocks, invested, tracked_current = [], ZERO, ZERO
    for account in accounts:
        rows, listed, block_cost, block_current = [], ZERO, ZERO, ZERO
        # An account set to show what was put in is worth its cost in net worth, whatever the holdings are worth now
        accrued = getattr(account, 'show_accrued_balance', True)
        for holding in by_account.get(account.pk, []):
            valuation = holding.valuations.first() if accrued else None
            cost = holding.cost_basis
            cached_nav = navs.get(holding.scheme_code) if (accrued and not valuation and holding.scheme_code) else None
            if valuation:
                current = valuation.value_for(holding.units)
            elif cached_nav is not None and holding.units:
                current = (holding.units * cached_nav).quantize(Decimal('0.01'))
            else:
                current = cost
            rate = rate_for(holding.currency)
            current_base, cost_base = (current * rate).quantize(Decimal('0.01')), (cost * rate).quantize(Decimal('0.01'))
            has_gain = bool((valuation or (cached_nav is not None and holding.units)) and cost > 0)
            if current_base <= 0 and cost_base <= 0:
                continue
            invested += cost_base
            tracked_current += current_base if has_gain else cost_base
            if has_gain:
                block_cost += cost_base
                block_current += current_base
            listed += current_base
            rows.append({
                'name': holding.instrument_name,
                'subtitle': holding.get_instrument_type_display() if holding.instrument_type != 'OTHER' else '',
                'value': current_base,
                'gain_pct': _pct(current - cost, cost) if has_gain else None,
            })
        total = max(balances.get(account.pk, ZERO), ZERO)
        if total - listed > Decimal('0.5'):
            rows.append({'name': _('Cash in this account'), 'subtitle': '', 'value': total - listed, 'gain_pct': None})
        if total <= 0 and not rows:
            continue
        rows.sort(key=lambda r: r['value'], reverse=True)
        gain = _pct(block_current - block_cost, block_cost)
        blocks.append({'title': account.name, 'total': total, 'gain_pct': gain, 'rows': rows})
    return blocks, invested, tracked_current


def _deposit_rows(accounts, balances, rate_for, labels):
    from .account_valuation import get_baseline_deposit
    rows, invested, tracked_current = [], ZERO, ZERO
    for account in accounts:
        value = balances.get(account.pk, ZERO)
        if value <= 0:
            continue
        subtitle = labels.get(account.account_type, '')
        if account.deposit_rate:
            subtitle = f'{subtitle} · {account.deposit_rate.normalize():f}%'
        if account.deposit_maturity_date and not account.deposit_closed_date:
            subtitle = f'{subtitle} · ' + _('matures %(date)s') % {'date': date_format(account.deposit_maturity_date, 'd M Y')}
        gain_pct, baseline_base = None, None
        try:
            if account.account_type != 'RD' and account.deposit_principal and account.deposit_principal > 0:
                baseline = account.deposit_principal.quantize(Decimal('0.01'))       # (what get_baseline_deposit gives, without its ledger query)
            else:
                baseline = get_baseline_deposit(account)
        except Exception:
            baseline = None
        if baseline is not None and baseline > 0:
            baseline_base = (baseline * rate_for(account.currency)).quantize(Decimal('0.01'))
            invested += baseline_base
            tracked_current += value
            gain_pct = _pct(value - baseline_base, baseline_base)
        rows.append({
            'name': account.name, 'subtitle': subtitle, 'value': value, 'gain_pct': gain_pct,
            # what the Deposits and Schemes tabs show
            'type': account.account_type, 'type_label': labels.get(account.account_type, ''),
            'rate': account.deposit_rate or None,
            'start': account.deposit_start_date,
            'maturity': None if account.deposit_closed_date else account.deposit_maturity_date,
            'invested': baseline_base,
            'gain': (value - baseline_base) if baseline_base is not None else None,
            'account_uuid': account.uuid,
        })
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows, invested, tracked_current


def _sip_rows(user, today, base_rate):
    schedules = (RecurringTransaction.objects
                 .filter(user=user, is_active=True, transaction_type='TRANSFER',
                         to_account__is_active=True, to_account__account_type__in=investment_codes())
                 .exclude(end_date__lt=today)
                 .select_related('to_account').prefetch_related('fund_splits__holding'))
    rows, total = [], ZERO
    for schedule in schedules:
        factor = MONTHLY_FACTOR.get(schedule.frequency, Decimal(1))
        rate = base_rate(schedule.currency)
        monthly = (schedule.amount * rate * factor).quantize(Decimal('0.01'))
        total += monthly
        splits = list(schedule.fund_splits.all())
        if splits:
            fund_rows = [{'name': split.holding.instrument_name, 'subtitle': schedule.to_account.name,
                          'value': (split.amount * rate * factor).quantize(Decimal('0.01'))} for split in splits]
            rows.extend(fund_rows)
            in_funds = sum((r['value'] for r in fund_rows), ZERO)
            if in_funds > monthly:
                total += in_funds - monthly         # (splits that came to more than the transfer: the total follows the rows)
            elif monthly - in_funds > Decimal('0.5'):
                rows.append({'name': _('Cash (not in a fund)'), 'subtitle': schedule.to_account.name, 'value': monthly - in_funds})
        else:
            rows.append({'name': schedule.description or schedule.to_account.name, 'subtitle': schedule.to_account.name,
                         'value': monthly})
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows, total


def active_accounts(user):
    return list(user.accounts.filter(is_active=True).select_related('linked_loan').order_by('name'))


def base_rate_getter(user):
    """rate_for(currency): the rate into the person's currency, looked up once per currency."""
    rates: dict[str, Decimal] = {}

    def rate_for(currency):
        if currency not in rates:
            rates[currency] = LoanService.base_rate(user, currency)
        return rates[currency]
    return rate_for


def split_accounts(accounts):
    """(accounts valued from holdings, deposit-type accounts and schemes, every other asset account)."""
    holdings_accounts = [a for a in accounts if strategy_for(a.account_type) == STRATEGY.HOLDINGS]
    deposit_accounts = [a for a in accounts if strategy_for(a.account_type) == STRATEGY.DEPOSIT]
    other_accounts = [a for a in accounts if a not in holdings_accounts and a not in deposit_accounts
                      and not is_liability(a.account_type) and strategy_for(a.account_type) != STRATEGY.LOAN_OUTSTANDING]
    return holdings_accounts, deposit_accounts, other_accounts


def allocation_by_group(accounts, balances) -> list[dict]:
    """The assets by account-type group, biggest first, with the colour index of each (see portfolio_summary.html)."""
    groups = _group_names()
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for account in accounts:
        if is_liability(account.account_type) or strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING:
            continue
        value = balances.get(account.pk, ZERO)
        if value > 0:
            totals[str(groups.get(account.account_type, _('Other')))] += value
    allocated_total = sum(totals.values(), ZERO)
    allocation = [{'label': name, 'amount': amount, 'percent': _pct(amount, allocated_total, '1') or Decimal('0')}
                  for name, amount in sorted(totals.items(), key=lambda kv: kv[1], reverse=True)]
    # Beyond the palette the smallest groups share the last colour
    for index, item in enumerate(allocation):
        item['color'] = min(index, PALETTE_SIZE - 1)
    return allocation


def upcoming_maturities(deposit_rows, today, limit=UPCOMING_LIMIT) -> list[dict]:
    """The next deposits and schemes to mature, soonest first (matured and closed ones are not listed)."""
    due = [r for r in deposit_rows if r.get('maturity') and r['maturity'] >= today]
    due.sort(key=lambda r: (r['maturity'], r['name']))
    return [{'kind': 'maturity', 'date': r['maturity'], 'name': r['name'], 'value': r['value'],
             'account_uuid': r['account_uuid']} for r in due[:limit]]


def build_investments_overview(user, today=None, *, accounts=None, balances=None, rate_for=None) -> dict:
    """Everything the investment screens show. ``accounts`` and ``balances`` (the net worth's own per-account figures)
    may be passed by a caller that already has them, so net worth (and each currency's rate) is worked out once."""
    today = today or timezone.localdate()
    if accounts is None:
        accounts = active_accounts(user)
    if balances is None:
        _net_worth, balances = LedgerReadService.get_net_worth(user)
    rate_for = rate_for or base_rate_getter(user)
    labels = _type_labels()
    holdings_accounts, deposit_accounts, other_accounts = split_accounts(accounts)

    holding_blocks, holdings_invested, holdings_current = _holding_rows(user, holdings_accounts, balances, rate_for)
    deposit_rows, deposits_invested, deposits_current = _deposit_rows(deposit_accounts, balances, rate_for, labels)
    sip_rows, monthly_sip = _sip_rows(user, today, rate_for)

    invested = holdings_invested + deposits_invested
    tracked_current = holdings_current + deposits_current
    # A gain is reported only where something has a value to set against its cost (an account shown at cost has none)
    gain_known = any(b['gain_pct'] is not None for b in holding_blocks) or any(r['gain_pct'] is not None for r in deposit_rows)
    returns = tracked_current - invested if (invested > 0 and gain_known) else None
    return {
        'as_of': today,
        'holding_blocks': holding_blocks,
        'deposit_rows': deposit_rows,
        'deposits': [r for r in deposit_rows if r['type'] in DEPOSIT_TAB_TYPES],
        'schemes': [r for r in deposit_rows if r['type'] not in DEPOSIT_TAB_TYPES],
        'upcoming': upcoming_maturities(deposit_rows, today),
        'other_accounts': other_accounts,
        'sip_rows': sip_rows,
        'monthly_sip': monthly_sip,
        'invested': invested,
        'returns': returns,
        'returns_pct': _pct(returns, invested) if returns is not None else None,
        'investments_value': (sum((b['total'] for b in holding_blocks), ZERO) + sum((r['value'] for r in deposit_rows), ZERO)),
        'allocation': allocation_by_group(accounts, balances),
    }
