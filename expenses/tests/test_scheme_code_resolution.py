"""A fund typed by name is matched to an AMFI scheme only when the name points to exactly one; otherwise the person is asked to pick
one, instead of the first of several being chosen."""
from unittest.mock import MagicMock, patch

from django.test import TestCase

from expenses.holdings_service import resolve_scheme_code
from expenses.models import AMFIScheme

DIRECT = 'UTI Nifty 50 Index Fund - Direct Plan - Growth'
REGULAR = 'UTI Nifty 50 Index Fund - Regular Plan - Growth'


class Base(TestCase):
    def setUp(self):
        AMFIScheme.objects.create(scheme_code='120716', scheme_name=DIRECT)
        AMFIScheme.objects.create(scheme_code='100001', scheme_name=REGULAR)
        AMFIScheme.objects.create(scheme_code='122639', scheme_name='Parag Parikh Flexi Cap Fund - Direct Plan - Growth')
        patcher = patch('requests.get', side_effect=AssertionError('the network must not be needed here'))
        self.network = patcher.start()
        self.addCleanup(patcher.stop)


class KnownSchemeTests(Base):
    def test_the_full_name_finds_its_scheme_whatever_the_case(self):
        self.assertEqual(resolve_scheme_code(DIRECT, 'MF'), '120716')
        self.assertEqual(resolve_scheme_code(REGULAR.lower(), 'MF'), '100001')

    def test_a_part_of_a_name_that_only_one_scheme_has_finds_it(self):
        self.assertEqual(resolve_scheme_code('parag parikh', 'MF'), '122639')

    def test_a_part_that_several_schemes_share_finds_none(self):
        self.assertEqual(resolve_scheme_code('UTI Nifty 50', 'MF'), '')      # (direct or regular: the person has to say)

    def test_a_stock_is_never_matched_to_a_fund(self):
        self.assertEqual(resolve_scheme_code(DIRECT, 'STOCK'), '')

    def test_nothing_typed_finds_nothing(self):
        self.assertEqual(resolve_scheme_code('', 'MF'), '')


class SearchServiceTests(TestCase):
    def reply(self, results):
        response = MagicMock(status_code=200)
        response.json.return_value = results
        return patch('requests.get', return_value=response)

    def test_one_result_is_used(self):
        with self.reply([{'schemeCode': 999, 'schemeName': 'Only Fund'}]):
            self.assertEqual(resolve_scheme_code('Only Fund', 'MF'), '999')

    def test_several_results_are_not_guessed_between(self):
        with self.reply([{'schemeCode': 1, 'schemeName': 'A'}, {'schemeCode': 2, 'schemeName': 'B'}]):
            self.assertEqual(resolve_scheme_code('Fund', 'MF'), '')

    def test_an_exact_name_among_several_results_is_used(self):
        with self.reply([{'schemeCode': 1, 'schemeName': 'Alpha Fund Growth'}, {'schemeCode': 2, 'schemeName': 'Alpha Fund'}]):
            self.assertEqual(resolve_scheme_code('alpha fund', 'MF'), '2')

    def test_no_result_finds_none(self):
        with self.reply([]):
            self.assertEqual(resolve_scheme_code('Nothing', 'MF'), '')

    def test_a_failing_service_finds_none_and_does_not_crash(self):
        with patch('requests.get', side_effect=Exception('down')):
            self.assertEqual(resolve_scheme_code('Anything', 'MF'), '')
