"""The investments and liabilities summary report (Accounts > Summary report).

One call, build_portfolio_summary(user), returns everything the printable report shows, from the same figures the
rest of the app uses:

* net worth, assets and liabilities come from LedgerReadService.get_net_worth, so the report always agrees with the
  net worth card; whatever the rows below do not add up to is shown as an "Other" row rather than left out;
* each account's value is the converted balance that net worth uses (holdings at their latest valuation, deposits
  with their accrued interest, loans at what is still owed);
* the investments, deposits, SIPs and allocation come from build_investments_overview (expenses/investments_overview.py),
  the same figures the Investments page shows.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone
from django.utils.translation import gettext as _

from .account_types import STRATEGY, is_liability, strategy_for
from .investments_overview import (  # noqa: F401  (re-exported: other code and tests import these from here)
    MONTHLY_FACTOR,
    PALETTE_SIZE,
    ZERO,
    _pct,
    _type_labels,
    active_accounts,
    base_rate_getter,
    build_investments_overview,
    holdings_with_latest_valuation,
)
from .ledger_read_service import LedgerReadService
from .models import Loan
from .services import annotate_loan_principal_totals


def _liability_rows(user, accounts, balances, base_rate):
    rows = []
    linked_loan_ids = set()
    for account in accounts:
        if account.linked_loan_id:
            linked_loan_ids.add(account.linked_loan_id)
    loans = {loan.pk: loan for loan in annotate_loan_principal_totals(Loan.objects.filter(user=user, is_active=True))
             .prefetch_related('interest_rates')}
    for account in accounts:
        if not (is_liability(account.account_type) or strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING):
            continue
        balance = balances.get(account.pk, ZERO)
        # Net worth keeps what is owed on a loan account as a positive figure (the loan's outstanding principal) and a
        # card's debt as a negative balance, so each is read the way net worth wrote it.
        owed = abs(balance) if strategy_for(account.account_type) == STRATEGY.LOAN_OUTSTANDING else -balance
        if owed <= 0:
            continue
        loan = loans.get(account.linked_loan_id) if account.linked_loan_id else None
        subtitle = ''
        if loan:
            subtitle = loan.get_loan_type_display()
            rate = next(iter(loan.interest_rates.all()), None)       # (newest first, already loaded)
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
        rate = next(iter(loan.interest_rates.all()), None)       # (newest first, already loaded)
        if rate:
            subtitle = f'{subtitle} · {rate.interest_rate.normalize():f}%'
        rows.append({'name': loan.name, 'subtitle': subtitle, 'value': owed})
    rows.sort(key=lambda r: r['value'], reverse=True)
    return rows


def build_portfolio_summary(user, today=None) -> dict:
    today = today or timezone.localdate()
    detail: dict = {}
    net_worth, balances = LedgerReadService.get_net_worth(user, detail=detail)
    accounts = active_accounts(user)
    rate_for = base_rate_getter(user)

    labels = _type_labels()
    assets_total = Decimal(str(detail.get('assets', ZERO)))
    liabilities_total = Decimal(str(detail.get('liabilities', ZERO)))

    overview = build_investments_overview(user, today, accounts=accounts, balances=balances, rate_for=rate_for)
    holding_blocks, deposit_rows = overview['holding_blocks'], overview['deposit_rows']
    sip_rows, monthly_sip = overview['sip_rows'], overview['monthly_sip']

    other_rows = []
    for account in overview['other_accounts']:
        value = balances.get(account.pk, ZERO)
        if value > 0:
            other_rows.append({'name': account.name, 'subtitle': labels.get(account.account_type, ''), 'value': value})
    other_rows.sort(key=lambda r: r['value'], reverse=True)

    liability_rows = _liability_rows(user, accounts, balances, rate_for)

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
    # Shares are of what is listed as held: a block that takes away from the total (an overdrawn account, a correction)
    # has no share, and the rest add up to 100%.
    held_total = sum((b['total'] for b in blocks if b['total'] > 0), ZERO)
    for block in blocks:
        block['share_pct'] = _pct(block['total'], held_total, '1') if block['total'] > 0 else None

    return {
        'as_of': today,
        'net_worth': net_worth,
        'assets': assets_total,
        'liabilities': liabilities_total,
        'investments_value': overview['investments_value'],
        'invested': overview['invested'],
        'returns': overview['returns'],
        'returns_pct': overview['returns_pct'],
        'monthly_sip': monthly_sip,
        'allocation': overview['allocation'],
        'blocks': blocks,
        'liability_rows': liability_rows,
        'sip_rows': sip_rows,
        'is_empty': not blocks and not liability_rows,
    }
