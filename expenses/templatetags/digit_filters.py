import math
import re
from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils.html import format_html
from django.utils.safestring import SafeData, mark_safe
from django.utils.translation import get_language

from ..utils import format_indian_number
from ..utils import translate_digits as utils_translate_digits

register = template.Library()

_MARKUP_TAG = re.compile(r'(<[^>]+>)')
_SHOWN_IN_TAG = re.compile(r'(data-exact|aria-label)="([^"]*)"')


@register.filter
def translate_digits(value):
    """Digits in Devanagari for Hindi and Marathi. Markup that is already safe (an amount with its exact-value tooltip)
    stays markup: only the text between tags and the exact value in its attributes are translated, never the tags."""
    if isinstance(value, SafeData):
        if get_language() not in ('hi', 'mr'):
            return value
        parts = []
        for part in _MARKUP_TAG.split(str(value)):
            if part.startswith('<'):
                part = _SHOWN_IN_TAG.sub(lambda m: f'{m.group(1)}="{utils_translate_digits(m.group(2))}"', part)
            else:
                part = utils_translate_digits(part)
            parts.append(part)
        return mark_safe(''.join(parts))
    return utils_translate_digits(value)

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

def compact_amount(value, currency=''):
    """Plain text, for Python code that builds sentences (the template filter of the same name adds the tooltip)."""
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


def compact_money(value, currency='₹'):
    """compact_amount with the currency symbol included and the sign in front of it: -₹59,772, ₹1.5L.

    Writing the symbol and then compact_amount's own sign gives "₹-59,772", which reads wrongly."""
    text = compact_amount(value, currency)
    if not isinstance(text, str) or text is value:
        return text                                  # not a number: handed back as it came
    if text.startswith('-'):
        return f"-{currency}{text[1:]}"
    return f"{currency}{text}"


def _paise_hidden(number: float) -> bool:
    """True when the amount has paise that a whole-rupee display leaves out."""
    return round(abs(number) * 100) % 100 != 0


def _is_rounded(number: float) -> bool:
    """True when the compact form hides part of the amount: it is abbreviated (1,00,000 and up) or has paise."""
    magnitude = abs(number)
    return magnitude >= 100000 or round(magnitude * 100) % 100 != 0


def with_exact_tip(text, value, currency='', whole_amount=False):
    """`text` (an amount as shown), wrapped so that a tap or click shows the exact amount, when `text` is rounded.

    `whole_amount` is for a display that always shows every digit but drops the paise (1,00,000 is already exact):
    it is wrapped only when there are paise. An amount that is shown in full stays plain text.
    static/js/exact_amounts.js shows the tooltip."""
    try:
        number = float(value)
    except (ValueError, TypeError):
        return text
    rounded = _paise_hidden(number) if whole_amount and math.isfinite(number) else (math.isfinite(number) and _is_rounded(number))
    if not rounded or not isinstance(text, str):
        return text
    exact = _exact_money(value, currency)
    if not isinstance(exact, str):
        return text
    return format_html(
        '<span class="tmr-amt" data-exact="{0}" aria-label="{0}" tabindex="0" role="button">{1}</span>', exact, text)


def compact_amount_tip(value, currency=''):
    return with_exact_tip(compact_amount(value, currency), value, currency)


def compact_money_tip(value, currency='₹'):
    return with_exact_tip(compact_money(value, currency), value, currency)


def ind_comma_tip(value, currency_symbol='₹'):
    return with_exact_tip(ind_comma(value, currency_symbol), value, currency_symbol, whole_amount=True)


# In templates the amount filters give the tooltip form; Python code keeps the plain functions above.
# (ind_comma_plain is for the few places that cannot hold markup, such as the text of an <option>.)
register.filter('compact_amount', compact_amount_tip)
register.filter('compact_money', compact_money_tip)
register.filter('ind_comma', ind_comma_tip)
register.filter('ind_comma_plain', ind_comma)


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
