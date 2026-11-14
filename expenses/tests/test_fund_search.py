"""Fund search finds a fund however the person spells it: "Large & Mid Cap" is also typed "large and midcap"."""
from unittest import mock

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from expenses.models import AMFIScheme
from expenses.views.accounts import fund_name_matches, fund_search_words, fund_word_variants

ICICI = 'ICICI Prudential Large & Mid Cap Fund - Direct Plan - Growth'


class WordTests(SimpleTestCase):
    def test_ampersand_reads_as_and_and_filler_words_are_dropped(self):
        self.assertEqual(fund_search_words('ICICI Prudential Large & Mid Cap Fund'),
                         ['icici', 'prudential', 'large', 'mid', 'cap'])
        self.assertEqual(fund_search_words('Large and Midcap Fund Direct Growth'),
                         ['large', 'midcap', 'direct', 'growth'])

    def test_cap_words_can_be_split_for_the_remote_search(self):
        self.assertEqual(fund_search_words('large and midcap', split_caps=True), ['large', 'mid', 'cap'])

    def test_a_cap_word_matches_every_spelling(self):
        self.assertEqual(fund_word_variants('midcap'), ['midcap', 'mid cap', 'mid-cap'])
        self.assertEqual(fund_word_variants('growth'), ['growth'])

    def test_name_matching(self):
        self.assertTrue(fund_name_matches(ICICI, fund_search_words('icici prudential large and midcap fund direct growth')))
        self.assertFalse(fund_name_matches(ICICI, fund_search_words('icici prudential small cap')))


class SearchViewTests(TestCase):
    def setUp(self):
        user = User.objects.create_user('fund-searcher', password='pw')
        self.client.force_login(user)
        AMFIScheme.objects.create(scheme_code='120596', scheme_name=ICICI)
        AMFIScheme.objects.create(scheme_code='100349', scheme_name='ICICI Prudential Large & Mid Cap Fund - Regular Plan - Growth')
        AMFIScheme.objects.create(scheme_code='900001', scheme_name='ICICI Prudential Bluechip Fund - Direct Plan - Growth')

    def search(self, q):
        return [r['scheme_code'] for r in self.client.get(reverse('search-amfi-schemes'), {'q': q}).json()['results']]

    def test_every_way_of_typing_the_fund_finds_it(self):
        for q in ('ICICI Prudential Large and midcap fund direct growth', 'icici large & mid cap direct',
                  'icici large and mid cap', 'icici large midcap', 'ICICI Prudential Large & Mid Cap Fund - Direct Plan - Growth'):
            self.assertIn('120596', self.search(q), q)

    def test_direct_plan_is_listed_before_regular_when_not_asked_for(self):
        self.assertEqual(self.search('icici large and midcap')[:2], ['120596', '100349'])

    def test_unrelated_funds_are_not_returned(self):
        self.assertNotIn('900001', self.search('icici large and midcap'))

    def test_remote_fallback_uses_meaningful_words_and_filters_the_answer(self):
        class Reply:
            status_code = 200

            @staticmethod
            def json():
                return [{'schemeCode': 120596, 'schemeName': ICICI},
                        {'schemeCode': 5, 'schemeName': 'Some Other Large Cap Fund'}]

        with mock.patch('requests.get', return_value=Reply) as get:
            codes = self.search('zzz large and midcap')           # not in the local table
        self.assertEqual(codes, [])                               # "zzz" matches neither remote name
        self.assertIn('q=zzz%20large%20mid%20cap', get.call_args[0][0])

        with mock.patch('requests.get', return_value=Reply):
            AMFIScheme.objects.all().delete()
            self.assertEqual(self.search('large and midcap'), ['120596'])
