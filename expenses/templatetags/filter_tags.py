import json
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.inclusion_tag('components/filter_toolbar.html', takes_context=True)
def render_filter_toolbar(context, config, applied_state=None):
    if applied_state is None:
        applied_state = context.get('applied_state', {})

    # Ensure config object serialization
    config_dict = config.to_dict() if hasattr(config, 'to_dict') else config

    return {
        'request': context.get('request'),
        'filter_config': config,
        'filter_config_json': mark_safe(json.dumps(config_dict)),
        'applied_state_json': mark_safe(json.dumps(applied_state)),
        'applied_state': applied_state,
    }
