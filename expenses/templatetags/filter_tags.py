import json
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Same escapes Django's json_script uses: keeps user-controlled strings from closing the
# surrounding <script> element or being read as HTML.
_JSON_SCRIPT_ESCAPES = {
    ord('<'): '\\u003C',
    ord('>'): '\\u003E',
    ord('&'): '\\u0026',
    0x2028: '\\u2028',
    0x2029: '\\u2029',
}


def _script_safe_json(value):
    return mark_safe(json.dumps(value).translate(_JSON_SCRIPT_ESCAPES))


@register.inclusion_tag('components/filter_toolbar.html', takes_context=True)
def render_filter_toolbar(context, config, applied_state=None):
    if applied_state is None:
        applied_state = context.get('applied_state', {})

    # Ensure config object serialization
    config_dict = config.to_dict() if hasattr(config, 'to_dict') else config

    return {
        'request': context.get('request'),
        'filter_config': config,
        'filter_config_json': _script_safe_json(config_dict),
        'applied_state_json': _script_safe_json(applied_state),
        'applied_state': applied_state,
    }
