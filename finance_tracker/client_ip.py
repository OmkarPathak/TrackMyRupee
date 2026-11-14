"""The address a request really came from, for rate limits and audit logs.

Behind a proxy the connection's own address (REMOTE_ADDR) is the proxy's, so the real client is read from
X-Forwarded-For. That header is a list the client can start with anything it likes: each proxy only *appends* the
address it saw. So the entries to believe are the ones our own proxies wrote, at the end of the list.

Which entries are ours is decided by address, not by position: the connection address and then the list are read from
the right, skipping every hop that is a trusted proxy (`settings.TRUSTED_PROXY_RANGES`: private and loopback networks,
which cover nginx and Docker, plus Cloudflare). The first hop that is not a trusted proxy is the client. Anything a
caller put in front of it is never read, with or without a CDN, and a request that reaches the origin directly is
attributed to the caller's real address.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from django.conf import settings

# Always trusted as proxies: loopback, private and link-local networks (nginx, Docker, an internal load balancer).
PRIVATE_NETWORKS = (
    '127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '169.254.0.0/16',
    '::1/128', 'fc00::/7', 'fe80::/10',
)

# https://www.cloudflare.com/ips/ (check this list there when it changes; a missing range is safe, see below).
CLOUDFLARE_NETWORKS = (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22', '141.101.64.0/18', '108.162.192.0/18',
    '190.93.240.0/20', '188.114.96.0/20', '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32', '2405:8100::/32', '2a06:98c0::/29',
    '2c0f:f248::/32',
)
# A Cloudflare range that is missing from the list makes visitors on it look like that Cloudflare address: they share
# one rate-limit bucket, but nobody can pick the address that is recorded.

DEFAULT_TRUSTED_PROXY_RANGES = PRIVATE_NETWORKS + CLOUDFLARE_NETWORKS


def _valid(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


@lru_cache(maxsize=8)
def _networks(ranges: tuple) -> tuple:
    return tuple(ipaddress.ip_network(r.strip(), strict=False) for r in ranges if r and r.strip())


def _is_trusted_proxy(address: str, networks) -> bool:
    ip = ipaddress.ip_address(address)
    return any(ip.version == net.version and ip in net for net in networks)


def client_ip(request) -> str | None:
    """The client address as text, or None when the request carries no usable one."""
    remote = _valid(request.META.get('REMOTE_ADDR') or '')
    if remote is None:
        return None
    ranges = tuple(getattr(settings, 'TRUSTED_PROXY_RANGES', DEFAULT_TRUSTED_PROXY_RANGES))
    networks = _networks(ranges)
    chain = [part.strip() for part in (request.META.get('HTTP_X_FORWARDED_FOR') or '').split(',') if part.strip()]
    for hop in [remote] + chain[::-1]:                  # the connection first, then the list from its end
        address = _valid(hop)
        if address is None:
            return remote                               # not an address: the header is not trustworthy past here
        if not _is_trusted_proxy(address, networks):
            return address
    return remote                                       # every hop is a trusted proxy (a local request)
