import math
from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

from ..utils import format_indian_number
from ..utils import translate_digits as utils_translate_digits

register = template.Library()

@register.filter
def translate_digits(value):
    return utils_translate_digits(value)

@register.filter
def ind_comma(value, currency_symbol='₹'):
    """
    Formats a number with localized commas based on currency.
    ₹/INR: Indian Numbering System (3,2,2)
    Others: International Numbering System (3,3,3)
    """
    
    try:
        num = float(value)
    except (ValueError, TypeError):
        return value
    if not math.isfinite(num):
        return value
        
    if str(currency_symbol).upper() in ['INR', '₹']:
        return format_indian_number(num)
    
    # Default to international 3-digit comma grouping (integers only)
    return intcomma(f"{int(round(num)):,d}")

@register.filter
def compact_amount(value, currency=''):
    try:
        num = float(value)
    except (ValueError, TypeError):
        return value
    if not math.isfinite(num):
        return value

    from django.contrib.humanize.templatetags.humanize import intcomma

    abs_num = abs(num)
    # No "-0" for amounts that round to zero
    sign = "-" if num < 0 and round(abs_num) != 0 else ""

    # Only abbreviate if the absolute number is >= 100,000
    if abs_num < 100000:
        return f"{sign}{intcomma(f'{abs_num:,.0f}')}"

    # Currency-aware formatting
    if str(currency).upper() in ['INR', '₹']:
        # Indian Numbering System (Lakhs, Crores)
        if abs_num >= 10000000 or round(abs_num / 100000, 1) >= 100:  # 1 Crore
            res = f"{abs_num / 10000000:.1f}Cr".replace('.0Cr', 'Cr')
        elif abs_num >= 100000:  # 1 Lakh
            res = f"{abs_num / 100000:.1f}L".replace('.0L', 'L')
        else:
            res = intcomma(f"{abs_num:,.0f}")
    else:
        # International Numbering System (Millions, Billions)
        if abs_num >= 1000000000 or round(abs_num / 1000000, 1) >= 1000:  # 1 Billion
            res = f"{abs_num / 1000000000:.1f}B".replace('.0B', 'B')
        elif abs_num >= 1000000 or round(abs_num / 1000) >= 1000:  # 1 Million
            res = f"{abs_num / 1000000:.1f}M".replace('.0M', 'M')
        elif abs_num >= 1000:    # 1 Thousand
            res = f"{abs_num / 1000:.0f}k"
        else:
            res = intcomma(f"{abs_num:,.0f}")
    
    return f"{sign}{res}"


def _exact_money(value, currency, plus=False):
    try:
        num = Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError, TypeError):
        return value
    sign = '-' if num < 0 else ('+' if plus and num > 0 else '')
    whole, cents = divmod(abs(num), 1)
    body = ind_comma(int(whole), currency)
    if cents:
        body += f"{cents:.2f}"[1:]
    return f"{sign}{currency}{body}"


@register.filter
def money_exact(value, currency='₹'):
    """Amount with the sign in front of the symbol and paise kept: -₹450, ₹1,234.50."""
    return utils_translate_digits(_exact_money(value, currency))


@register.filter
def money_exact_signed(value, currency='₹'):
    """Like money_exact, with an explicit + for positive amounts: +₹249."""
    return utils_translate_digits(_exact_money(value, currency, plus=True))
