from django import template
from ..utils import format_indian_number
from django.contrib.humanize.templatetags.humanize import intcomma
from django.utils.translation import get_language

register = template.Library()

@register.filter
def translate_digits(value):
    if value is None:
        return ""
    
    lang = get_language()
    if lang not in ['mr', 'hi']:
        return value
    
    value_str = str(value)
    arabic_to_devanagari = {
        '0': '०', '1': '१', '2': '२', '3': '३', '4': '४',
        '5': '५', '6': '६', '7': '७', '8': '८', '9': '९'
    }
    
    return ''.join(arabic_to_devanagari.get(char, char) for char in value_str)

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

    from django.contrib.humanize.templatetags.humanize import intcomma

    abs_num = abs(num)
    sign = "-" if num < 0 else ""

    # Only abbreviate if the absolute number is >= 100,000
    if abs_num < 100000:
        return f"{sign}{intcomma(f'{abs_num:,.0f}')}"

    # Currency-aware formatting
    if str(currency).upper() in ['INR', '₹']:
        # Indian Numbering System (Lakhs, Crores)
        if abs_num >= 10000000:  # 1 Crore
            res = f"{abs_num / 10000000:.1f}Cr".replace('.0Cr', 'Cr')
        elif abs_num >= 100000:  # 1 Lakh
            res = f"{abs_num / 100000:.1f}L".replace('.0L', 'L')
        else:
            res = intcomma(f"{abs_num:,.0f}")
    else:
        # International Numbering System (Millions, Billions)
        if abs_num >= 1000000000:  # 1 Billion
            res = f"{abs_num / 1000000000:.1f}B".replace('.0B', 'B')
        elif abs_num >= 1000000:  # 1 Million
            res = f"{abs_num / 1000000:.1f}M".replace('.0M', 'M')
        elif abs_num >= 1000:    # 1 Thousand
            res = f"{abs_num / 1000:.0f}k"
        else:
            res = intcomma(f"{abs_num:,.0f}")
    
    return f"{sign}{res}"
