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


@register.filter(name='payment_color')
def payment_color(method):
    """Returns a subtle color for a payment method."""
    colors = {
        'cash': '#2e7d32',
        'credit card': '#1565c0',
        'debit card': '#6a1b9a',
        'upi': '#e65100',
        'netbanking': '#00838f',
    }
    return colors.get(method.strip().lower(), '#6c757d')


@register.filter(name='payment_bg')
def payment_bg(method):
    """Returns a light background color for a payment method badge."""
    bgs = {
        'cash': '#e8f5e9',
        'credit card': '#e3f2fd',
        'debit card': '#f3e5f5',
        'upi': '#fff3e0',
        'netbanking': '#e0f7fa',
    }
    return bgs.get(method.strip().lower(), '#f5f5f5')
 
 
@register.simple_tag
def category_icon(category_name, user):
    """Returns the bootstrap icon class for a category name and user."""
    from expenses.models import Category
    try:
        # Avoid database lookup for every row if possible? 
        # For simplicity now, let's do it. For performance, we should ideally use a context processor or pass a map.
        category = Category.objects.filter(user=user, name=category_name).first()
        if category:
            return category.icon
    except Exception:
         pass
    return 'bi-tag'
