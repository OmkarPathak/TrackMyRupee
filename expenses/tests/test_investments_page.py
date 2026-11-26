"""The Investments page (the old Holdings page): its tiles and allocation come from the shared investments overview, so they
match the printable summary report, and it keeps the market holdings table and the cash note."""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape
from django.utils import timezone

from expenses.models import Account, Holding, RecurringTransaction, Valuation
from expenses.portfolio_summary import build_portfolio_summary

D = Decimal


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('investor', 'i@example.com', 'pw')
        profile = self.user.profile
        profile.tier = 'PRO'
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def account(self, name, kind='SAVINGS_ACCOUNT', balance='0', **extra):
        return Account.objects.create(user=self.user, name=name, account_type=kind, balance=D(balance), currency='₹', **extra)

    def holding(self, account, name, units, cost, nav=None):
        holding = Holding.objects.create(account=account, instrument_name=name, units=D(units), avg_cost=D(cost),
                                         instrument_type='MF', currency='₹')
        if nav is not None:
            Valuation.objects.create(holding=holding, value=D(units) * D(nav), unit_nav=D(nav), as_of_date=self.today)
        return holding

    def page(self):
        return self.client.get(reverse('holding-list'))


class WithInvestments(Base):
    def setUp(self):
        super().setUp()
        self.bank = self.account('HDFC Savings', balance='100000')
        self.funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.holding(self.funds, 'Winner Fund', '10', '100', nav='120')
        self.account('Bank FD', 'FD', balance='100000', deposit_principal=D('100000'), deposit_rate=D('7'),
                     deposit_start_date=self.today - timedelta(days=100), deposit_maturity_date=self.today + timedelta(days=265))
        RecurringTransaction.objects.create(user=self.user, transaction_type='TRANSFER', amount=D('5000'), currency='₹',
                                            base_amount=D('5000'), description='SIP', frequency='MONTHLY', start_date=self.today,
                                            from_account=self.bank, to_account=self.funds)


class PageTests(WithInvestments):
    def test_the_page_is_called_investments(self):
        html = self.page().content.decode()
        self.assertIn('<h1 class="h3 fw-bold mb-1">Investments</h1>', html)
        self.assertNotIn('Investment &amp; Holdings Portfolio', html)

    def test_the_tiles_show_the_overview_figures(self):
        response = self.page()
        overview = response.context['overview']
        summary = build_portfolio_summary(self.user)
        self.assertEqual(overview['investments_value'], summary['investments_value'])
        self.assertEqual(overview['invested'], summary['invested'])
        self.assertEqual(overview['monthly_sip'], D('5000.00'))
        self.assertContains(response, 'id="investments-overview"')
        self.assertContains(response, 'data-iv="sip"')
        self.assertEqual(response.context['investment_count'], 2)       # one holding and one deposit

    def test_the_allocation_bar_is_shown_with_every_group(self):
        response = self.page()
        self.assertContains(response, 'id="investments-allocation"')
        for item in response.context['overview']['allocation']:
            self.assertContains(response, escape(item['label']))

    def test_the_print_report_button_goes_to_the_summary(self):
        self.assertContains(self.page(), f'href="{reverse("portfolio-summary")}"')

    def test_the_market_holdings_table_is_still_there(self):
        response = self.page()
        self.assertContains(response, 'Winner Fund')
        self.assertContains(response, 'Market holdings')

    def test_the_cash_note_adds_up_with_the_holdings_accounts(self):
        self.funds.balance = D('500')
        self.funds.save()
        response = self.page()
        total = sum((b['total'] for b in response.context['overview']['holding_blocks']), D('0'))
        self.assertEqual(response.context['total_with_cash'], total)


class EmptyTests(Base):
    def test_a_person_with_nothing_sees_zero_tiles_and_no_allocation(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['overview']['investments_value'], D('0.00'))
        self.assertNotContains(response, 'id="investments-allocation"')
        self.assertContains(response, 'id="investments-overview"')

    def test_the_gain_tile_waits_for_a_recorded_value(self):
        funds = self.account('Mutual Funds', 'MUTUAL_FUND')
        self.holding(funds, 'New Fund', '10', '100')        # no valuation yet
        response = self.page()
        self.assertIsNone(response.context['overview']['returns'])
        self.assertContains(response, 'Shown once a value is recorded')

    def test_another_persons_investments_are_not_counted(self):
        other = User.objects.create_user('someone-else', 'x@example.com', 'pw')
        funds = Account.objects.create(user=other, name='Theirs', account_type='MUTUAL_FUND', balance=D('0'), currency='₹')
        Holding.objects.create(account=funds, instrument_name='Their Fund', units=D('10'), avg_cost=D('100'), currency='₹')
        response = self.page()
        self.assertEqual(response.context['overview']['investments_value'], D('0.00'))
        self.assertNotContains(response, 'Their Fund')
