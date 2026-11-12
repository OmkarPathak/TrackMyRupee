"""What counts as "invested".

Invested is money moved into investments, minus money taken out of them:

* a transfer from a bank, cash or card account into an investment or deposit account counts as invested;
* a transfer from an investment account out to a bank, cash or card account counts against it (a maturity, a
  withdrawal);
* a transfer between two investment accounts (a renewal into a new FD, a fund switch) is neither.

So an FD that matures and is renewed shows the new money only, not the same money twice. The result can be
negative: more was taken out than put in.

Amounts are the transfer's `converted_amount` (the amount in the base currency), for money going in and coming out.
"""
from decimal import Decimal

from django.db.models import Q, Sum

from .account_types import investment_codes

ZERO = Decimal('0.00')


def into_investments() -> Q:
    codes = list(investment_codes())
    return Q(to_account__account_type__in=codes) & ~Q(from_account__account_type__in=codes)


def out_of_investments() -> Q:
    codes = list(investment_codes())
    return Q(from_account__account_type__in=codes) & ~Q(to_account__account_type__in=codes)


def investing_transfers(transfers):
    """The transfers that change how much is invested (in or out), from a queryset of Transfers."""
    return transfers.filter(into_investments() | out_of_investments())


def invested_totals(transfers) -> dict:
    """{'inflow', 'outflow', 'net'} over a queryset of Transfers (any; moves between investments are ignored)."""
    found = transfers.order_by().aggregate(
        inflow=Sum('converted_amount', filter=into_investments()),
        outflow=Sum('converted_amount', filter=out_of_investments()),
    )
    inflow, outflow = found['inflow'] or ZERO, found['outflow'] or ZERO
    return {'inflow': inflow, 'outflow': outflow, 'net': inflow - outflow}


def is_investment_type(account_type) -> bool:
    return account_type in investment_codes()
