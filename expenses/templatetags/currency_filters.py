from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

register = template.Library()

@register.filter
def humanize_currency(value, currency_symbol='₹'):
    """
    Formats a number to a human-readable format based on currency.
    ₹ (Rupee): 
        >= 1Cr -> 1.25Cr
        >= 1L  -> 3.5L
        >= 1k  -> 35k
    Others: 
        >= 1B  -> 1.5B
        >= 1M  -> 1.5M
        >= 1k  -> 1.5k
    """
    try:
        value = float(value)
    except (ValueError, TypeError):
        return value


    if value < 100000 and value > -100000:
        return intcomma(f"{value:.0f}")

    abs_value = abs(value)
    
    def format_num(num, divisor, suffix):
        result = num / divisor
        if result % 1 == 0:
            return f"{result:.0f}{suffix}"
        return f"{result:.1f}{suffix}"

    if currency_symbol == '₹':
        if abs_value >= 10000000: # 1 Crore
            return format_num(value, 10000000, 'Cr')
        elif abs_value >= 100000: # 1 Lakh
            return format_num(value, 100000, 'L')
    else:
        if abs_value >= 1000000000: # 1 Billion
            return format_num(value, 1000000000, 'B')
        elif abs_value >= 1000000: # 1 Million
            return format_num(value, 1000000, 'M')
        elif abs_value >= 1000:    # 1 Thousand
            return format_num(value, 1000, 'k')
    
    return intcomma(f"{value:.0f}")
