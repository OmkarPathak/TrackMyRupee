"""Money, percent and date text for insight copy, formatted the way the rest of the app does it.

Wraps analytics.money.Money (lakh/crore grouping, the minus sign in front of the symbol, Devanagari digits in
Hindi and Marathi) and adds the "about ₹9.2k" style the copy wants. The exact figure always goes in the evidence.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.utils.formats import date_format
from django.utils.translation import pgettext

from ..analytics.money import Money
from ..utils import translate_digits

ONE_DECIMAL = Decimal('0.1')


def _suffix(key: str) -> str:
    """The k, L, Cr, M, B words. Translators give Hindi and Marathi readers their own words for them."""
    if key == 'k':
        return pgettext('amount suffix: thousand', 'k')
    if key == 'L':
        return pgettext('amount suffix: lakh', 'L')
    if key == 'Cr':
        return pgettext('amount suffix: crore', 'Cr')
    if key == 'M':
        return pgettext('amount suffix: million', 'M')
    return pgettext('amount suffix: billion', 'B')


class Fmt:
    def __init__(self, symbol: str = '₹'):
        self.symbol = symbol or '₹'
        self.money_ = Money(self.symbol)
        self.indian = self.symbol == '₹'

    def money(self, value) -> str:
        """Exact rupees, for evidence lines: ₹1,23,456."""
        return self.money_.full(value)

    def approx(self, value) -> str:
        """Rounded, for sentences: ₹9.2k, ₹1.4L, ₹2.1Cr. Under a thousand it is the exact figure."""
        number = Decimal(str(value or 0))
        size_steps = ((Decimal(10) ** 7, 'Cr'), (Decimal(10) ** 5, 'L'), (Decimal(10) ** 3, 'k')) if self.indian else \
            ((Decimal(10) ** 9, 'B'), (Decimal(10) ** 6, 'M'), (Decimal(10) ** 3, 'k'))
        size = abs(number)
        for step, key in size_steps:
            if size >= step:
                scaled = (size / step).quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)
                text = f'{scaled:f}'.removesuffix('.0')
                word = _suffix(key)
                gap = '' if word.isascii() else ' '
                sign = '−' if number < 0 else ''
                return translate_digits(f'{sign}{self.symbol}{text}{gap}{word}')
        return self.money_.full(number)

    @staticmethod
    def percent(value) -> str:
        """A whole-number percent: 40%."""
        whole = Decimal(str(value or 0)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return translate_digits(f'{whole:f}%')

    @staticmethod
    def short(text, words: int = 2, chars: int = 24) -> str:
        """A name trimmed to fit a title (titles are at most eight words): two words and 24 characters."""
        text = (text or '').strip()
        parts = text.split()
        shown = ' '.join(parts[:words])
        if len(shown) > chars:
            return shown[:chars - 1].rstrip() + '…'
        return shown + ('…' if len(parts) > words else '')

    @staticmethod
    def number(value) -> str:
        return translate_digits(str(value))

    @staticmethod
    def day(value: date) -> str:
        """24 Oct"""
        return translate_digits(date_format(value, 'j M'))

    @staticmethod
    def month(value: date) -> str:
        """Oct"""
        return translate_digits(date_format(value, 'M'))

    @staticmethod
    def day_year(value: date) -> str:
        """24 Oct 2026"""
        return translate_digits(date_format(value, 'j M Y'))
