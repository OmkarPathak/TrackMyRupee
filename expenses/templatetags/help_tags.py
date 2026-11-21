from django import template
from django.conf import settings
from django.utils import translation

register = template.Library()

GUIDE_LANGUAGES = ('hi', 'mr')


def guide_url(path: str = '') -> str:
    """The address of a guide page in the person's language (the guide has Hindi and Marathi under /hi/ and /mr/)."""
    language = (translation.get_language() or 'en')[:2]
    prefix = f'{language}/' if language in GUIDE_LANGUAGES else ''
    return f'{settings.DOCS_BASE_URL}/{prefix}{path.lstrip("/")}'


@register.simple_tag
def docs_url(path=''):
    return guide_url(path)


@register.inclusion_tag('components/help_link.html')
def docs_help(path, label='', source=''):
    """A link to a page of the guide, in a new tab.

    {% docs_help '07-budgets/' %} is a "?" button; {% docs_help '07-budgets/' _('How budgets work') %} a text link.
    `source` names where it sits, for counting which links get used."""
    return {'path': path, 'label': label, 'url': guide_url(path), 'source': source}
