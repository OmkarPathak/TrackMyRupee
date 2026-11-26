"""Insurance (life, health): what a policy is worth to net worth.

A savings or endowment policy is worth the surrender value entered, or else the premiums paid so far. Pure protection (term
or health cover) is worth nothing: its premiums are a cost, not an asset.
"""
from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from e2e.base import E2ETestCase, make_account, make_user
from e2e.type_helpers import net_worth

D = Decimal
TODAY = timezone.localdate()


class Base(E2ETestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user('insure-edge')
        self.bank = make_account(self.user, 'HDFC Savings', 'SAVINGS_ACCOUNT', 500000)

    def policy(self, kind='LIFE_INSURANCE', name='Policy', policy_type='SAVINGS'):
        from expenses.models import PhysicalAsset
        asset = PhysicalAsset.objects.create(user=self.user, name=name, asset_class='INSURANCE', currency='₹', policy_type=policy_type,
                                             acquisition_date=TODAY - timedelta(days=400), premium_amount=D('25000'),
                                             premium_frequency='ANNUAL')
        account = make_account(self.user, name, kind, 0, linked_physical_asset=asset)
        return asset, account

    def premium(self, asset, amount, days_ago=0):
        """A premium paid from the bank and linked to the policy."""
        from expenses.models import Expense
        return Expense.objects.create(user=self.user, account=self.bank, amount=D(str(amount)), base_amount=D(str(amount)), currency='₹',
                                      date=TODAY - timedelta(days=days_ago), description='Premium', category='Insurance',
                                      linked_physical_asset=asset)

    def surrender_value(self, asset, amount, days_ago=0):
        from expenses.models import AssetValuation
        return AssetValuation.objects.create(asset=asset, value=D(str(amount)), as_of_date=TODAY - timedelta(days=days_ago))


class ProtectionTests(Base):
    def test_a_term_policy_is_worth_nothing_so_its_premium_lowers_net_worth(self):
        asset, _ = self.policy(policy_type='PROTECTION')
        self.premium(asset, 12000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('488000'))

    def test_health_cover_is_worth_nothing_however_many_premiums_are_paid(self):
        asset, _ = self.policy('HEALTH_INSURANCE', 'Family floater', 'PROTECTION')
        for years_ago in range(4):
            self.premium(asset, 18000, days_ago=365 * years_ago)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000') - D('72000'))

    def test_a_value_entered_for_a_protection_policy_is_ignored(self):
        asset, _ = self.policy(policy_type='PROTECTION')
        self.surrender_value(asset, 300000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))


class SavingsPolicyTests(Base):
    def test_premiums_paid_count_as_the_policys_value_when_no_surrender_value_is_known(self):
        asset, _ = self.policy()
        self.premium(asset, 25000, days_ago=400)
        self.premium(asset, 25000, days_ago=35)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))             # 50,000 left the bank, 50,000 counted in the policy

    def test_a_surrender_value_above_zero_replaces_the_premiums_paid(self):
        asset, _ = self.policy()
        self.premium(asset, 25000, days_ago=400)
        self.premium(asset, 25000, days_ago=35)
        self.surrender_value(asset, 41000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000') - D('50000') + D('41000'))

    def test_a_surrender_value_of_zero_falls_back_to_the_premiums_paid(self):
        asset, _ = self.policy()
        self.premium(asset, 30000)
        self.surrender_value(asset, 0)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))

    def test_the_newest_surrender_value_wins(self):
        asset, _ = self.policy()
        self.premium(asset, 30000)
        self.surrender_value(asset, 45000, days_ago=1)
        self.surrender_value(asset, 20000, days_ago=200)                  # entered later, but older
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000') - D('30000') + D('45000'))

    def test_a_policy_with_nothing_paid_is_worth_nothing(self):
        self.policy()
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))

    def test_premiums_on_one_policy_do_not_count_toward_another(self):
        first, _ = self.policy(name='First')
        self.policy(name='Second')
        self.premium(first, 10000)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))             # only First holds the 10,000

    def test_the_same_premium_amounts_in_paise_add_exactly(self):
        asset, _ = self.policy()
        for _ in range(3):
            self.premium(asset, '1234.57')
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('500000'))

    def test_an_inactive_policy_account_stops_counting(self):
        asset, account = self.policy()
        self.premium(asset, 25000)
        type(account).objects.filter(pk=account.pk).update(is_active=False)
        page = self.new_page(self.user)
        self.assertEqual(net_worth(self, page), D('475000'))


