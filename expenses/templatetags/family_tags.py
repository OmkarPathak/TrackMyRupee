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
    # the toggle can appear more than once on a page: look the household up once per request
    cached = getattr(request, '_family_toggle_state', None)
    if cached is None:
        household = households.household_for(user) if family_enabled_for(user) else None
        paused = household is not None and plan_rules.household_plan_state(household).state == plan_rules.PAUSED
        cached = (household is not None, paused)
        if request is not None:
            request._family_toggle_state = cached
    if not cached[0]:
        return {'show': False}
    paused = cached[1]
    me_url = reverse('analytics-networth') if page == 'networth' else reverse('account-list')
    family_url = reverse('family-networth') if page == 'networth' else reverse('family-inventory')
    return {'show': True, 'current': current, 'me_url': me_url, 'family_url': family_url, 'paused': paused,
            'family_home': reverse('family-home')}
