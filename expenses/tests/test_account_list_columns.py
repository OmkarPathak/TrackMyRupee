"""The Accounts list shows extra columns only for the group in view: policy number for insurance, rate and
maturity for fixed income, limit / use / next bill for cards. The tiles switch the view in the browser by setting
data-view-type on the list's shell, so the page carries each group's cells and the CSS shows the right ones."""
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, PhysicalAsset


class ListColumnsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('cols', 'cols@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.save()
        self.client.force_login(self.user)
        Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=Decimal('5000'))

    def page(self, **params):
        response = self.client.get(reverse('account-list'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_a_plain_list_has_no_extra_columns(self):
        response = self.page()
        for flag in ('show_policy_column', 'show_fixed_columns', 'show_credit_columns'):
            self.assertFalse(response.context[flag], flag)
        self.assertNotContains(response, 'Policy number')
        self.assertNotContains(response, '<span class="acx-x acx-x-rate">')        # the stylesheet names them; the cells are absent
        self.assertNotContains(response, '<span class="acx-x acx-x-limit">')

    def test_fixed_income_gets_rate_and_maturity(self):
        Account.objects.create(user=self.user, name='My FD', account_type='FD', balance=Decimal('100000'),
                               deposit_rate=Decimal('7.5'), deposit_maturity_date=date.today() + timedelta(days=200))
        response = self.page()
        self.assertTrue(response.context['show_fixed_columns'])
        self.assertFalse(response.context['show_credit_columns'])
        self.assertContains(response, '<span class="acx-x acx-x-rate">')
        self.assertContains(response, '<span class="acx-x acx-x-maturity">')
        self.assertContains(response, '7.5%')

    def test_cards_get_limit_use_and_next_bill(self):
        Account.objects.create(user=self.user, name='My Card', account_type='CREDIT_CARD', balance=Decimal('-20000'),
                               credit_limit=Decimal('100000'))
        response = self.page()
        self.assertTrue(response.context['show_credit_columns'])
        for cls in ('acx-x-limit', 'acx-x-used', 'acx-x-bill'):
            self.assertContains(response, f'<span class="acx-x {cls}">')

    def test_insurance_gets_the_policy_column_even_before_a_policy_number_is_entered(self):
        Account.objects.create(user=self.user, name='Term plan', account_type='LIFE_INSURANCE', balance=Decimal('0'))
        response = self.page()
        self.assertTrue(response.context['show_policy_column'])
        self.assertContains(response, 'Policy number')

    def test_a_policy_number_is_shown_in_its_own_cell(self):
        asset = PhysicalAsset.objects.create(user=self.user, name='LIC', asset_class='INSURANCE', policy_number='POL-1234')
        Account.objects.create(user=self.user, name='LIC', account_type='LIFE_INSURANCE', balance=Decimal('0'),
                               linked_physical_asset=asset)
        self.assertContains(self.page(), '<div class="acx-x acx-x-policy">POL-1234</div>', html=False)

    def test_each_column_is_shown_only_for_its_group(self):
        css = self.page().content.decode()
        self.assertIn('.acx-x { display: none; }', css)
        self.assertIn('.acx[data-view-type="INSURANCE"] .acx-x-policy { display: block; }', css)
        self.assertIn('.acx[data-view-type="FIXED_INCOME"] .acx-x-rate', css)
        self.assertIn('.acx[data-view-type="SHORT_TERM_CREDIT"] .acx-x-limit', css)
        # nothing shows the policy cell for the other groups
        self.assertNotIn('data-view-type="CASH___BANK"] .acx-x-policy', css)

    def test_the_view_type_follows_the_chosen_group(self):
        Account.objects.create(user=self.user, name='My FD', account_type='FD', balance=Decimal('1'))
        self.assertContains(self.page(type='FIXED_INCOME'), 'data-view-type="FIXED_INCOME"')


class TabCountsTests(TestCase):
    """Pinned / Needs update count what a click on them shows in the group and search being looked at."""

    def setUp(self):
        from django.utils import timezone

        cache.clear()
        self.user = User.objects.create_user('tabs', 'tabs@example.com', 'pw')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.tier = 'PRO'            # a plan that does not lock any of these accounts (locked ones never need an update)
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        old = timezone.now() - timedelta(days=90)
        self.fd1 = Account.objects.create(user=self.user, name='Alpha FD', account_type='FD', balance=Decimal('100'))
        self.fd2 = Account.objects.create(user=self.user, name='Beta FD', account_type='FD', balance=Decimal('100'))
        self.mf = Account.objects.create(user=self.user, name='Gamma Flat', account_type='REAL_ESTATE', balance=Decimal('100'))
        Account.objects.filter(pk__in=[self.fd1.pk, self.mf.pk]).update(updated_at=old)
        Account.objects.filter(pk=self.mf.pk).update(is_pinned=True)

    def counts(self, **params):
        response = self.client.get(reverse('account-list'), params)
        self.assertEqual(response.status_code, 200)
        return response.context['tab_counts']

    def test_everything_counts_when_no_group_is_picked(self):
        self.assertEqual(self.counts(), {'all': 3, 'pinned': 1, 'needs_update': 2})

    def test_counts_follow_the_group(self):
        self.assertEqual(self.counts(type='FIXED_INCOME'), {'all': 2, 'pinned': 0, 'needs_update': 1})
        self.assertEqual(self.counts(type='PHYSICAL_ASSETS'), {'all': 1, 'pinned': 1, 'needs_update': 1})

    def test_counts_follow_the_search(self):
        self.assertEqual(self.counts(search='alpha'), {'all': 1, 'pinned': 0, 'needs_update': 1})
        self.assertEqual(self.counts(type='FIXED_INCOME', search='beta'), {'all': 1, 'pinned': 0, 'needs_update': 0})

    def test_the_page_script_keeps_the_counts_in_step(self):
        html = self.client.get(reverse('account-list')).content.decode()
        self.assertIn('pinnedCount', html)
        self.assertIn("tab.dataset.acxTab === 'needs_update'", html)
