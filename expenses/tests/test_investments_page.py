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

    def test_each_group_is_listed_with_its_amount_and_share(self):
        response = self.page()
        self.assertContains(response, 'iv-breakdown')
        self.assertContains(response, 'class="iv-br-amt"', count=len(response.context['overview']['allocation']))

    def test_the_print_report_button_goes_to_the_summary(self):
        self.assertContains(self.page(), f'href="{reverse("portfolio-summary")}"')

    def test_the_market_holdings_table_is_still_there(self):
        response = self.page()
        self.assertContains(response, 'Winner Fund')
        self.assertContains(response, 'id="iv-pane-market"')

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


class TabsTests(WithInvestments):
    def setUp(self):
        super().setUp()
        self.account('PPF', 'PPF', balance='50000')
        self.account('Post Office RD', 'RD', balance='20000', deposit_rate=D('6.7'),
                     deposit_maturity_date=self.today + timedelta(days=30))

    def test_the_tab_bar_has_market_deposits_and_schemes(self):
        html = self.page().content.decode()
        for tab in ('iv-tab-market', 'iv-tab-deposits', 'iv-tab-schemes', 'iv-pane-market', 'iv-pane-deposits', 'iv-pane-schemes'):
            self.assertIn(f'id="{tab}"', html)

    def test_the_deposits_tab_lists_fixed_and_recurring_deposits_with_links(self):
        response = self.page()
        self.assertContains(response, 'id="iv-deposits-table"')
        self.assertContains(response, 'Bank FD')
        self.assertContains(response, 'Post Office RD')
        fd = Account.objects.get(user=self.user, name='Bank FD')
        self.assertContains(response, reverse('account-detail', args=[fd.uuid]))
        self.assertContains(response, '7%')

    def test_the_schemes_tab_lists_schemes_not_deposits(self):
        response = self.page()
        html = response.content.decode()
        schemes = html[html.index('id="iv-schemes-table"'):]
        self.assertIn('PPF', schemes.split('</table>')[0])
        self.assertNotIn('Bank FD', schemes.split('</table>')[0])

    def test_the_tabs_sit_above_the_panes_like_the_other_pages(self):
        html = self.page().content.decode()
        self.assertIn('class="tmr-tabs" id="investmentTabs"', html)
        self.assertLess(html.index('id="investmentTabs"'), html.index('id="iv-pane-market"'))
        self.assertNotIn('iv-count', html)

    def test_phones_get_compact_summary_tiles(self):
        response = self.page()
        self.assertContains(response, 'id="investments-overview-phone"')
        self.assertContains(response, 'tmr-stat-card')
        for key in ('value', 'invested', 'gain', 'sip'):
            self.assertContains(response, f'data-ivm="{key}"')

    def test_coming_up_lists_maturities_with_the_soonest_first(self):
        response = self.page()
        self.assertContains(response, 'id="investments-upcoming"')
        html = response.content.decode()
        block = html[html.index('id="investments-upcoming"'):]
        self.assertLess(block.index('Post Office RD'), block.index('Bank FD'))
        self.assertIn('Matures', block)

    def test_the_phone_cards_are_there_too(self):
        response = self.page()
        self.assertContains(response, 'id="iv-deposits-cards"')
        self.assertContains(response, 'id="iv-schemes-cards"')


class EmptyTabsTests(Base):
    def test_empty_deposits_and_schemes_invite_adding_one(self):
        response = self.page()
        self.assertContains(response, 'id="iv-deposits-empty"')
        self.assertContains(response, 'id="iv-schemes-empty"')
        self.assertContains(response, reverse('account-create'))

    def test_no_coming_up_card_without_maturities(self):
        self.assertNotContains(self.page(), 'id="investments-upcoming"')

    def test_the_allocation_takes_the_full_width_without_coming_up(self):
        self.account('HDFC Savings', balance='1000')
        html = self.page().content.decode()
        card = html[:html.index('id="investments-allocation"')]
        self.assertNotIn('col-lg-8', card.split('class="row g-3 mb-4"')[-1])
