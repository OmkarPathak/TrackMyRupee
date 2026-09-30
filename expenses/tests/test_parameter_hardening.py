from datetime import date

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase
from django.urls import reverse

from expenses.views.utils import parse_month_year


class ParseMonthYearTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.fixed_today = date(2026, 4, 15)

    def test_valid_month_and_year(self):
        request = self.factory.get('/', {'month': '7', 'year': '2024'})
        m, y = parse_month_year(request, today=self.fixed_today)
        self.assertEqual(m, 7)
        self.assertEqual(y, 2024)

    def test_non_integer_month(self):
        request = self.factory.get('/', {'month': 'abc', 'year': '2024'})
        m, y = parse_month_year(request, today=self.fixed_today)
        self.assertEqual(m, 4)
        self.assertEqual(y, 2024)

    def test_non_integer_year(self):
        request = self.factory.get('/', {'month': '7', 'year': 'def'})
        m, y = parse_month_year(request, today=self.fixed_today)
        self.assertEqual(m, 7)
        self.assertEqual(y, 2026)

    def test_out_of_bounds_month(self):
        # Month < 1
        req1 = self.factory.get('/', {'month': '0'})
        m1, _ = parse_month_year(req1, today=self.fixed_today)
        self.assertEqual(m1, 4)

        # Month > 12
        req2 = self.factory.get('/', {'month': '13'})
        m2, _ = parse_month_year(req2, today=self.fixed_today)
        self.assertEqual(m2, 4)

    def test_out_of_bounds_year(self):
        # Year < 2000
        req1 = self.factory.get('/', {'year': '1995'})
        _, y1 = parse_month_year(req1, today=self.fixed_today)
        self.assertEqual(y1, 2026)

        # Year > today.year + 5
        req2 = self.factory.get('/', {'year': '99999'})
        _, y2 = parse_month_year(req2, today=self.fixed_today)
        self.assertEqual(y2, 2026)

    def test_missing_params(self):
        request = self.factory.get('/')
        m, y = parse_month_year(request, today=self.fixed_today)
        self.assertEqual(m, 4)
        self.assertEqual(y, 2026)


class HardenedViewsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='harden_tester', password='password123')
        profile = self.user.profile
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.tier = 'PLUS'
        profile.is_lifetime = True
        profile.save()
        self.client.login(username='harden_tester', password='password123')

    def test_budget_dashboard_malformed_params(self):
        response = self.client.get(reverse('budget'), {'month': 'abc', 'year': '99999'})
        self.assertEqual(response.status_code, 200)

        response2 = self.client.get(reverse('budget'), {'month': '14', 'year': '-10'})
        self.assertEqual(response2.status_code, 200)

    def test_year_in_review_malformed_params(self):
        response = self.client.get(reverse('year_in_review_default'), {'year': 'invalid_year'})
        self.assertEqual(response.status_code, 200)

        response2 = self.client.get(reverse('year_in_review_default'), {'year': '99999'})
        self.assertEqual(response2.status_code, 200)

    def test_year_in_review_free_user_redirect_message(self):
        self.user.profile.tier = 'FREE'
        self.user.profile.is_lifetime = False
        self.user.profile.save()

        response = self.client.get(reverse('year_in_review_default'), follow=True)
        self.assertRedirects(response, reverse('pricing'))
        messages_list = list(response.context['messages'])
        self.assertTrue(any('Year in Review is a Premium feature' in m.message for m in messages_list))
