"""Landing page A/B experiment: the short tour page vs the classic long page (LANDING_TOUR_PERCENT)."""
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import translation

TOUR = 'id="tp-sim"'
CLASSIC = 'window.tmrLandingVariant="classic"'


class LandingExperimentTests(TestCase):
    def setUp(self):
        cache.clear()
        translation.activate('en')
        self.url = reverse('landing')

    def tearDown(self):
        cache.clear()

    def get(self, *args, **kw):
        return self.client.get(self.url, *args, **kw)

    @override_settings(LANDING_TOUR_PERCENT=100)
    def test_100_percent_is_tour_for_everyone(self):
        r = self.get(HTTP_COOKIE='tmr_lp=classic')
        self.assertContains(r, TOUR)
        self.assertNotContains(r, CLASSIC)
        self.assertEqual(r.context['landing_variant'], 'tour')
        self.assertNotIn('tmr_lp', r.cookies)

    @override_settings(LANDING_TOUR_PERCENT=0)
    def test_0_percent_is_classic_for_everyone(self):
        r = self.get()
        self.assertContains(r, CLASSIC)
        self.assertNotContains(r, TOUR)
        self.assertEqual(r.context['landing_variant'], 'classic')
        self.assertNotIn('tmr_lp', r.cookies)

    @override_settings(LANDING_TOUR_PERCENT=50)
    def test_cookie_keeps_the_variant(self):
        self.client.cookies['tmr_lp'] = 'classic'
        r = self.get()
        self.assertContains(r, CLASSIC)
        self.assertNotIn('tmr_lp', r.cookies)
        self.client.cookies['tmr_lp'] = 'tour'
        self.assertContains(self.get(), TOUR)

    @override_settings(LANDING_TOUR_PERCENT=50)
    def test_garbage_cookie_is_reassigned(self):
        self.client.cookies['tmr_lp'] = 'garbage'
        with mock.patch('expenses.views.auth.random.random', return_value=0.9):
            r = self.get()
        self.assertEqual(r.context['landing_variant'], 'classic')
        self.assertEqual(r.cookies['tmr_lp'].value, 'classic')

    @override_settings(LANDING_TOUR_PERCENT=50)
    def test_new_visitor_is_assigned_by_percent_and_cookie_is_set(self):
        with mock.patch('expenses.views.auth.random.random', return_value=0.1):
            r = self.get()
        self.assertEqual(r.context['landing_variant'], 'tour')
        c = r.cookies['tmr_lp']
        self.assertEqual(c.value, 'tour')
        self.assertEqual(c['samesite'], 'Lax')
        self.assertEqual(int(c['max-age']), 90 * 24 * 3600)

    @override_settings(LANDING_TOUR_PERCENT=100)
    def test_override_sets_cookie_and_is_never_shared_cached(self):
        r = self.get({'lp': 'classic'})
        self.assertContains(r, CLASSIC)
        self.assertEqual(r.cookies['tmr_lp'].value, 'classic')
        self.assertEqual(r['Cache-Control'], 'private, no-cache')
        self.assertIn('Cookie', r['Vary'])
        r = self.get({'lp': 'nonsense'})
        self.assertContains(r, TOUR)
        self.assertNotIn('tmr_lp', r.cookies)

    def test_cache_headers_in_the_three_regimes(self):
        for percent, shared in [(100, True), (0, True), (50, False)]:
            with self.subTest(percent=percent), override_settings(LANDING_TOUR_PERCENT=percent):
                self.client.cookies.clear()
                r = self.get()
                if shared:
                    self.assertIn('s-maxage=300', r['Cache-Control'])
                else:
                    self.assertEqual(r['Cache-Control'], 'private, no-cache')
                    self.assertIn('Cookie', r['Vary'])

    @override_settings(LANDING_TOUR_PERCENT=50)
    def test_both_variants_render_with_a_marker_each(self):
        for variant, marker in [('tour', TOUR), ('classic', CLASSIC)]:
            r = self.get({'lp': variant})
            self.assertEqual(r.status_code, 200)
            self.assertContains(r, marker)

    @override_settings(LANDING_TOUR_PERCENT=0)
    def test_classic_without_a_demo_user_still_renders_its_net_worth_widget(self):
        r = self.get()
        self.assertEqual(r.context['demo_networth_rows'], [])
        self.assertContains(r, 'id="networth-demo-data"')
