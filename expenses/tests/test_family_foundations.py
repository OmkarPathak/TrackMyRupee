"""Phase 0 of family sharing: net worth for a chosen set of accounts, and the canary harness itself."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings

from expenses.ledger_read_service import LedgerReadService
from expenses.models import Account, Loan, Notification

from .family_canary import MARKERS, CanaryWorld

D = Decimal


def make_user(username, currency='₹'):
    user = User.objects.create_user(username=username, password='password')
    profile = user.profile
    profile.currency = currency
    profile.save()
    return user


@override_settings(LEDGER_READ_ENABLED=True, LEDGER_WRITE_ENABLED=True, NET_WORTH_EXTENDED_MODELS_ENABLED=True)
class NetWorthForChosenAccountsTests(TestCase):
    def setUp(self):
        self.user = make_user('nw_subset')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT',
                                           balance=D('10000.00'), currency='₹')
        self.card = Account.objects.create(user=self.user, name='Card', account_type='CREDIT_CARD',
                                           balance=D('-2500.00'), currency='₹')
        self.private = Account.objects.create(user=self.user, name='Private', account_type='SAVINGS_ACCOUNT',
                                              balance=D('77777.00'), currency='₹')

    def test_only_the_chosen_accounts_are_valued(self):
        detail = {}
        total, per_account = LedgerReadService.get_net_worth(
            self.user, only_account_ids=[self.bank.id, self.card.id], detail=detail)
        self.assertEqual(total, D('7500.00'))
        self.assertEqual(set(per_account), {self.bank.pk, self.card.pk})
        self.assertEqual(detail, {'assets': D('10000.00'), 'liabilities': D('2500.00')})

    def test_the_full_net_worth_is_unchanged(self):
        total, per_account = LedgerReadService.get_net_worth(self.user)
        self.assertEqual(total, D('85277.00'))
        self.assertEqual(len(per_account), 3)

    def test_a_private_account_never_enters_the_subset(self):
        total, per_account = LedgerReadService.get_net_worth(self.user, only_account_ids=[self.bank.id])
        self.assertEqual(total, D('10000.00'))
        self.assertNotIn(self.private.pk, per_account)

    def test_an_empty_selection_is_worth_nothing(self):
        detail = {}
        total, per_account = LedgerReadService.get_net_worth(self.user, only_account_ids=[], detail=detail)
        self.assertEqual((total, per_account), (D('0.00'), {}))
        self.assertEqual(detail, {'assets': D('0.00'), 'liabilities': D('0.00')})

    def test_another_users_account_id_is_ignored(self):
        other = make_user('nw_other')
        theirs = Account.objects.create(user=other, name='Theirs', account_type='SAVINGS_ACCOUNT',
                                        balance=D('999.00'), currency='₹')
        total, per_account = LedgerReadService.get_net_worth(self.user, only_account_ids=[self.bank.id, theirs.id])
        self.assertEqual(total, D('10000.00'))
        self.assertNotIn(theirs.pk, per_account)

    def test_a_loan_no_account_stands_for_is_not_added_to_a_subset(self):
        Loan.objects.create(user=self.user, name='Loose loan', loan_type='PERSONAL',
                            initial_principal=D('50000.00'), duration_months=12, start_date=date(2026, 1, 1))
        everything, _ = LedgerReadService.get_net_worth(self.user)
        subset, _ = LedgerReadService.get_net_worth(self.user, only_account_ids=[self.bank.id])
        self.assertLess(everything, D('85277.00'))
        self.assertEqual(subset, D('10000.00'))

    def test_an_inactive_account_is_ignored(self):
        Account.objects.filter(pk=self.bank.pk).update(is_active=False)
        total, per_account = LedgerReadService.get_net_worth(self.user, only_account_ids=[self.bank.id])
        self.assertEqual((total, per_account), (D('0.00'), {}))


class CanaryHarnessTests(TestCase):
    """The harness has to catch a leak, or every later leak test proves nothing."""

    def setUp(self):
        self.owner = make_user('canary_owner')
        self.world = CanaryWorld.seed(self.owner)

    def test_every_marker_is_planted_in_the_owners_data(self):
        from expenses.models import Expense, Income, PhysicalAsset
        self.assertTrue(Account.objects.filter(name=MARKERS['private_account']).exists())
        self.assertTrue(Expense.objects.filter(description=MARKERS['transaction']).exists())
        self.assertTrue(Expense.objects.filter(category=MARKERS['category']).exists())
        self.assertTrue(Income.objects.filter(description=MARKERS['income']).exists())
        self.assertTrue(PhysicalAsset.objects.filter(policy_number=MARKERS['policy_number']).exists())

    def test_a_clean_page_passes(self):
        self.world.assert_clean('<h1>Family net worth</h1><p>HDFC Joint Savings ₹3,42,000</p>')

    def test_a_leak_in_a_page_is_caught_and_named(self):
        with self.assertRaises(AssertionError) as caught:
            self.world.assert_clean(f'<td>{MARKERS["transaction"]}</td>')
        self.assertIn('transaction', str(caught.exception))

    def test_a_leak_is_found_in_every_kind_of_surface(self):
        self.assertEqual(self.world.leaked({'rows': [{'note': MARKERS['income']}]}), ['income'])
        self.assertEqual(self.world.leaked(b'xx ' + MARKERS['policy_number'].encode()), ['policy_number'])
        email = mail.EmailMessage(subject='Hi', body=f'see {MARKERS["category"]}', to=['a@example.com'])
        self.assertEqual(self.world.leaked(email), ['category'])
        note = Notification(user=self.owner, title='T', message=MARKERS['private_account'])
        self.assertEqual(self.world.leaked(note), ['private_account'])

    def test_a_marker_that_is_allowed_does_not_fail(self):
        self.world.assert_clean(MARKERS['transaction'], allow=['transaction'])

    def test_a_response_header_or_cookie_leak_is_caught(self):
        response = self.client.get('/', follow=False)
        response.headers['X-Debug'] = MARKERS['second_transaction']
        self.assertEqual(self.world.leaked(response), ['second_transaction'])
