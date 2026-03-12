from django import template
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
def compact_amount(value, currency=''):
    try:
        num = float(value)
    except (ValueError, TypeError):
        return value

    from django.contrib.humanize.templatetags.humanize import intcomma

    # Only abbreviate if the number is >= 100,000
    if num < 100000:
        return intcomma(f"{num:,.0f}")

    # Currency-aware formatting
    if str(currency).upper() in ['INR', '₹']:
        # Indian Numbering System (Lakhs, Crores)
        if num >= 10000000:  # 1 Crore
            return f"{num / 10000000:.1f}Cr".replace('.0Cr', 'Cr')
        elif num >= 100000:  # 1 Lakh
            return f"{num / 100000:.1f}L".replace('.0L', 'L')
    else:
        # International Numbering System (Millions, Billions)
        if num >= 1000000000:  # 1 Billion
            return f"{num / 1000000000:.1f}B".replace('.0B', 'B')
        elif num >= 1000000:  # 1 Million
            return f"{num / 1000000:.1f}M".replace('.0M', 'M')
        elif num >= 1000:    # 1 Thousand
            return f"{num / 1000:.0f}k"
    
    return intcomma(f"{num:,.0f}")
