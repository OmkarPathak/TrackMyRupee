from django import template

from expenses.family.presenters import display_name, initials

register = template.Library()


@register.filter
def member_initials(user):
    return initials(user)


@register.filter
def member_name(user):
    return display_name(user)
