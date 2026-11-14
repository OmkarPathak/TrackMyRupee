"""A URL that takes an id must answer 404 or 405 for an id that does not exist (or a method it does not support), never
a 500. The maturity-income view used to crash on any GET because of a mixin it did not need."""
import re
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from expenses.models import Account, Income

CONVERTER = re.compile(r'<(?:(\w+):)?(\w+)>')
SKIPPED = ('admin', 'api/cron', 'api/razorpay', 'demo')


def id_routes():
    routes = []

    def walk(patterns, prefix=''):
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                walk(pattern.url_patterns, prefix + str(pattern.pattern))
            elif isinstance(pattern, URLPattern):
                routes.append(prefix + str(pattern.pattern))
    walk(get_resolver().url_patterns)
    usable = []
    for route in sorted(set(routes)):
        if '^' in route or '(?' in route or route.startswith(SKIPPED) or not CONVERTER.search(route):
            continue
        if {(m.group(1) or 'str') for m in CONVERTER.finditer(route)} <= {'int', 'uuid', 'uuid_or_int'}:
            usable.append(route)
    return usable


def fill(route, number):
    def sub(match):
        kind = match.group(1)
        return str(number) if kind == 'int' else str(uuid.uuid4()) if kind == 'uuid' else str(number)
    return '/' + CONVERTER.sub(sub, route)


class MaturityIncomeViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('maturity-user', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.consent_granted = 'PRO', True, True
        profile.save()
        self.other = User.objects.create_user('maturity-other', password='pw')
        self.fd = Account.objects.create(
            user=self.user, name='FD', account_type='FD', balance=Decimal('10000'), currency='₹',
            deposit_principal=Decimal('10000'), deposit_rate=Decimal('10'), deposit_compounding='SIMPLE',
            deposit_start_date=timezone.localdate() - timedelta(days=365),
            deposit_maturity_date=timezone.localdate() - timedelta(days=1))
        self.url = reverse('account-record-maturity-income', args=[self.fd.pk])
        self.client = Client(raise_request_exception=False)

    def test_a_get_by_the_owner_answers_405_not_500(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertIn('POST', response.headers['Allow'])

    def test_a_get_by_someone_else_is_also_405_and_reveals_nothing(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_signed_out_get_goes_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.headers['Location'])

    def test_the_post_is_still_refused_for_someone_elses_account(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertFalse(Income.objects.filter(account=self.fd).exists())

    def test_the_post_still_works_for_the_owner(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.url).status_code, 302)


class NoIdUrlCrashesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('sweeper', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.consent_granted = 'PRO', True, True
        profile.save()

    def test_no_id_url_answers_500_for_an_id_that_does_not_exist(self):
        routes = id_routes()
        self.assertGreater(len(routes), 50)                  # the sweep really covers the app
        crashed = []
        for route in routes:
            for number in (999999, 1):
                path = fill(route, number)
                client = Client(raise_request_exception=False)
                client.force_login(self.user)
                for method in ('get', 'post'):
                    status = getattr(client, method)(path).status_code
                    if status >= 500:
                        crashed.append((method.upper(), status, re.sub(r'[0-9a-f-]{36}', '<uuid>', path)))
        self.assertEqual(sorted(set(crashed)), [])
