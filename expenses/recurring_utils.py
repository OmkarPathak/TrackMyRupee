import calendar
from datetime import date, timedelta
from decimal import Decimal
from typing import Tuple, Union


def calculate_recurring_equivalents(
    frequency: str,
    amount: Union[Decimal, float, int]
) -> Tuple[Union[Decimal, float], Union[Decimal, float]]:
    """
    Calculate (monthly_equivalent, yearly_equivalent) for a given frequency and amount.
    Multipliers:
      - DAILY: 30 / 365
      - WEEKLY: 4 / 52
      - BIWEEKLY: 2 / 26
      - MONTHLY: 1 / 12
      - QUARTERLY: / 3 / * 4
      - SEMIANNUALLY: / 6 / * 2
      - YEARLY: / 12 / * 1
    """
    if amount is None:
        amount = Decimal('0.00')

    freq = (frequency or '').upper()
    if freq == 'DAILY':
        monthly = amount * 30
        yearly = amount * 365
    elif freq == 'WEEKLY':
        monthly = amount * 4
        yearly = amount * 52
    elif freq == 'BIWEEKLY':
        monthly = amount * 2
        yearly = amount * 26
    elif freq == 'MONTHLY':
        monthly = amount
        yearly = amount * 12
    elif freq == 'QUARTERLY':
        monthly = amount / 3
        yearly = amount * 4
    elif freq == 'SEMIANNUALLY':
        monthly = amount / 6
        yearly = amount * 2
    elif freq == 'YEARLY':
        monthly = amount / 12
        yearly = amount
    else:
        monthly = amount
        yearly = amount * 12

    return monthly, yearly


def get_recurring_monthly_equivalent(frequency: str, amount: Union[Decimal, float, int]) -> Union[Decimal, float]:
    return calculate_recurring_equivalents(frequency, amount)[0]


def get_recurring_yearly_equivalent(frequency: str, amount: Union[Decimal, float, int]) -> Union[Decimal, float]:
    return calculate_recurring_equivalents(frequency, amount)[1]


def get_recurring_month_occurrence_amount(
    rt,
    year: int,
    month: int
) -> Union[Decimal, float]:
    """
    Calculate the total monetary amount a recurring transaction contributes
    in a specific forecast target month (year, month).
    Uses base_amount if available, falling back to amount.
    """
    amt = getattr(rt, 'base_amount', None)
    if amt is None:
        amt = getattr(rt, 'amount', Decimal('0.00'))
    
    if amt == 0:
        return amt

    month_start = date(year, month, 1)
    _, last_day = calendar.monthrange(year, month)
    month_end = date(year, month, last_day)

    start_date = getattr(rt, 'start_date', None)
    end_date = getattr(rt, 'end_date', None)

    if start_date and start_date > month_end:
        return type(amt)(0)
    if end_date and end_date < month_start:
        return type(amt)(0)

    freq = (getattr(rt, 'frequency', '') or '').upper()

    if freq == 'DAILY':
        # DAILY occurs every day by definition; contributes its full monthly-equivalent amount
        return amt * 30
    elif freq == 'MONTHLY':
        return amt
    elif freq == 'QUARTERLY':
        if not start_date:
            return amt
        month_diff = (year - start_date.year) * 12 + (month - start_date.month)
        if month_diff >= 0 and month_diff % 3 == 0:
            return amt
        return type(amt)(0)
    elif freq == 'SEMIANNUALLY':
        if not start_date:
            return amt
        month_diff = (year - start_date.year) * 12 + (month - start_date.month)
        if month_diff >= 0 and month_diff % 6 == 0:
            return amt
        return type(amt)(0)
    elif freq == 'YEARLY':
        if not start_date:
            return amt
        month_diff = (year - start_date.year) * 12 + (month - start_date.month)
        if month_diff >= 0 and month_diff % 12 == 0:
            return amt
        return type(amt)(0)
    elif freq == 'WEEKLY':
        if not start_date:
            return amt * 4
        # Calculate occurrences within [month_start, min(month_end, end_date or month_end)]
        if start_date >= month_start:
            cur = start_date
        else:
            days_since = (month_start - start_date).days
            rem = days_since % 7
            cur = month_start if rem == 0 else month_start + timedelta(days=(7 - rem))

        effective_end = min(month_end, end_date) if end_date else month_end
        count = 0
        while cur <= effective_end:
            count += 1
            cur += timedelta(days=7)
        return amt * count
    elif freq == 'BIWEEKLY':
        if not start_date:
            return amt * 2
        # Calculate occurrences within [month_start, min(month_end, end_date or month_end)]
        if start_date >= month_start:
            cur = start_date
        else:
            days_since = (month_start - start_date).days
            rem = days_since % 14
            cur = month_start if rem == 0 else month_start + timedelta(days=(14 - rem))

        effective_end = min(month_end, end_date) if end_date else month_end
        count = 0
        while cur <= effective_end:
            count += 1
            cur += timedelta(days=14)
        return amt * count
    else:
        return amt
