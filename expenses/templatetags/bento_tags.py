"""The comparison line under each Dashboard metric card (Net Worth, Income, Spent, Invested).

Each card used to carry its own copy of this markup, and the copies had drifted: a space between the currency symbol and a
rounded amount, "▲ ₹0" in one card and "Same as before" in another. One tag now draws every card's line the same way."""
from __future__ import annotations

from decimal import Decimal

from django import template

register = template.Library()

BIG_CHANGE = 999        # a change of more than 999 percent reads "10x+"


@register.inclusion_tag('expenses/partials/_bento_delta.html')
def bento_delta(diff, pct, label, currency, good_when='up'):
    """``diff`` is the signed change, ``pct`` its percentage (may be empty), ``label`` what it is against (this month, vs
    last month). ``good_when`` is 'up' for a figure that is better when it rises (income, net worth, invested) and 'down'
    for one that is better when it falls (spent): it decides the colour, never the arrow."""
    try:
        change = Decimal(str(diff if diff is not None else 0))
    except Exception:
        change = Decimal('0')
    context = {'label': label, 'currency': currency, 'same': change == 0}
    if change == 0:
        return context
    rising = change > 0
    good = rising if good_when == 'up' else not rising
    context.update(amount=abs(change), arrow='up' if rising else 'down', tone='success' if good else 'danger', pct_text='')
    try:
        number = float(pct) if pct not in (None, '') else 0.0
    except (TypeError, ValueError):
        number = 0.0
    if number:
        if abs(number) > BIG_CHANGE:
            context['pct_text'] = '10x+'
        else:
            context['pct_text'] = f'{number:+.1f}%'
    return context
