"""The demo user (setup_demo_user) shows what TrackMyRupee can do, including the newer features: family sharing and an
emergency sheet, insurance, refunds, SIP-bought funds with a sale, versioned budgets, a dollar account, a car loan,
net worth history. Seeded twice here, because the demo reseeds itself and must be able to."""
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

import expenses.models as m
from expenses import demo_seed_extras as extras


@override_settings(FAMILY_ENABLED=True)
class DemoSeedTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.out = StringIO()
        call_command('setup_demo_user', stdout=cls.out)
        cls.user = User.objects.get(username='demo')

    def test_every_extra_section_loaded(self):
        self.assertNotIn('FAILED', self.out.getvalue())
        self.assertTrue(extras.marker_present(self.user))

    def test_family_with_shares_and_an_emergency_sheet_someone_asked_for(self):
        household = m.Household.objects.get(owner=self.user)
        self.assertEqual(household.members.count(), 3)
        shares = m.FamilyShare.objects.filter(household=household)
        self.assertGreaterEqual(shares.count(), 12)
        self.assertEqual({s.level for s in shares}, {'AMOUNTS', 'LIST'})
        self.assertTrue(shares.filter(alias='Our home').exists())
        sheet = m.EmergencySheet.objects.get(owner=self.user)
        self.assertEqual(sheet.requests.filter(status='PENDING').count(), 1)
        self.assertEqual(household.invitations.filter(accepted_at__isnull=True).count(), 1)

    def test_the_family_history_has_a_start_in_the_past(self):
        from django.utils import timezone
        earliest = min(m.FamilyShare.objects.filter(household__owner=self.user).values_list('amounts_since', flat=True))
        self.assertLess((timezone.now() - earliest).days, 200)
        self.assertGreater((timezone.now() - earliest).days, 100)

    def test_insurance_policies_have_premiums_and_a_next_due_date(self):
        policies = m.PhysicalAsset.objects.filter(user=self.user, asset_class='INSURANCE')
        self.assertEqual({p.policy_type for p in policies}, {'PROTECTION', 'SAVINGS'})
        for policy in policies:
            self.assertTrue(m.Expense.objects.filter(user=self.user, linked_physical_asset=policy).exists(), policy.name)
        self.assertTrue(m.RecurringTransaction.objects.filter(user=self.user, category='Insurance').exists())

    def test_refunds_against_an_expense_and_a_capital_event(self):
        incomes = m.Income.objects.filter(user=self.user, source_type='Refund / Reimbursement')
        self.assertTrue(incomes.filter(refund_for_expense__isnull=False).exists())
        self.assertTrue(incomes.filter(refund_for_capital_event__isnull=False).exists())
        trip = m.Expense.objects.get(user=self.user, description__startswith='Goa trip')
        trip.refresh_from_db()
        self.assertEqual(trip.refunded_base, 16000)          # three friends paid back their share

    def test_funds_are_bought_through_sips_and_one_stock_was_part_sold(self):
        txns = m.HoldingTransaction.objects.filter(holding__account__user=self.user)
        self.assertTrue(txns.filter(kind='BUY', transfer__isnull=False).exists())          # a SIP instalment
        self.assertTrue(txns.filter(kind='SELL').exists())
        split = m.RecurringFundSplit.objects.filter(recurring__user=self.user)
        self.assertEqual(split.count(), 2)
        accounts = set(m.Holding.objects.filter(account__user=self.user).values_list('account__account_type', flat=True))
        self.assertEqual(accounts, {'MUTUAL_FUND', 'DEMAT'})

    def test_the_year_has_a_spread_of_capital_events(self):
        events = m.CapitalEvent.objects.filter(user=self.user)
        self.assertGreaterEqual(events.count(), 12)
        self.assertGreaterEqual(len({e.subtype for e in events}), 6)

    def test_holdings_are_backed_by_recorded_cash_so_the_page_shows_no_warning(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse('holding-list'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'more than the money recorded')

    def test_budgets_have_history_and_a_month_with_its_own_limit(self):
        self.assertGreaterEqual(m.BudgetLimitVersion.objects.filter(user=self.user).count(), 6)
        self.assertTrue(m.BudgetMonthOverride.objects.filter(user=self.user).exists())

    def test_a_dollar_account_a_car_and_the_indian_deposit_accounts(self):
        types = set(m.Account.objects.filter(user=self.user).values_list('account_type', flat=True))
        self.assertTrue({'RD', 'PPF', 'EPF', 'NPS', 'VEHICLE', 'VEHICLE_LOAN', 'MUTUAL_FUND', 'DEMAT'} <= types)
        usd = m.Account.objects.get(user=self.user, currency='$')
        self.assertTrue(m.Expense.objects.filter(user=self.user, account=usd, currency='$').exists())

    def test_net_worth_has_a_year_of_month_end_history_for_the_family_too(self):
        self.assertEqual(m.NetWorthSnapshot.objects.filter(user=self.user).count(), 12)
        self.assertEqual(m.NetWorthSnapshot.objects.filter(user__username='demo_arjun').count(), 12)

    def test_the_pages_that_show_it_all_render(self):
        self.client.force_login(self.user)
        for name in ('home', 'account-list', 'income-list', 'capital-event-list', 'loan-list', 'budget', 'analytics',
                     'analytics-networth', 'recurring-list', 'holding-list', 'flow-landing', 'family-home', 'family-share',
                     'family-preview', 'family-networth', 'family-inventory', 'family-emergency', 'notification-list'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_reseeding_replaces_everything_without_duplicates(self):
        out = StringIO()
        call_command('setup_demo_user', stdout=out)
        self.assertNotIn('FAILED', out.getvalue())
        self.assertEqual(m.Household.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(m.EmergencySheet.objects.filter(owner=self.user).count(), 1)
        self.assertEqual(User.objects.filter(username__in=extras.FAMILY_USERNAMES).count(), 2)
        self.assertEqual(m.Notification.objects.filter(user=self.user, slug=extras.MARKER_SLUG).count(), 1)
