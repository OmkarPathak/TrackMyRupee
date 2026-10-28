from django import template

from expenses.family import family_enabled_for, households
from expenses.family import plan as plan_rules
from expenses.family.presenters import display_name, initials

register = template.Library()


@register.filter
def member_initials(user):
    return initials(user)


@register.filter
def member_name(user):
    return display_name(user)


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.inclusion_tag('expenses/family/_toggle.html', takes_context=True)
def family_toggle(context, page, current='me'):
    """The Me / Family switch on Net worth and Accounts. Shown only to members of a family. ``page`` is
    'networth' or 'accounts'; ``current`` is the side being shown."""
    from django.urls import reverse
    request = context.get('request')
    user = getattr(request, 'user', None)
    household = households.household_for(user) if family_enabled_for(user) else None
    if household is None:
        return {'show': False}
    paused = plan_rules.household_plan_state(household).state == plan_rules.PAUSED
    me_url = reverse('analytics-networth') if page == 'networth' else reverse('account-list')
    family_url = reverse('family-networth') if page == 'networth' else reverse('family-inventory')
    return {'show': True, 'current': current, 'me_url': me_url, 'family_url': family_url, 'paused': paused,
            'family_home': reverse('family-home')}
