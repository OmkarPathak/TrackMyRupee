"""The address a request really came from, for rate limits and audit logs.

Behind a proxy the connection's own address (REMOTE_ADDR) is the proxy's, so the real client is read from
X-Forwarded-For. That header is a list the client can start with anything it likes: each proxy only *appends* the
address it saw. So the list's first entry is the client's claim and its last entry is the one our own proxy wrote.
This helper trusts exactly `settings.TRUSTED_PROXY_COUNT` proxies and takes the entry that many places from the
end of the list; whatever a client puts in front of that is ignored.

    TRUSTED_PROXY_COUNT = 0   the app is reached directly: only REMOTE_ADDR is used (the default with DEBUG on)
    TRUSTED_PROXY_COUNT = 1   one proxy in front (nginx, a platform load balancer): the last entry (the default)
    TRUSTED_PROXY_COUNT = 2   two in front (a CDN and then nginx): the second from last
"""
from __future__ import annotations

import ipaddress

from django.conf import settings


def _valid(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request) -> str | None:
    """The trusted client address as text, or None when the request carries no usable one."""
    remote = _valid(request.META.get('REMOTE_ADDR') or '')
    count = int(getattr(settings, 'TRUSTED_PROXY_COUNT', 0) or 0)
    if count <= 0:
        return remote
    chain = [part.strip() for part in (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',') if part.strip()]
    if len(chain) < count:
        return remote                       # fewer hops than we trust: the header was not written by our proxies
    return _valid(chain[-count]) or remote
