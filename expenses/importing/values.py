"""Reading single cells of a statement: amounts and dates.

Pure functions with no database access, so every odd bank format can be pinned down in a small test.
"""
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import NamedTuple, Optional

from django.utils import timezone

MAX_AMOUNT = Decimal('1e13')            # what Expense.amount (15 digits, 2 decimals) can hold


class Amount(NamedTuple):
    value: Decimal                      # signed: "(100.00)" and "-100" are negative
    marker: Optional[str]               # 'DR' or 'CR' when the cell says so ("350.00 Dr")


_MARKER = re.compile(r'(?i)(?<![a-z])(dr|cr|debit|credit)\.?(?![a-z])')
_CURRENCY_WORDS = re.compile(r'(?i)(?<![a-z])(rs|inr|usd|eur|gbp|aed)\.?(?![a-z])|[₹$€£]')
_PLAIN_NUMBER = re.compile(r'\d+(\.\d+)?|\.\d+')


def parse_amount(raw):
    """The number in a cell, or None when the cell is not an amount.

    Understands "1,20,000.50", "Rs. 1,200", "₹350", "(100.00)", "-100", "100-" and a Dr/Cr marker such as
    "350.00 Dr". Anything with other text mixed in ("UPI 12345 350") is not an amount.
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float, Decimal)):
        value = Decimal(str(raw))
        return Amount(value, None) if value.is_finite() else None
    text = str(raw).strip().replace('−', '-').replace('–', '-').replace(' ', ' ')
    if not text:
        return None
    marker = None
    found = _MARKER.search(text)
    if found:
        marker = 'DR' if found.group(1).lower() in ('dr', 'debit') else 'CR'
        text = _MARKER.sub(' ', text)
    text = _CURRENCY_WORDS.sub(' ', text).replace(',', '').replace(' ', '')
    negative = False
    if text.startswith('(') and text.endswith(')'):
        negative, text = True, text[1:-1]
    if text.startswith('-'):
        negative, text = True, text[1:]
    elif text.startswith('+'):
        text = text[1:]
    if text.endswith('-'):
        negative, text = True, text[:-1]
    if not _PLAIN_NUMBER.fullmatch(text):
        return None
    value = Decimal(text)
    return Amount(-value if negative else value, marker)


# ---------------------------------------------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------------------------------------------

_DAY_FIRST = ['%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y', '%d/%m/%y', '%d-%m-%y', '%d.%m.%y', '%d/%m']
_MONTH_FIRST = ['%m/%d/%Y', '%m-%d-%Y', '%m/%d/%y', '%m-%d-%y']
_UNAMBIGUOUS = [
    '%Y-%m-%d', '%Y/%m/%d', '%y/%m/%d',
    '%d %b %Y', '%d %B %Y', '%d-%b-%Y', '%d-%B-%Y', '%d-%b-%y', '%d %b %y',
    '%b %d, %Y', '%B %d, %Y', '%d %b', '%d-%b', '%d %B', '%b %d', '%B %d',
    '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M',
    '%d %b %Y, %I:%M %p', '%b %d, %Y, %I:%M %p', '%b %d, %Y %I:%M %p', '%d %b %Y %H:%M:%S',
]
_TIMED = ['%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M', '%d-%m-%Y %H:%M:%S', '%d-%m-%Y %H:%M']
_NUMERIC_DATE = re.compile(r'^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})(?:\D|$)')


def detect_day_first(cells):
    """Whether a file writes 03/04/2026 as 3 April (True) or March 4 (False).

    One cell that cannot be month-first (25/04/2026) settles it for the whole file, and the other way round. A
    file with nothing to go on is read day-first, as Indian bank statements are. None means the file mixes both,
    so each cell is read on its own.
    """
    first_over_12 = second_over_12 = False
    for cell in cells:
        if isinstance(cell, (date, datetime)) or cell is None:
            continue
        found = _NUMERIC_DATE.match(str(cell))
        if not found:
            continue
        first, second = int(found.group(1)), int(found.group(2))
        first_over_12 |= first > 12 and second <= 12
        second_over_12 |= second > 12 and first <= 12
    if first_over_12 and second_over_12:
        return None
    return not second_over_12


def parse_date(value, day_first=True):
    """A cell as a date, or None. A date with no year is this year, or last year if that would be in the future."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value or not str(value).strip():
        return None
    text = str(value).strip()
    numeric = _MONTH_FIRST + _DAY_FIRST if day_first is False else _DAY_FIRST + _MONTH_FIRST
    for fmt in _TIMED + numeric + _UNAMBIGUOUS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if parsed.year == 1900:
            return _assume_year(parsed)
        return parsed
    return None


def _assume_year(parsed):
    today = timezone.localdate()
    try:
        candidate = parsed.replace(year=today.year)
    except ValueError:                  # 29 February
        candidate = parsed.replace(year=today.year, day=28)
    if candidate > today + timedelta(days=31):
        # "15 Dec" in a January statement means last December
        previous = today.year - 1
        day = min(candidate.day, 28) if candidate.month == 2 else candidate.day
        candidate = candidate.replace(year=previous, day=day)
    return candidate
