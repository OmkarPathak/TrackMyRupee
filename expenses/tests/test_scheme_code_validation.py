"""A mutual fund's scheme code goes into a request path to the NAV service, so it must be a plain number."""
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from expenses import holdings_service as hs
from expenses.models import Account, Holding
from expenses.nav_provider import MFAPIProvider, fetch_scheme_history, is_scheme_code

BAD_CODES = ['../../etc/passwd', '1/../2', '12?x=1', '12#frag', '12 34', 'abc', '12.5', '१०१२३४', '١٢٣', '1234567890123', '%2e%2e']


class CleanSchemeCodeTests(SimpleTestCase):
    def test_a_number_passes_and_is_trimmed(self):
        self.assertEqual(hs.clean_scheme_code('101234'), '101234')
        self.assertEqual(hs.clean_scheme_code('  101234 '), '101234')

    def test_nothing_given_means_no_code(self):
        self.assertEqual(hs.clean_scheme_code(''), '')
        self.assertEqual(hs.clean_scheme_code(None), '')
        self.assertEqual(hs.clean_scheme_code('   '), '')

    def test_anything_that_is_not_a_plain_number_is_refused(self):
        for code in BAD_CODES:
            with self.assertRaises(hs.HoldingError, msg=code):
                hs.clean_scheme_code(code)

    def test_the_message_says_what_a_code_looks_like(self):
        with self.assertRaises(hs.HoldingError) as caught:
            hs.clean_scheme_code('../x')
        self.assertIn('101234', str(caught.exception))


class NavProviderGuardTests(SimpleTestCase):
    def test_is_scheme_code(self):
        self.assertTrue(is_scheme_code('120503'))
        for code in BAD_CODES + ['', None]:
            self.assertFalse(is_scheme_code(code), code)

    def test_a_bad_code_never_reaches_the_network(self):
        with patch('requests.get') as get:
            self.assertIsNone(MFAPIProvider().fetch_latest('../../x'))
            self.assertIsNone(fetch_scheme_history('1/../2'))
        get.assert_not_called()

    def test_a_good_code_is_requested_at_the_expected_address(self):
        with patch('requests.get', return_value=MagicMock(status_code=404)) as get:
            MFAPIProvider().fetch_latest('120503')
            fetch_scheme_history('120503')
        self.assertEqual([call.args[0] for call in get.call_args_list],
                         ['https://api.mfapi.in/mf/120503/latest', 'https://api.mfapi.in/mf/120503'])


class CreatingAHoldingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('fund-user', password='pw')
        profile = self.user.profile
        profile.tier, profile.has_seen_tutorial, profile.consent_granted = 'PRO', True, True
        profile.save()
        self.account = Account.objects.create(user=self.user, name='Funds', account_type='MUTUAL_FUND',
                                              balance=Decimal('0'), currency='₹')
        self.client.force_login(self.user)

    def test_a_holding_with_a_good_code_is_created(self):
        holding = hs.create_holding(self.account, name='Some Fund', units='10', price='100', scheme_code='101234')
        self.assertEqual(holding.scheme_code, '101234')

    def test_a_holding_with_a_bad_code_is_refused_and_nothing_is_created(self):
        with self.assertRaises(hs.HoldingError):
            hs.create_holding(self.account, name='Some Fund', units='10', price='100', scheme_code='../../x')
        self.assertFalse(Holding.objects.filter(account=self.account).exists())

    def test_an_empty_holding_with_a_bad_code_is_refused(self):
        with self.assertRaises(hs.HoldingError):
            hs.create_empty_holding(self.account, name='Some Fund', scheme_code='a/b')
        self.assertFalse(Holding.objects.filter(account=self.account).exists())

    def test_the_add_holding_form_says_so_instead_of_saving_it(self):
        with patch('expenses.nav_provider.NAVFetchService.fetch_scheme') as fetch:
            response = self.client.post(reverse('holding-create', args=[self.account.pk]), {
                'instrument_name': 'Some Fund', 'units': '10', 'avg_cost': '100', 'scheme_code': '1/../2',
                'date': '2026-09-01'}, follow=True)
        self.assertFalse(Holding.objects.filter(account=self.account).exists())
        self.assertContains(response, 'A fund code is a number')
        fetch.assert_not_called()
