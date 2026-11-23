from django import template
from urllib.parse import urlencode

from django.conf import settings
from django.utils import translation

register = template.Library()

GUIDE_LANGUAGES = ('hi', 'mr')


def guide_url(path: str = '', source: str = '') -> str:
    """The address of a guide page in the person's language (the guide has Hindi and Marathi under /hi/ and /mr/).

    With a ``source`` (where the link sits: topbar, more-menu, ...) the address carries the parameters that tell the guide's own
    analytics the visit came from the app. They are in the address itself, so middle-click, "open in a new tab" and "copy link"
    carry them too; the guide's canonical addresses have none, so search is unaffected."""
    language = (translation.get_language() or 'en')[:2]
    prefix = f'{language}/' if language in GUIDE_LANGUAGES else ''
    url = f'{settings.DOCS_BASE_URL}/{prefix}{path.lstrip("/")}'
    if source:
        url += '?' + urlencode({'utm_source': 'trackmyrupee-app', 'utm_medium': 'guide-link', 'utm_content': source})
    return url


@register.simple_tag
def docs_url(path='', source=''):
    return guide_url(path, source)


@register.inclusion_tag('components/help_link.html')
def docs_help(path, label='', source=''):
    """A link to a page of the guide, in a new tab.

    {% docs_help '07-budgets/' %} is a "?" button; {% docs_help '07-budgets/' _('How budgets work') %} a text link.
    `source` names where it sits, for counting which links get used."""
    return {'path': path, 'label': label, 'url': guide_url(path, source), 'source': source}
