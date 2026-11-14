"""Browser-side limits added to every response.

A Content-Security-Policy is the second line of defence against injected script. A full policy (script-src) needs the
inline scripts the pages use today to move out first, so this sets the restrictions that cost nothing: no plug-in
content, no <base> tag redirecting relative links, and framing only by the site itself. It is a baseline to grow from,
and `CONTENT_SECURITY_POLICY` in settings replaces it (set it to an empty string to send none).

Permissions-Policy switches off browser features the app never uses, so a script that did get onto a page could not
reach the camera, location or USB. The microphone stays on for the spoken-expense input.
"""
from django.conf import settings

DEFAULT_CSP = "object-src 'none'; base-uri 'self'; frame-ancestors 'self'"
DEFAULT_PERMISSIONS_POLICY = ('camera=(), geolocation=(), microphone=(self), usb=(), serial=(), bluetooth=(), '
                              'accelerometer=(), gyroscope=(), magnetometer=()')


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        csp = getattr(settings, 'CONTENT_SECURITY_POLICY', DEFAULT_CSP)
        if csp:
            response.headers.setdefault('Content-Security-Policy', csp)       # a view that sets its own keeps it
        policy = getattr(settings, 'PERMISSIONS_POLICY', DEFAULT_PERMISSIONS_POLICY)
        if policy:
            response.headers.setdefault('Permissions-Policy', policy)
        return response
