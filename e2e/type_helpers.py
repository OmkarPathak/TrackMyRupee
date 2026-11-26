"""Helpers for the per-account-type browser tests: make accounts, read exact amounts off a page.

The app wraps every amount in `<span class="tmr-amt" data-exact="₹1,07,185.90">1,07,186</span>`, rounded for reading
and exact for checking, so the tests compare the exact value a person would see on tapping the number.
"""
import re
from datetime import date
from decimal import Decimal

from django.urls import reverse

from e2e.base import (  # noqa: F401  (re-exported for the type tests)
    make_account,
    make_user,
)

D = Decimal


def money(text) -> Decimal:
    """'₹1,07,185.90', '-₹7,000' or '−₹7,000' -> Decimal."""
    text = str(text).replace('−', '-')
    negative = '-' in text
    digits = re.sub(r'[^0-9.]', '', text)
    return -D(digits) if negative else D(digits)


def exact_in(locator) -> Decimal:
    """The exact amount of the first amount inside `locator`."""
    amount = locator.locator('[data-exact]').first
    return money(amount.get_attribute('data-exact')) if amount.count() else money(locator.inner_text())


def balance_on(page) -> Decimal:
    """The big balance on an account page."""
    return exact_in(page.locator('div[style*="clamp(2rem"]').first)


def labelled(page, label) -> Decimal:
    """The amount beside a label on an account page ('Expected:', 'Principal:' ...)."""
    row = page.locator(f'xpath=//span[normalize-space()="{label}"]/parent::div').first
    return exact_in(row)


def open_account(test, page, account):
    test.open(page, reverse('account-detail', kwargs={'pk': account.uuid}))
    page.locator('div[style*="clamp(2rem"]').first.wait_for()


def years_between(start: date, end: date) -> Decimal:
    """Time the way a bank counts a deposit (and the guide describes it): whole calendar years, then the odd days over 365."""
    if end <= start:
        return D(0)
    years = end.year - start.year

    def add(d, n):
        try:
            return d.replace(year=d.year + n)
        except ValueError:                    # 29 February in a year that has none
            return d.replace(year=d.year + n, day=28)
    if add(start, years) > end:
        years -= 1
    return D(years) + D((end - add(start, years)).days) / D(365)


def deposit_value(principal, rate_pct, start: date, end: date, compounding: str) -> Decimal:
    """What a lump-sum deposit is worth on `end`, worked out independently of the app's own code."""
    rate, years = D(str(rate_pct)) / D(100), years_between(start, end)
    principal = D(str(principal))
    if compounding == 'SIMPLE':
        value = principal * (1 + rate * years)
    elif compounding == 'QUARTERLY':
        value = principal * (1 + rate / 4) ** (4 * years)
    elif compounding == 'MONTHLY':
        value = principal * (1 + rate / 12) ** (12 * years)
    else:                                      # ANNUAL
        value = principal * (1 + rate) ** years
    return value.quantize(D('0.01'))


def rd_value(installments, rate_pct, end: date, compounding: str) -> Decimal:
    """What a recurring deposit is worth on `end`: each installment grows from the day it was paid."""
    # the app rounds each installment's value to the paisa and adds those up
    return sum((deposit_value(amount, rate_pct, paid_on, end, compounding) for paid_on, amount in installments if paid_on < end),
               D('0.00'))


def monthly_dates(start: date, day: int, count: int):
    """The installment dates of a schedule: one a month on `day`, clamped to the month's last day."""
    import calendar
    dates = []
    year, month = start.year, start.month
    for _ in range(count):
        dates.append(date(year, month, min(day, calendar.monthrange(year, month)[1])))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return dates
