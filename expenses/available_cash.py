"""Available cash: the money a person can spend today.

The balances of their active cash and bank accounts (cash, savings, salary, current accounts and wallets), in their own
currency, leaving out any account they set aside (an investment pot, an emergency fund). Money in deposits, mutual
funds and other investments is not available cash, however it was moved there, and neither are cards and loans (what is
owed does not reduce what is in the wallet). Net worth is a different figure and is not changed by any of this.
"""
from __future__ import annotations

from decimal import Decimal


def available_cash(accounts, base_balance) -> Decimal:
    """Sum of ``base_balance(account)`` over the accounts that count. ``base_balance`` gives an account's balance in the
    person's own currency (the pages already hold it, so nothing is read again here)."""
    total = Decimal('0.00')
    for account in accounts:
        if account.counts_as_available_cash:
            total += Decimal(str(base_balance(account) or 0))
    return total.quantize(Decimal('0.01'))
