"""The test runner keeps the suite independent of the internet (exchange-rate APIs are stubbed)."""
import re
from datetime import datetime, timedelta, timezone
from unittest import mock

import requests
from django.test import SimpleTestCase, TestCase


class OfflineRunnerTests(SimpleTestCase):
    def test_requests_get_is_stubbed_in_this_process(self):
        """Runs in a parallel worker when the suite is run with --parallel; workers are spawned fresh,
        so this fails if only the parent process had the stub."""
        self.assertIsInstance(requests.get, mock.Mock)

    def test_exchange_rate_hosts_answer_without_network(self):
        response = requests.get('https://api.frankfurter.app/latest?from=USD&to=INR')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rates']['INR'], 83.0)

    def test_unknown_currency_is_rejected_like_the_real_service(self):
        response = requests.get('https://api.frankfurter.app/latest?from=ZZZ&to=INR')
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(requests.HTTPError):
            response.raise_for_status()


class SecurityTxtExpiryTests(TestCase):
    def test_expiry_is_not_close_and_not_over_a_year_away(self):
        body = self.client.get('/.well-known/security.txt').content.decode()
        expires = re.search(r'^Expires: (\S+)$', body, re.M).group(1)
        when = datetime.strptime(expires, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        self.assertGreater(when, now + timedelta(days=30), 'security.txt expires soon: renew Expires in finance_tracker/urls.py')
        self.assertLess(when, now + timedelta(days=366))
