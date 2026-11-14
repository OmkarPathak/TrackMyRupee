"""A public URL that raises an error on purpose lets anyone burn the error-monitoring quota, so none may exist."""
from django.test import Client, SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver


def every_route():
    found = []

    def walk(patterns, prefix=''):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                found.append((prefix + str(pattern.pattern), pattern.callback))
    walk(get_resolver().url_patterns)
    return found


class NoDeliberateErrorRouteTests(SimpleTestCase):
    def test_the_old_sentry_test_url_is_gone(self):
        response = Client(raise_request_exception=False).get('/sentry-debug/')
        self.assertEqual(response.status_code, 404)

    def test_no_route_is_an_anonymous_function(self):
        """The old route was `lambda request: 1 / 0`: a throwaway view nobody reviews. Every route is a named view."""
        lambdas = [route for route, callback in every_route() if getattr(callback, '__name__', '') == '<lambda>']
        self.assertEqual(lambdas, [])

    def test_no_route_is_named_like_a_debug_or_test_hook(self):
        suspicious = [route for route, _callback in every_route()
                      if any(word in route.lower() for word in ('sentry-debug', 'debug-', '/debug', 'raise-error', 'crash'))]
        self.assertEqual(suspicious, [])
