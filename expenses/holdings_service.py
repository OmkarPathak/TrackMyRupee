"""Purchases of holdings.

A holding's units and average cost are worked out from its HoldingTransaction rows (weighted-average cost), so
every rupee that went into a fund is on record, and a transfer into an investment account can say how much of
it went to which fund.

Rules:
  * A purchase needs any two of units, price per unit and amount; the third is worked out.
  * A purchase may be tied to the Transfer that paid for it; the purchases of one transfer never add up to
    more than the transfer (same currency only).
  * A holding that has units but no purchase rows (made before this existed, or by a flow) gets an opening
    purchase from its current units and cost the first time it changes.
  * Only accounts valued from their holdings take holdings (account_types.holdings_codes()).
"""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from .account_types import holdings_codes

D2 = Decimal('0.01')
D6 = Decimal('0.000001')
MAX_UNITS = Decimal('10') ** 9
MAX_AMOUNT = Decimal('10') ** 13
MAX_ALLOCATION_ROWS = 20

INSTRUMENT_TYPES = {
    'MUTUAL_FUND': 'MF', 'INVESTMENT': 'MF', 'ULIP': 'MF',
    'DEMAT': 'STOCK', 'ETF': 'STOCK',
}


class HoldingError(ValueError):
    """A purchase that cannot be recorded; str() is a translated message for the user."""


def instrument_type_for(account) -> str:
    return INSTRUMENT_TYPES.get(account.account_type, 'OTHER')


def _positive(value, label):
    if value is None or str(value).strip() == '':
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise HoldingError(_('Enter a valid number for %(field)s.') % {'field': label}) from None
    if not number.is_finite() or number <= 0:
        raise HoldingError(_('%(field)s must be greater than zero.') % {'field': label})
    return number


def derive_purchase(units=None, price=None, amount=None):
    """(units, price, amount) from any two of them. Units are kept to 6 places, amount to 2, price to 6."""
    u, p, a = _positive(units, _('Units')), _positive(price, _('Price per unit')), _positive(amount, _('Amount'))
    if sum(x is not None for x in (u, p, a)) < 2:
        raise HoldingError(_('Enter any two of units, price per unit and amount.'))
    if u is not None and a is not None:
        p = a / u
    elif u is not None:
        a = u * p
    else:
        u = a / p
    if u >= MAX_UNITS or a >= MAX_AMOUNT or p >= MAX_UNITS:
        raise HoldingError(_('That amount is too large.'))
    u, a, p = u.quantize(D6), a.quantize(D2), p.quantize(D6)
    if u <= 0 or a <= 0 or p <= 0:
        raise HoldingError(_('That purchase is too small to record.'))
    return u, p, a


