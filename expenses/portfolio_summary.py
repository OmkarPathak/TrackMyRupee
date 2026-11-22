"""The investments and liabilities summary report (Accounts > Summary report).

One call, build_portfolio_summary(user), returns everything the printable report shows, from the same figures the
rest of the app uses:

* net worth, assets and liabilities come from LedgerReadService.get_net_worth, so the report always agrees with the
  net worth card; whatever the rows below do not add up to is shown as an "Other" row rather than left out;
* each account's value is the converted balance that net worth uses (holdings at their latest valuation, deposits
  with their accrued interest, loans at what is still owed);
* a holding's gain is its latest valuation against what it cost;
* a deposit's gain is its accrued value against what was put in;
* the SIPs are the active recurring transfers into investment accounts, shown per fund when the transfer is split,
  with every frequency worked out as a monthly amount.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from .account_types import STRATEGY, investment_codes, is_liability, strategy_for
from .ledger_read_service import LedgerReadService
from .models import Account, Holding, Loan, RecurringTransaction
from .services import LoanService, annotate_loan_principal_totals

ZERO = Decimal('0.00')

# A schedule's amount, as a month's worth
MONTHLY_FACTOR = {
    'DAILY': Decimal(30),
    'WEEKLY': Decimal(52) / Decimal(12),
    'BIWEEKLY': Decimal(26) / Decimal(12),
    'MONTHLY': Decimal(1),
    'QUARTERLY': Decimal(1) / Decimal(3),
    'SEMIANNUALLY': Decimal(1) / Decimal(6),
    'YEARLY': Decimal(1) / Decimal(12),
}

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


def _holding_rows(user, accounts, balances, rate_for):
    """[(account, block)] for the accounts valued from their holdings, and the figures for the returns."""
    holdings = (Holding.objects.filter(account__in=accounts, is_active=True)
                .select_related('account').prefetch_related('valuations'))
    by_account = defaultdict(list)
    for holding in holdings:
        by_account[holding.account_id].append(holding)

    blocks, invested, tracked_current = [], ZERO, ZERO
    for account in accounts:
        rows, listed, block_cost, block_current = [], ZERO, ZERO, ZERO
        for holding in by_account.get(account.pk, []):
            valuation = holding.valuations.first()
            cost = holding.cost_basis
            current = valuation.value_for(holding.units) if valuation else cost
            rate = rate_for(holding.currency)
            current_base, cost_base = (current * rate).quantize(Decimal('0.01')), (cost * rate).quantize(Decimal('0.01'))
            has_gain = bool(valuation and cost > 0)
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
            subtitle = f'{subtitle} · ' + _('matures %(date)s') % {'date': account.deposit_maturity_date.strftime('%d %b %Y')}
        gain_pct = None
        try:
            baseline = get_baseline_deposit(account)
        except Exception:
            baseline = None
        if baseline is not None and baseline > 0:
            baseline_base = (baseline * rate_for(account.currency)).quantize(Decimal('0.01'))
            invested += baseline_base
            tracked_current += value
            gain_pct = _pct(value - baseline_base, baseline_base)
        rows.append({'name': account.name, 'subtitle': subtitle, 'value': value, 'gain_pct': gain_pct})
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows, invested, tracked_current


def _liability_rows(user, accounts, balances, base_rate):
    rows = []
    linked_loan_ids = set()
    for account in accounts:
        if account.linked_loan_id:
            linked_loan_ids.add(account.linked_loan_id)
    loans = {loan.pk: loan for loan in annotate_loan_principal_totals(Loan.objects.filter(user=user, is_active=True))}
    for account in accounts:
        if not (is_liability(account.account_type) or strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING):
            continue
        owed = -balances.get(account.pk, ZERO)
        if owed <= 0:
            continue
        loan = loans.get(account.linked_loan_id) if account.linked_loan_id else None
        subtitle = ''
        if loan:
            subtitle = loan.get_loan_type_display()
            rate = loan.interest_rates.first()
            if rate:
                subtitle = f'{subtitle} · {rate.interest_rate.normalize():f}%'
        rows.append({'name': account.name, 'subtitle': subtitle, 'value': owed})
    for loan in loans.values():
        if loan.pk in linked_loan_ids:
            continue
        owed = (loan.remaining_principal * base_rate(loan.currency)).quantize(Decimal('0.01'))
        if owed <= 0:
            continue
        subtitle = loan.get_loan_type_display()
        rate = loan.interest_rates.first()
        if rate:
            subtitle = f'{subtitle} · {rate.interest_rate.normalize():f}%'
        rows.append({'name': loan.name, 'subtitle': subtitle, 'value': owed})
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows


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
            for split in splits:
                rows.append({'name': split.holding.instrument_name, 'subtitle': schedule.to_account.name,
                             'value': (split.amount * rate * factor).quantize(Decimal('0.01'))})
            rest = monthly - sum((r['value'] for r in rows[-len(splits):]), ZERO)
            if rest > Decimal('0.5'):
                rows.append({'name': _('Cash (not in a fund)'), 'subtitle': schedule.to_account.name, 'value': rest})
        else:
            rows.append({'name': schedule.description or schedule.to_account.name, 'subtitle': schedule.to_account.name,
                         'value': monthly})
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows, total


def build_portfolio_summary(user, today=None) -> dict:
    today = today or timezone.localdate()
    detail: dict = {}
    net_worth, balances = LedgerReadService.get_net_worth(user, detail=detail)
    accounts = list(user.accounts.filter(is_active=True).select_related('linked_loan').order_by('name'))
    rates: dict[str, Decimal] = {}

    def rate_for(currency):
        if currency not in rates:
            rates[currency] = LoanService.base_rate(user, currency)
        return rates[currency]

    labels, groups = _type_labels(), _group_names()
    assets_total = Decimal(str(detail.get('assets', ZERO)))
    liabilities_total = Decimal(str(detail.get('liabilities', ZERO)))

    holdings_accounts = [a for a in accounts if strategy_for(a.account_type) == STRATEGY.HOLDINGS]
    deposit_accounts = [a for a in accounts if strategy_for(a.account_type) == STRATEGY.DEPOSIT]
    other_accounts = [a for a in accounts if a not in holdings_accounts and a not in deposit_accounts
                      and not is_liability(a.account_type) and strategy_for(a.account_type) != STRATEGY.LOAN_OUTSTANDING]

    holding_blocks, holdings_invested, holdings_current = _holding_rows(user, holdings_accounts, balances, rate_for)
    deposit_rows, deposits_invested, deposits_current = _deposit_rows(deposit_accounts, balances, rate_for, labels)

    other_rows = []
    for account in other_accounts:
        value = balances.get(account.pk, ZERO)
        if value > 0:
            other_rows.append({'name': account.name, 'subtitle': labels.get(account.account_type, ''), 'value': value})
    other_rows.sort(key=lambda r: r['value'], reverse=True)

    liability_rows = _liability_rows(user, accounts, balances, rate_for)
    sip_rows, monthly_sip = _sip_rows(user, today, rate_for)

    blocks = list(holding_blocks)
    if deposit_rows:
        blocks.append({'title': _('Deposits and schemes'), 'total': sum((r['value'] for r in deposit_rows), ZERO),
                       'gain_pct': None, 'rows': deposit_rows})
    if other_rows:
        blocks.append({'title': _('Cash and other assets'), 'total': sum((r['value'] for r in other_rows), ZERO),
                       'gain_pct': None, 'rows': other_rows})

    # The rows are the net worth's own figures; what they do not add up to is shown, never dropped.
    listed_assets = sum((b['total'] for b in blocks), ZERO)
    if abs(assets_total - listed_assets) > Decimal('0.5'):
        blocks.append({'title': _('Other'), 'total': assets_total - listed_assets, 'gain_pct': None,
                       'rows': [{'name': _('Goal savings and adjustments'), 'subtitle': '', 'value': assets_total - listed_assets}]})
    listed_liabilities = sum((r['value'] for r in liability_rows), ZERO)
    if abs(liabilities_total - listed_liabilities) > Decimal('0.5'):
        liability_rows.append({'name': _('Other'), 'subtitle': '', 'value': liabilities_total - listed_liabilities})

    blocks.sort(key=lambda b: b['total'], reverse=True)
    for block in blocks:
        block['share_pct'] = _pct(block['total'], assets_total, '1')

    # The allocation bar: assets by account-type group (the other rows by their own label)
    allocation_totals: dict[str, Decimal] = defaultdict(Decimal)
    for account in accounts:
        if is_liability(account.account_type) or strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING:
            continue
        value = balances.get(account.pk, ZERO)
        if value > 0:
            allocation_totals[str(groups.get(account.account_type, _('Other')))] += value
    allocation = []
    for name, amount in sorted(allocation_totals.items(), key=lambda kv: kv[1], reverse=True):
        allocation.append({'label': name, 'amount': amount, 'percent': _pct(amount, assets_total, '1') or Decimal('0')})
    # Beyond the palette the smallest groups share the last colour
    for index, item in enumerate(allocation):
        item['color'] = min(index, PALETTE_SIZE - 1)

    invested = holdings_invested + deposits_invested
    tracked_current = holdings_current + deposits_current
    returns = tracked_current - invested if invested > 0 else None
    investments_value = (sum((b['total'] for b in holding_blocks), ZERO)
                         + sum((r['value'] for r in deposit_rows), ZERO))

    return {
        'as_of': today,
        'net_worth': net_worth,
        'assets': assets_total,
        'liabilities': liabilities_total,
        'investments_value': investments_value,
        'invested': invested,
        'returns': returns,
        'returns_pct': _pct(returns, invested) if returns is not None else None,
        'monthly_sip': monthly_sip,
        'allocation': allocation,
        'blocks': blocks,
        'liability_rows': liability_rows,
        'sip_rows': sip_rows,
        'is_empty': not blocks and not liability_rows,
    }
