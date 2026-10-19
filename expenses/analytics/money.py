"""Money and percentage text for the Analytics page, formatted exactly as the rest of the app does."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from ..templatetags.digit_filters import compact_amount, ind_comma
from ..utils import translate_digits


class Money:
    def __init__(self, symbol: str = '₹'):
        self.symbol = symbol or '₹'

    @staticmethod
    def _whole(value) -> Decimal:
        return Decimal(str(value or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    def full(self, value) -> str:
        """₹1,23,456 (rupees only; the minus sign goes in front of the symbol)."""
        number = self._whole(value)
        sign = '−' if number < 0 else ''
        return translate_digits(f'{sign}{self.symbol}{ind_comma(abs(number), self.symbol)}')

    def compact(self, value) -> str:
        """₹1.2L for big amounts, for tight places such as chart ticks and narrow rows."""
        number = Decimal(str(value or 0))
        sign = '−' if number < 0 and self._whole(number) != 0 else ''
        return translate_digits(f'{sign}{self.symbol}{compact_amount(abs(number), self.symbol)}')

    def signed(self, value) -> str:
        number = self._whole(value)
        return ('+' if number > 0 else '') + self.full(number)


def percent(value, places: int = 0) -> str:
    return translate_digits(f'{float(value):.{places}f}%')


def change(current, previous):
    """(percent change, direction) of current against previous, or (None, 'flat') when it cannot be told."""
    previous = Decimal(str(previous or 0))
    if previous == 0:
        return None, 'flat'
    pct = (Decimal(str(current or 0)) - previous) / abs(previous) * 100
    return pct, ('up' if pct > 0 else 'down' if pct < 0 else 'flat')