def resolve_scheme_code(name, instrument_type):
    """AMFI scheme code for a fund name ('' if none). Only mutual funds are looked up: a stock's name must
    never be matched to a fund."""
    if instrument_type != 'MF' or not name:
        return ''
    from .models import AMFIScheme

    match = AMFIScheme.objects.filter(scheme_name__icontains=name).first()
    if match:
        return match.scheme_code
    try:
        import urllib.parse

        import requests
        resp = requests.get(f"https://api.mfapi.in/mf/search?q={urllib.parse.quote(name)}",
                            headers={'User-Agent': 'TrackMyRupee/1.0'}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data:
                return str(data[0].get('schemeCode'))
    except Exception:
        pass
    return ''


def _check_date(date):
    if date is None:
        return timezone.localdate()
    if date > timezone.localdate():
        raise HoldingError(_('The date cannot be in the future.'))
    return date


def unallocated_amount(transfer, exclude_id=None) -> Decimal:
    """What is left of a transfer after the purchases tied to it."""
    rows = transfer.holding_allocations.all()
    if exclude_id:
        rows = rows.exclude(pk=exclude_id)
    return transfer.amount - (rows.aggregate(t=Sum('amount'))['t'] or Decimal('0'))


def _check_transfer(holding, transfer, amount, exclude_id=None):
    if transfer is None:
        return
    account = holding.account
    if transfer.user_id != account.user_id or transfer.to_account_id != account.id:
        raise HoldingError(_('That transfer did not go into this account.'))
    if transfer.from_account.currency != account.currency:
        raise HoldingError(_('A transfer between accounts in different currencies cannot be allocated to funds.'))
    if amount > unallocated_amount(transfer, exclude_id):
        raise HoldingError(_('Only %(left)s of that transfer is left to allocate.') % {
            'left': f'{account.currency}{unallocated_amount(transfer, exclude_id):,.2f}'})


def recompute(holding_id, removed=False):
    """Set a holding's units and average cost from its purchases (weighted-average cost). Called whenever a
    purchase is saved or deleted. With no purchases left (``removed``: its last one just went) the holding
    is closed. A sale that brings the units to zero closes it too."""
    from .models import Holding, HoldingTransaction
    from .signals import invalidate_dashboard_cache

    holding = Holding.objects.select_related('account').filter(pk=holding_id).first()
    if holding is None:
        return
    rows = list(HoldingTransaction.objects.filter(holding_id=holding_id, units__isnull=False)
                .order_by('date', 'id').values('kind', 'units', 'amount'))
    if not rows:
        if removed and not HoldingTransaction.objects.filter(holding_id=holding_id).exists():
            Holding.objects.filter(pk=holding_id).update(units=Decimal('0'), avg_cost=None, is_active=False,
                                                         updated_at=timezone.now())
            invalidate_dashboard_cache(user_id=holding.account.user_id)
        return
    units, cost = Decimal('0'), Decimal('0')
    for row in rows:
        if row['kind'] == 'SELL':
            if units > 0:
                sold = min(row['units'], units)
                cost -= cost / units * sold
                units -= sold
        else:
            units += row['units']
            cost += row['amount']
    units = units.quantize(D6)
    changes = {'units': units, 'avg_cost': (cost / units).quantize(D6) if units > 0 else None,
               'updated_at': timezone.now()}
    if units <= 0:
        changes['is_active'] = False
    Holding.objects.filter(pk=holding_id).update(**changes)
    invalidate_dashboard_cache(user_id=holding.account.user_id)


def ensure_opening(holding):
    """Give a holding that has units but no purchase rows one opening purchase from what it holds now."""
    from .models import HoldingTransaction

    if holding.transactions.exists() or not holding.units or holding.units <= 0 or holding.avg_cost is None:
        return
    HoldingTransaction.objects.create(
        holding=holding, kind='BUY', date=holding.created_at.date(), units=holding.units,
        price=holding.avg_cost, amount=holding.cost_basis, note=_('Opening position'),
    )


def wants_estimate(units, price, amount) -> bool:
    """Only an amount was given: find the NAV and work the units out."""
    blank = lambda v: v is None or str(v).strip() == ''
    return not blank(amount) and blank(units) and blank(price)


def estimate_purchase_terms(scheme_code, scheme_name, order_date, amount, before_cutoff=True, *, fetch=True):
    """Price an amount put into a fund on ``order_date`` at the NAV the AMFI rules give: the units, or
    PENDING while that NAV is not published yet. See nav_rules."""
    from . import nav_provider, nav_rules

    if not scheme_code:
        raise HoldingError(_('Enter the units, or choose a mutual fund so its NAV can be found.'))
    a = _positive(amount, _('Amount'))
    if a is None:
        raise HoldingError(_('Enter the amount.'))
    a = a.quantize(D2)
    category = nav_rules.fund_category(scheme_name)
    holidays = nav_rules.load_holidays(order_date - timedelta(days=15), order_date + timedelta(days=20))
    target = nav_rules.applicable_nav_date(order_date, before_cutoff=before_cutoff, category=category,
                                           holidays=holidays)
    duty = nav_rules.stamp_duty(a)
    hit = nav_provider.nav_on_or_after(scheme_code, target, fetch=fetch)
    if hit is None:
        return {'status': 'PENDING', 'units': None, 'price': None, 'nav_date': target, 'stamp_duty': duty,
                'amount': a, 'before_cutoff': before_cutoff}
    nav_date, nav = hit
    units = nav_rules.units_for(a, nav, duty)
    if units <= 0:
        raise HoldingError(_('That purchase is too small to record.'))
    return {'status': 'ESTIMATED', 'units': units, 'price': nav.quantize(D6), 'nav_date': nav_date,
            'stamp_duty': duty, 'amount': a, 'before_cutoff': before_cutoff}


def resolve_pending(*, scheme_code=None, holding=None, fetch=True):
    """Fill in the units of purchases that were waiting for a NAV, as far as it is now published."""
    from .models import HoldingTransaction

    pending = HoldingTransaction.objects.filter(status='PENDING', kind='BUY').select_related('holding')
    if scheme_code:
        pending = pending.filter(holding__scheme_code=scheme_code)
    if holding is not None:
        pending = pending.filter(holding=holding)
    done = 0
    for tx in pending:
        try:
            terms = estimate_purchase_terms(tx.holding.scheme_code, tx.holding.instrument_name, tx.date, tx.amount,
                                            tx.before_cutoff, fetch=fetch)
        except HoldingError:
            continue
        if terms['status'] == 'PENDING':
            continue
        tx.units, tx.price, tx.nav_date, tx.stamp_duty = terms['units'], terms['price'], terms['nav_date'], terms['stamp_duty']
        tx.status = 'ESTIMATED'
        tx.save()
        done += 1
    return done


def add_purchase(holding, *, date=None, units=None, price=None, amount=None, transfer=None, note='',
                 estimate=None, before_cutoff=True):
    from .models import HoldingTransaction

    date = _check_date(date)
    extra = {}
    if estimate is None and wants_estimate(units, price, amount):
        estimate = estimate_purchase_terms(holding.scheme_code, holding.instrument_name, date, amount, before_cutoff)
    if estimate is not None:                     # priced from the NAV, outside any database transaction
        u, p, a = estimate['units'], estimate['price'], estimate['amount']
        extra = {'status': estimate['status'], 'nav_date': estimate['nav_date'],
                 'stamp_duty': estimate['stamp_duty'], 'before_cutoff': estimate['before_cutoff']}
    else:
        u, p, a = derive_purchase(units, price, amount)
    with transaction.atomic():
        _check_transfer(holding, transfer, a)
        ensure_opening(holding)
        return HoldingTransaction.objects.create(
            holding=holding, kind='BUY', date=date, units=u, price=p, amount=a, transfer=transfer,
            note=(note or '')[:120], **extra,
        )


def update_purchase(tx, *, date=None, units=None, price=None, amount=None, note=None, before_cutoff=None):
    """Change a purchase. Giving units (or a price) confirms it as you know it from your statement; giving only
    the amount prices it from the NAV again."""
    date = _check_date(date or tx.date)
    cutoff = tx.before_cutoff if before_cutoff is None else before_cutoff
    changes = {}
    if wants_estimate(units, price, amount):
        est = estimate_purchase_terms(tx.holding.scheme_code, tx.holding.instrument_name, date, amount, cutoff)
        u, p, a = est['units'], est['price'], est['amount']
        changes = {'status': est['status'], 'nav_date': est['nav_date'], 'stamp_duty': est['stamp_duty']}
    else:
        u, p, a = derive_purchase(units, price, amount)
        changes = {'status': 'CONFIRMED'}
    with transaction.atomic():
        _check_transfer(tx.holding, tx.transfer, a, exclude_id=tx.pk)
        tx.date, tx.units, tx.price, tx.amount, tx.before_cutoff = date, u, p, a, cutoff
        for key, value in changes.items():
            setattr(tx, key, value)
        if note is not None:
            tx.note = note[:120]
        tx.save()
    return tx


def delete_purchase(tx):
    """Remove one purchase. A holding keeps at least one; to drop the holding itself, remove the holding."""
    if tx.holding.transactions.count() <= 1:
        raise HoldingError(_('A holding needs at least one purchase. Remove the holding instead.'))
    tx.delete()


def create_holding(account, *, name, units=None, price=None, amount=None, date=None, scheme_code='', isin='',
                   transfer=None, estimate=None, before_cutoff=True):
    """A new holding with its first purchase. Give units and price/amount, or just an amount for a mutual fund
    to have it priced from the NAV. Returns the holding (its NAV is fetched by the caller)."""
    from .models import Holding

    name = (name or '').strip()
    if not name:
        raise HoldingError(_('Holding name is required.'))
    if account.account_type not in holdings_codes():
        raise HoldingError(_('Holdings can only be added to Mutual Fund, Demat, ETF, SGB, ULIP or Gold accounts.'))
    instrument_type = instrument_type_for(account)
    scheme_code = (scheme_code or '').strip() or resolve_scheme_code(name, instrument_type)
    purchase_date = _check_date(date)
    if estimate is None and wants_estimate(units, price, amount):
        estimate = estimate_purchase_terms(scheme_code, name, purchase_date, amount, before_cutoff)
    elif estimate is None:
        derive_purchase(units, price, amount)      # fail before anything is created
    with transaction.atomic():
        holding = Holding.objects.create(
            account=account, instrument_name=name[:255], instrument_type=instrument_type, units=Decimal('0'),
            avg_cost=None, currency=account.currency, scheme_code=scheme_code or None,
            isin=(isin or '').strip() or None, is_active=True,
        )
        add_purchase(holding, date=purchase_date, units=units, price=price, amount=amount, transfer=transfer,
                     estimate=estimate, before_cutoff=before_cutoff)
    holding.refresh_from_db()
    return holding


def parse_allocations(raw, *, user, to_account, from_currency, transfer_amount, transfer=None, order_date=None):
    """Validate the 'allocate to funds' rows posted with a transfer. ``raw`` is the JSON the page sends:
    [{"holding_id": 3 | null, "name": "", "scheme_code": "", "amount": "6000", "units": "12.5",
      "cutoff": "before" | "after"}]. A row with no units is priced from the NAV (estimate_purchase_terms),
    which is looked up here, before anything is saved. Returns a list of cleaned rows; raises HoldingError."""
    from .models import Holding

    if raw in (None, '', '[]'):
        return []
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise HoldingError(_('The fund allocation could not be read. Please try again.')) from None
    if not isinstance(data, list) or not data:
        return []
    if len(data) > MAX_ALLOCATION_ROWS:
        raise HoldingError(_('You can allocate to at most %(n)d funds at once.') % {'n': MAX_ALLOCATION_ROWS})
    if to_account is None or to_account.user_id != user.id or to_account.account_type not in holdings_codes():
        raise HoldingError(_('Only a transfer into a Mutual Fund, Demat, ETF, SGB, ULIP or Gold account can be allocated to funds.'))
    if from_currency != to_account.currency:
        raise HoldingError(_('A transfer between accounts in different currencies cannot be allocated to funds.'))
    order_date = _check_date(order_date)
    instrument_type = instrument_type_for(to_account)
    rows, total = [], Decimal('0')
    for item in data:
        if not isinstance(item, dict):
            raise HoldingError(_('The fund allocation could not be read. Please try again.'))
        holding = None
        if item.get('holding_id') not in (None, '', 'new'):
            try:
                holding = Holding.objects.select_related('account').get(
                    pk=int(item['holding_id']), account=to_account, is_active=True)
            except (Holding.DoesNotExist, ValueError, TypeError):
                raise HoldingError(_('Choose a fund from the account you are transferring to.')) from None
        elif not (item.get('name') or '').strip():
            raise HoldingError(_('Choose a fund or enter the name of a new one.'))
        name = holding.instrument_name if holding else (item.get('name') or '').strip()
        scheme_code = holding.scheme_code if holding else (item.get('scheme_code') or '').strip()
        before = item.get('cutoff') != 'after'
        row = {'holding': holding, 'name': name, 'scheme_code': scheme_code, 'isin': (item.get('isin') or '').strip(),
               'units': None, 'estimate': None}
        if wants_estimate(item.get('units'), None, item.get('amount')):
            if not scheme_code and holding is None:
                scheme_code = resolve_scheme_code(name, instrument_type)
                row['scheme_code'] = scheme_code
            if not scheme_code:
                raise HoldingError(_('Enter the units for %(name)s: no NAV can be found for it.') % {'name': name})
            row['estimate'] = estimate_purchase_terms(scheme_code, name, order_date, item.get('amount'), before)
            amount = row['estimate']['amount']
        else:
            units, _price, amount = derive_purchase(units=item.get('units'), amount=item.get('amount'))
            row['units'] = units
        row['amount'] = amount
        row['before_cutoff'] = before
        total += amount
        rows.append(row)
    already = transfer_amount
    if transfer is not None and transfer.pk:
        already -= transfer.holding_allocations.aggregate(t=Sum('amount'))['t'] or Decimal('0')
    if total > already:
        raise HoldingError(_('The funds add up to more than the transfer (%(total)s of %(left)s).') % {
            'total': f'{to_account.currency}{total:,.2f}', 'left': f'{to_account.currency}{already:,.2f}'})
    return rows


def allocate_transfer(transfer, rows):
    """Record the cleaned rows from parse_allocations against a saved transfer. Returns (purchases,
    new_holdings); the caller fetches NAVs for the new holdings."""
    purchases, created = [], []
    with transaction.atomic():
        for row in rows:
            terms = dict(date=transfer.date, amount=row['amount'], transfer=transfer, estimate=row['estimate'],
                         before_cutoff=row['before_cutoff'])
            if row['units'] is not None:
                terms['units'] = row['units']
            if row['holding'] is not None:
                purchases.append(add_purchase(row['holding'], **terms))
            else:
                holding = create_holding(transfer.to_account, name=row['name'], scheme_code=row['scheme_code'],
                                         isin=row['isin'], **terms)
                created.append(holding)
                purchases.append(holding.transactions.get(transfer=transfer))
    return purchases, created


def create_empty_holding(account, *, name, scheme_code='', isin=''):
    """A holding with no purchases yet, for a SIP whose first instalment has not been posted. Its first
    purchase makes it real; until then it has no units and no cost."""
    from .models import Holding

    name = (name or '').strip()
    if not name:
        raise HoldingError(_('Holding name is required.'))
    if account.account_type not in holdings_codes():
        raise HoldingError(_('Holdings can only be added to Mutual Fund, Demat, ETF, SGB, ULIP or Gold accounts.'))
    return Holding.objects.create(
        account=account, instrument_name=name[:255], instrument_type=instrument_type_for(account),
        units=Decimal('0'), avg_cost=None, currency=account.currency, scheme_code=(scheme_code or '').strip() or None,
        isin=(isin or '').strip() or None, is_active=True,
    )


def record_instalment(recurring, transfer):
    """Buy the fund a recurring transfer is assigned to with this instalment. The transfer is already posted, so
    a purchase that cannot be recorded is logged and skipped rather than undoing it. Returns the purchase or None."""
    import logging

    fund = recurring.fund
    if fund is None or not fund.is_active or fund.account_id != transfer.to_account_id:
        return None
    try:
        return add_purchase(fund, date=transfer.date, amount=transfer.amount, transfer=transfer,
                            note=(recurring.description or '')[:120])
    except Exception as exc:        # a NAV lookup that fails must never undo the transfer
        logging.getLogger(__name__).warning("SIP instalment of recurring %s on %s not recorded: %s",
                                            recurring.pk, transfer.date, exc, exc_info=not isinstance(exc, HoldingError))
        return None