class FormTests(Base):
    def fill(self, page, kind, name, **fields):
        self.open(page, reverse('account-create') + f'?type={kind}')
        page.fill('#id_name', name)
        page.click('#nextBtn')
        page.wait_for_selector('#id_asset_name', state='visible')
        page.fill('#id_asset_name', f'{name} policy')
        for field, value in fields.items():
            if field == 'policy_type':
                self.set_select(page, '#id_policy_type', value)
            else:
                page.fill(f'#id_{field}', value if isinstance(value, str) else value.isoformat())
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()

    def test_a_life_policy_is_created_with_its_details_and_counts_as_savings_by_default(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'LIFE_INSURANCE', 'LIC', policy_number='LIC-778', premium_amount='24000', sum_assured='1000000',
                  policy_start_date=TODAY - timedelta(days=200))
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='LIC').exists(), 'the policy was not saved', seconds=15)
        asset = Account.objects.get(user=self.user, name='LIC').linked_physical_asset
        self.assertEqual((asset.policy_number, asset.premium_amount, asset.sum_assured, asset.policy_type),
                         ('LIC-778', D('24000.00'), D('1000000.00'), 'SAVINGS'))

    def test_a_health_policy_chosen_from_the_type_list_is_pure_protection(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open(page, reverse('account-create'))
        page.fill('#typeSearch', 'health')
        page.press('#typeSearch', 'Enter')                       # one match: it is chosen
        page.wait_for_selector('#accountBasicsFields', state='visible')
        self.assertEqual(page.input_value('#id_account_type'), 'HEALTH_INSURANCE')
        page.fill('#id_name', 'Star Health')
        page.click('#nextBtn')
        page.wait_for_selector('#id_asset_name', state='visible')
        page.fill('#id_premium_amount', '18000')
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Star Health').exists(), 'the policy was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Star Health').linked_physical_asset.policy_type, 'PROTECTION')

    def test_a_health_policy_opened_from_a_type_link_is_pure_protection(self):
        """Opened straight to Health Insurance (?type=HEALTH_INSURANCE, as the add links do), the form starts on pure protection."""
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'HEALTH_INSURANCE', 'Star Health', premium_amount='18000')
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Star Health').exists(), 'the policy was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Star Health').linked_physical_asset.policy_type, 'PROTECTION')

    def test_a_term_policy_can_be_made_under_life_insurance(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'LIFE_INSURANCE', 'Term plan', premium_amount='9000', policy_type='PROTECTION')
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Term plan').exists(), 'the policy was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Term plan').linked_physical_asset.policy_type, 'PROTECTION')

    def test_a_new_policy_starts_worth_nothing_and_no_surrender_value_is_invented(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.fill(page, 'LIFE_INSURANCE', 'Fresh', premium_amount='24000')
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Fresh').exists(), 'the policy was not saved', seconds=15)
        asset = Account.objects.get(user=self.user, name='Fresh').linked_physical_asset
        self.assertEqual([v.value for v in asset.valuations.all()], [D('0.00')])
        self.assertEqual(net_worth(self, page), D('500000'))

    def test_a_policy_name_is_needed_or_the_account_name_is_used(self):
        from expenses.models import Account
        page = self.new_page(self.user)
        self.open(page, reverse('account-create') + '?type=LIFE_INSURANCE')
        page.fill('#id_name', 'Unnamed policy')
        page.click('#nextBtn')
        page.wait_for_selector('#id_asset_name', state='visible')
        page.locator('form button[type="submit"]:visible, form input[type="submit"]:visible').first.click()
        self.wait_until(lambda: Account.objects.filter(user=self.user, name='Unnamed policy').exists(), 'the policy was not saved', seconds=15)
        self.assertEqual(Account.objects.get(user=self.user, name='Unnamed policy').linked_physical_asset.name, 'Unnamed policy')
