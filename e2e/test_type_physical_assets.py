"""Physical assets (real estate, vehicle): worth the latest valuation entered, or what was paid when none has been.

The account is tied to a physical asset record. Its value comes from that asset's valuations, newest date first.
"""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import balance_on, net_worth, open_account

D = Decimal
TODAY = timezone.localdate()
KINDS = ('REAL_ESTATE', 'VEHICLE')


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('asset-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 100000)

    def asset(self, kind='REAL_ESTATE', name='Flat', cost=5000000, bought_days_ago=730, **extra):
        from expenses.models import PhysicalAsset
        return PhysicalAsset.objects.create(user=self.user, name=name, asset_class=kind, acquisition_cost=None if cost is None else D(str(cost)),
                                            acquisition_date=TODAY - timedelta(days=bought_days_ago), currency='₹', **extra)

    def account(self, asset, kind='REAL_ESTATE', name=None):
        return make_account(self.user, name or asset.name, kind, 0, linked_physical_asset=asset)

    def value(self, asset, amount, days_ago=0):
        from expenses.models import AssetValuation
        return AssetValuation.objects.create(asset=asset, value=D(str(amount)), as_of_date=TODAY - timedelta(days=days_ago))


class ValueTests(Base):
    def test_with_no_valuation_the_asset_is_worth_what_was_paid_for_it(self):
        asset = self.asset(cost=5000000)
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('5100000'))

    def test_the_latest_valuation_replaces_the_cost(self):
        asset = self.asset(cost=5000000)
        self.value(asset, 5800000, days_ago=30)
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('5900000'))

    def test_the_newest_date_wins_even_when_it_was_entered_first(self):
        asset = self.asset(cost=5000000)
        self.value(asset, 6000000, days_ago=1)
        self.value(asset, 4000000, days_ago=400)                 # entered later, but an older valuation
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('6100000'))

    def test_two_valuations_on_one_day_use_the_one_entered_last(self):
        asset = self.asset(cost=5000000)
        self.value(asset, 5100000, days_ago=5)
        self.value(asset, 5200000, days_ago=5)
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('5300000'))

    def test_a_vehicle_losing_value_lowers_net_worth(self):
        asset = self.asset('VEHICLE', 'Car', cost=900000, bought_days_ago=1095)
        self.value(asset, 620000, days_ago=2)
        self.account(asset, 'VEHICLE')
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('720000'))

    def test_a_valuation_of_zero_means_worth_nothing_not_the_cost(self):
        asset = self.asset('VEHICLE', 'Scrapped car', cost=300000)
        self.value(asset, 0)
        self.account(asset, 'VEHICLE')
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('100000'))

    def test_an_asset_with_no_cost_and_no_valuation_is_worth_nothing(self):
        asset = self.asset(cost=None)
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('100000'))

    def test_paise_in_a_valuation_are_kept(self):
        asset = self.asset(cost=1000000)
        self.value(asset, '1234567.89')
        self.account(asset)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('1334567.89'))

    def test_an_inactive_account_stops_counting(self):
        asset = self.asset(cost=2000000)
        account = self.account(asset)
        type(account).objects.filter(pk=account.pk).update(is_active=False)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('100000'))

    def test_the_asset_accounts_own_page_shows_its_value(self):
        """The account's own page shows the same amount as the list and net worth, not the empty ledger."""
        asset = self.asset(cost=5000000)
        account = self.account(asset)
        page = self.new_page(self.user)
        open_account(self, page, account)
        self.assertEqual(balance_on(page), D('5000000.00'))


class FormTests(Base):
    def fill(self, page, kind, name, *, cost=None, bought=None):
        self.open(page, reverse('account-create') + f'?type={kind}')
        page.fill('#id_name', name)
        page.click('#nextBtn')
        page.wait_for_selector('#id_acquisition_cost', state='visible')
        if cost is not None:
            page.fill('#id_acquisition_cost', cost)
        if bought is not None:
            page.fill('#id_acquisition_date', bought.isoformat())
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()

    def test_each_kind_is_created_with_its_asset_and_a_first_valuation_at_cost(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for kind in KINDS:
            with self.subTest(kind):
                self.fill(page, kind, f'New {kind}', cost='2500000.50', bought=TODAY - timedelta(days=100))
                self.wait_until(lambda k=kind: Account.objects.filter(user=self.user, name=f'New {k}').exists(), f'{kind} was not saved', seconds=15)
                account = Account.objects.get(user=self.user, name=f'New {kind}')
                asset = account.linked_physical_asset
                self.assertEqual((asset.asset_class, asset.acquisition_cost), (kind, D('2500000.50')))
                self.assertEqual([v.value for v in asset.valuations.all()], [D('2500000.50')])

    def test_the_cost_and_the_date_are_both_required(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        for label, terms, message in (('no cost', dict(bought=TODAY), 'Acquisition cost is required'),
                                      ('no date', dict(cost='1000'), 'Acquisition date is required')):
            with self.subTest(label):
                self.fill(page, 'REAL_ESTATE', f'Asset {label}', **terms)
                page.wait_for_timeout(1000)
                self.assertIn(message, page.inner_text('body'))
                self.assertFalse(Account.objects.filter(user=self.user, name=f'Asset {label}').exists())

    def test_an_existing_asset_can_be_chosen_instead_of_making_a_new_one(self):
        from expenses.models import Account, PhysicalAsset
        existing = self.asset(name='Already recorded', cost=1500000)
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=REAL_ESTATE')
        page.fill('#id_name', 'Second view')
        page.click('#nextBtn')
        page.wait_for_selector('#id_acquisition_cost', state='visible')
        page.locator('button.btn-toggle:has-text("Select Existing")').click()
        self.set_select(page, '#id_linked_physical_asset', str(existing.pk))
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Second view').exists(), 'the account was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Second view').linked_physical_asset_id, existing.pk)
        self.assertEqual(PhysicalAsset.objects.filter(user=self.user).count(), 1)

    def test_another_persons_asset_is_not_offered(self):
        from expenses.models import PhysicalAsset
        other = make_user('not-me')
        PhysicalAsset.objects.create(user=other, name='Their house', asset_class='REAL_ESTATE', acquisition_cost=D('100'))
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=REAL_ESTATE')
        options = page.locator('#id_linked_physical_asset option').all_inner_texts()
        self.assertFalse(any('Their house' in option for option in options))
