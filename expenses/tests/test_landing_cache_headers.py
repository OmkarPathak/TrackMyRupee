"""The landing page may be kept briefly by a shared cache, but only the anonymous English copy."""
from django.test import TestCase
from django.urls import reverse


class LandingCacheHeaderTests(TestCase):
    def get(self, **headers):
        return self.client.get(reverse('landing'), **headers)

    def test_an_anonymous_english_visitor_gets_a_page_a_cache_may_keep(self):
        response = self.get(HTTP_ACCEPT_LANGUAGE='en-US,en;q=0.9')
        self.assertEqual(response.status_code, 200)
        self.assertIn('s-maxage=300', response['Cache-Control'])
        self.assertIn('public', response['Cache-Control'])

    def test_another_language_is_never_kept_for_everyone(self):
        for language in ('hi', 'mr'):
            response = self.get(HTTP_ACCEPT_LANGUAGE=language)
            self.assertEqual(response['Cache-Control'], 'private, no-cache', language)

    def test_a_visitor_with_a_session_or_a_chosen_language_gets_their_own_copy(self):
        self.client.cookies['sessionid'] = 'abc'
        self.assertEqual(self.get()['Cache-Control'], 'private, no-cache')
        self.client.cookies.clear()
        self.client.cookies['django_language'] = 'en'
        self.assertEqual(self.get()['Cache-Control'], 'private, no-cache')

    def test_the_page_sets_no_cookie_so_a_cdn_can_keep_it(self):
        response = self.get(HTTP_ACCEPT_LANGUAGE='en')
        self.assertNotIn('Set-Cookie', response.headers)
