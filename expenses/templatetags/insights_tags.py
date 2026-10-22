from django import template

from ..insights import prefs, registry
from ..insights.gating import plan_of, surface_allowed
from ..insights.types import Surface
from ..insights.labels import MODULE_LABELS

register = template.Library()


@register.inclusion_tag('expenses/partials/_insights_settings.html', takes_context=True)
def insights_settings(context):
    """The Insights section of the Settings page: its own small form, posted to its own endpoint."""
    profile = context['request'].user.profile
    current = prefs.get(profile)
    return {
        'request': context['request'],
        'enabled': current['enabled'],
        'notify': current['notify'],
        'push': current['push'],
        'emergency_months': current['emergency_months'],
        'digest': current['digest'],
        'digest_available': surface_allowed(Surface.DIGEST, plan_of(profile)),
        'emergency_choices': prefs.EMERGENCY_MONTHS,
        'modules': [{'value': m.value, 'label': MODULE_LABELS[m], 'on': m.value not in current['modules_off']}
                    for m in registry.modules_with_rules()],
    }
