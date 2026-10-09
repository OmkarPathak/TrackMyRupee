from django import template

register = template.Library()

@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Updates the current request's query parameters with the provided kwargs.
    Usage: {% url_replace param1='val1' param2='val2' %}
    """
    query = context['request'].GET.copy()
    for key, value in kwargs.items():
        query[key] = value
    return query.urlencode()


@register.filter(name='sum_base_amounts')
def sum_base_amounts(expenses):
    """Sum the base_amount of a list of expenses."""
    return sum(e.base_amount for e in expenses)


@register.filter(name='sum_net_unified_amounts')
def sum_net_unified_amounts(transactions):
    """Sum signed unified amounts for mixed transaction lists.

    INCOME is positive, EXPENSE/LOAN are negative, TRANSFER is neutral.
    Supports both dict-like items (values() querysets) and model-like objects.
    """
    total = 0
    for tx in transactions:
        if isinstance(tx, dict):
            tx_type = tx.get('type')
            amount = tx.get('unified_amount', 0) or 0
        else:
            tx_type = getattr(tx, 'type', None)
            amount = getattr(tx, 'unified_amount', 0) or 0

        if tx_type == 'INCOME':
            total += amount
        elif tx_type in ('EXPENSE', 'LOAN', 'CAPITAL_EVENT'):
            total -= amount

    return total


@register.filter(name='payment_color')
def payment_color(method):
    """Returns an accessible color for a payment method."""
    colors = {
        'cash': '#0F7657',
        'credit card': '#1E65B5',
        'debit card': '#1E65B5',
        'upi': '#9C5400',
        'netbanking': '#0F7657',
    }
    return colors.get(method.strip().lower(), '#4A4A46')


@register.filter(name='payment_bg')
def payment_bg(method):
    """Returns a light background color for a payment method badge."""
    bgs = {
        'cash': '#E6F4EF',
        'credit card': '#EBF3FC',
        'debit card': '#EBF3FC',
        'upi': '#FDF4E7',
        'netbanking': '#E6F4EF',
    }
    return bgs.get(method.strip().lower(), '#F2F0EB')
 
 
@register.simple_tag(takes_context=True)
def category_icon(context, category_name, user=None):
    """Returns the bootstrap icon class for a category name and user."""
    # Use the context's request to store a per-request cache of category icons
    request = context.get('request')
    if not request or not request.user.is_authenticated:
        return 'bi-tag'
        
    if not hasattr(request, '_category_icon_map'):
        from django.core.cache import cache

        from expenses.models import Category
        # Per-user cache (invalidated with the dashboard cache on any Category write, see
        # signals.invalidate_dashboard_cache) so warm page loads don't pay a DB round trip.
        cache_key = f'category_icon_map_{request.user.id}'
        icon_map = cache.get(cache_key)
        if icon_map is None:
            icon_map = dict(Category.objects.filter(user=request.user).values_list('name', 'icon'))
            cache.set(cache_key, icon_map, 3600)
        request._category_icon_map = icon_map
    
    return request._category_icon_map.get(category_name, 'bi-tag')



@register.filter(name='abs_val')
def abs_val(value):
    """Returns the absolute value of the input."""
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return value


@register.filter(name='get_dict_item')
def get_dict_item(dictionary, key):
    """Returns the value for a given key in a dictionary."""
    if dictionary:
        return dictionary.get(key)
    return None


@register.filter(name='bound_field')
def bound_field(form, field_name):
    """Return a bound form field by name."""
    try:
        return form[field_name]
    except Exception:
        return None


@register.filter(name='split_string')
def split_string(value, key):
    """
    Returns the value turned into a list.
    Usage: {{ "a,b,c"|split_string:"," }}
    """
    if value:
        return str(value).split(key)
    return []
