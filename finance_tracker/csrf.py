"""What happens when a CSRF check fails.

A login or signup form that has been sitting open (a tab left overnight, an installed app resumed
from the background, the browser's back button, a second tab that already logged in) carries a token
that no longer matches. Showing Django's bare 403 page for that is hostile, so on the account pages we
send the person back to a fresh form with a short message. Every failure is logged with the reason
(never a token, cookie value, or form data) so a pattern, such as a missing cookie on one browser, shows up.
"""
import logging
from urllib.parse import urlparse

from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.csrf import csrf_failure as django_csrf_failure

logger = logging.getLogger('finance_tracker.csrf')

ACCOUNT_PREFIX = '/accounts/'


def _host(value):
    try:
        return urlparse(value or '').netloc or '-'
    except ValueError:
        return '?'


def csrf_failure(request, reason='', template_name='403_csrf.html'):
    meta = request.META
    logger.warning(
        'CSRF failure: reason=%r path=%s host=%s origin=%s referer=%s csrf_cookie=%s session_cookie=%s secure=%s ua=%s',
        reason, request.path, request.get_host(), _host(meta.get('HTTP_ORIGIN')), _host(meta.get('HTTP_REFERER')),
        'csrftoken' in request.COOKIES, 'sessionid' in request.COOKIES, request.is_secure(),
        (meta.get('HTTP_USER_AGENT') or '')[:120],
    )
    if request.method == 'POST' and request.path.startswith(ACCOUNT_PREFIX):
        messages.error(request, _('Your page had been open for a while and expired. Please try again.'))
        return redirect(request.path)
    return django_csrf_failure(request, reason=reason, template_name=template_name)
