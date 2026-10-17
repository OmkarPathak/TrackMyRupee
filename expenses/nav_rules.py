"""Which day's NAV a mutual fund purchase is priced at (AMFI / SEBI rules), and what stamp duty takes off.

Rules used (an estimate: the fund house's statement is the final word):
  * Equity, hybrid and debt funds: money available before the 3 PM cut-off on a business day gets that day's
    NAV; after it, or on a weekend or holiday, the next business day's NAV.
  * Liquid and overnight funds: money available before the 1:30 PM cut-off gets the NAV of the business day
    before; after it, the NAV of that day.
  * Stamp duty of 0.005% is taken from every mutual fund purchase before units are allotted.
  * Units are allotted to three decimals.

Weekends and the fixed-date holidays below never have a NAV. Movable holidays (Holi, Diwali, Eid...) are in the
MarketHoliday table; and because a purchase is priced at the first NAV that was actually published on or after
the date these rules give, a holiday missing from the table still lands on the right day.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

STANDARD, LIQUID = 'STANDARD', 'LIQUID'
CUTOFF_STANDARD = '3:00 PM'
CUTOFF_LIQUID = '1:30 PM'
STAMP_DUTY_RATE = Decimal('0.00005')       # 0.005%
UNIT_PLACES = Decimal('0.001')

# (month, day): Republic Day, Ambedkar Jayanti, Maharashtra Day, Independence Day, Gandhi Jayanti, Christmas
FIXED_HOLIDAYS = frozenset({(1, 26), (4, 14), (5, 1), (8, 15), (10, 2), (12, 25)})


def fund_category(scheme_name: str | None) -> str:
    """Liquid and overnight funds have an earlier cut-off and a previous-day NAV."""
    return LIQUID if re.search(r'\b(liquid|overnight)\b', scheme_name or '', re.I) else STANDARD


def cutoff_label(category: str) -> str:
    return CUTOFF_LIQUID if category == LIQUID else CUTOFF_STANDARD


def is_business_day(day: date, holidays=frozenset()) -> bool:
    return day.weekday() < 5 and (day.month, day.day) not in FIXED_HOLIDAYS and day not in holidays


def next_business_day(day: date, holidays=frozenset()) -> date:
    """The first business day strictly after ``day``."""
    day += timedelta(days=1)
    while not is_business_day(day, holidays):
        day += timedelta(days=1)
    return day


def previous_business_day(day: date, holidays=frozenset()) -> date:
    """The last business day strictly before ``day``."""
    day -= timedelta(days=1)
    while not is_business_day(day, holidays):
        day -= timedelta(days=1)
    return day


def applicable_nav_date(order_date: date, *, before_cutoff: bool = True, category: str = STANDARD,
                        holidays=frozenset()) -> date:
    """The date whose NAV a purchase placed on ``order_date`` is priced at (first date to look at: the NAV
    actually used is the first one published on or after it)."""
    effective, early = order_date, before_cutoff
    if not is_business_day(order_date, holidays):
        effective, early = next_business_day(order_date, holidays), True      # picked up when the market opens
    if category == LIQUID:
        return previous_business_day(effective, holidays) if early else effective
    return effective if early else next_business_day(effective, holidays)


def stamp_duty(amount: Decimal) -> Decimal:
    return (Decimal(amount) * STAMP_DUTY_RATE).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def units_for(amount: Decimal, nav: Decimal, duty: Decimal | None = None) -> Decimal:
    """Units allotted for ``amount`` at ``nav`` once stamp duty is taken off."""
    duty = stamp_duty(amount) if duty is None else duty
    return ((Decimal(amount) - duty) / Decimal(nav)).quantize(UNIT_PLACES, rounding=ROUND_HALF_UP)


def load_holidays(start: date, end: date) -> frozenset:
    """MarketHoliday dates in [start, end]."""
    from .models import MarketHoliday

    return frozenset(MarketHoliday.objects.filter(date__range=(start, end)).values_list('date', flat=True))
