import re
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import Account, Expense, Income, UserProfile

LONG_TEXT = (
    "Accrued interest earned on deposit with an unusually long description that used to be cut "
    "off at a fixed number of characters even when plenty of room was free"
)


class TableLayoutTests(TestCase):
    """Tables use fixed column widths (CSS ellipsis) instead of cutting text server-side, so the
    full text must reach the page, in a title attribute, for the hover tooltip."""

    def setUp(self):
        self.user = User.objects.create_user('tbl_user', 'tbl@example.com', 'pw')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True, has_seen_tutorial=True)
        self.account = Account.objects.create(user=self.user, name='Main Account', balance=Decimal('1000'))
        today = timezone.localdate()
        Expense.objects.create(
            user=self.user, amount=Decimal('250.00'), account=self.account, category='Food',
            description=LONG_TEXT, date=today,
        )
        Income.objects.create(
            user=self.user, date=today, amount=Decimal('1500.00'), source_type='Salary',
            source='Employer', description=LONG_TEXT, account=self.account,
        )
        self.client.force_login(self.user)

    def _html(self, name):
        resp = self.client.get(reverse(name))
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_tables_use_shared_layout_with_colgroup(self):
        for name in ('all-transactions', 'expense-list', 'income-list'):
            html = self._html(name)
            self.assertIn('tmr-table', html, name)
            table = html[html.index('tmr-table'):]
            self.assertIn('<colgroup>', table[:600], name)

    def test_long_descriptions_are_not_cut_server_side_and_carry_a_title(self):
        for name in ('all-transactions', 'expense-list', 'income-list'):
            html = self._html(name)
            self.assertIn(f'title="{LONG_TEXT}"', html, name)
            self.assertNotIn(LONG_TEXT[:30] + '…', html, name)

    def test_amounts_use_true_minus_and_tabular_class(self):
        html = self._html('expense-list')
        row = re.search(r'<td class="[^"]*tmr-amount[^"]*">\s*(.*?)</td>', html, re.S).group(1)
        self.assertTrue(row.strip().startswith('&minus;'), row)

    def test_stylesheet_defines_nowrap_cells_and_header_typography(self):
        css = open('static/style.css', encoding='utf-8').read()
        block = css[css.index('Data tables (every list in the app)'):]
        self.assertIn('white-space: nowrap', block)          # badges/cells never wrap onto two lines
        self.assertIn('table-layout: fixed', block)
        self.assertIn('font-size: 14px !important', block)    # headers
        self.assertIn('font-size: 15px !important', block)    # body
        self.assertIn('tabular-nums', block)

    def test_income_table_has_account_column(self):
        html = self._html('income-list')
        headers = re.findall(r'<th[^>]*>\s*(?:<[^>]+>\s*)*([A-Za-z ]+?)\s*(?:<|$)', html[html.index('tmr-table'):html.index('</thead>', html.index('tmr-table'))])
        self.assertIn('Account', headers)
        self.assertIn('title="Main Account">Main Account</td>', html)

    def test_income_mobile_card_shows_account_name(self):
        html = self._html('income-list')
        card = html[html.index('id="mobileGridView"'):]
        self.assertIn('Employer &middot; Main Account', card)


class EveryListTableTests(TestCase):
    """Every data table in the app opts into the shared layout (tmr-table + colgroup)."""

    def setUp(self):
        from datetime import timedelta

        from expenses.models import (
            CapitalEvent,
            GoalContribution,
            Holding,
            Loan,
            LoanRepayment,
            Notification,
            PaymentHistory,
            SavingsGoal,
            Transfer,
        )
        self.user = User.objects.create_user('every_tbl', 'every@example.com', 'pw')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True, has_seen_tutorial=True, tier='PRO')
        self.user.refresh_from_db()
        today = timezone.localdate()
        self.a = Account.objects.create(user=self.user, name='Alpha', balance=Decimal('100000'))
        self.b = Account.objects.create(user=self.user, name='Beta', balance=Decimal('0'), account_type='MUTUAL_FUND')
        Transfer.objects.create(user=self.user, from_account=self.a, to_account=self.b, amount=Decimal('10'), date=today)
        CapitalEvent.objects.create(user=self.user, account=self.a, amount=Decimal('900'), date=today,
                                    subtype='large_purchase', currency='₹', note='Car')
        self.loan = Loan.objects.create(user=self.user, name='Car loan', loan_type='AUTO',
                                        initial_principal=Decimal('100000'), duration_months=12,
                                        start_date=today - timedelta(days=60), currency='₹')
        LoanRepayment.objects.create(loan=self.loan, from_account=self.a, amount=Decimal('9000'),
                                     principal_portion=Decimal('8000'), interest_portion=Decimal('1000'), date=today)
        self.goal = SavingsGoal.objects.create(user=self.user, name='Trip', target_amount=Decimal('5000'), currency='₹')
        GoalContribution.objects.create(goal=self.goal, account=self.a, amount=Decimal('100'), date=today)
        Holding.objects.create(account=self.b, instrument_name='Fund', units=Decimal('1'), avg_cost=Decimal('10'), currency='₹')
        Notification.objects.create(user=self.user, title='Hi', message='There')
        PaymentHistory.objects.create(user=self.user, order_id='order_1', amount=Decimal('99'), tier='PLUS',
                                      duration='monthly', status='SUCCESS')
        self.client.force_login(self.user)

    def assertShared(self, html, label):
        self.assertIn('tmr-table', html, label)
        table = html[html.index('tmr-table'):]
        self.assertIn('<colgroup>', table[:700], label)

    def test_each_page_uses_the_shared_table(self):
        pages = {
            'transfers': reverse('transfer-list'),
            'capital events': reverse('capital-event-list') + '?time_period=all',
            'categories': reverse('category-list'),
            'goal detail': reverse('goal-detail', args=[self.goal.pk]),
            'holdings': reverse('holding-list'),
            'payment history': reverse('payment-history'),
            'notifications': reverse('notification-list'),
            'loan history': reverse('loan-tab-history', args=[self.loan.pk]),
            'loan schedule': reverse('loan-tab-schedule', args=[self.loan.pk]),
        }
        for label, url in pages.items():
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200, label)
            self.assertShared(response.content.decode(), label)

    def test_the_old_ad_hoc_table_classes_are_gone_from_those_pages(self):
        html = self.client.get(reverse('transfer-list')).content.decode()
        self.assertNotIn('badge bg-success bg-opacity-10 text-success border small', html)
        html = self.client.get(reverse('goal-detail', args=[self.goal.pk])).content.decode()
        self.assertIn('type-badge type-savings', html)

    def test_the_wrap_modifier_exists_for_rich_cells(self):
        css = open('static/style.css', encoding='utf-8').read()
        self.assertIn('.tmr-table td.tmr-wrap', css)


class InfoHintTests(TestCase):
    """The "how it works" info buttons are one quiet shared style, not a ringed button."""

    def setUp(self):
        self.user = User.objects.create_user('hint_user', 'hint@example.com', 'pw')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True, has_seen_tutorial=True, tier='PRO')
        self.client.force_login(self.user)

    def test_pages_use_the_shared_info_hint(self):
        for name in ('transfer-list', 'budget', 'recurring-list'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertIn('class="info-hint"', html, name)
            self.assertNotIn('bi-info-lg', html, name)

    def test_loan_templates_use_it_too(self):
        for path in ('templates/expenses/partials/_loan_list_partial.html', 'templates/expenses/loan_detail.html'):
            text = open(path, encoding='utf-8').read()
            self.assertIn('class="info-hint"', text, path)
            self.assertNotIn('bi-info-lg', text, path)

    def test_the_hint_has_no_ring_in_the_stylesheet(self):
        css = open('static/style.css', encoding='utf-8').read()
        block = css[css.index('.info-hint {'):]
        block = block[:block.index('}')]
        self.assertIn('border: 0', block)
        self.assertIn('background: transparent', block)


class SidebarRestoreTests(TestCase):
    """Clicking a sidebar link far down the list must not flash the sidebar at the top and then jump."""

    def setUp(self):
        self.user = User.objects.create_user('sb_user', 'sb@example.com', 'pw')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True, has_seen_tutorial=True)
        self.client.force_login(self.user)

    def test_scroll_and_collapsed_state_are_restored_before_first_paint(self):
        html = self.client.get(reverse('account-list')).content.decode()
        aside_end = html.index('</aside>')
        inline = html[aside_end:aside_end + 1500]
        self.assertIn("sessionStorage.getItem('sidebarScrollTop')", inline)
        self.assertIn("localStorage.getItem('sidebarState')", inline)
        self.assertIn('scrollRestored', inline)

    def test_the_deferred_script_does_not_reapply_a_stale_position(self):
        js = open('static/js/sidebar.js', encoding='utf-8').read()
        self.assertIn('scrollInitDone', js)
        self.assertIn('firstInit', js)
        self.assertIn('scrollRestored', js)


class LoaderFeedbackTests(TestCase):
    """Taps must always give feedback, even before the deferred skeleton script has loaded."""

    def setUp(self):
        self.user = User.objects.create_user('ld_user', 'ld@example.com', 'pw')
        UserProfile.objects.filter(user=self.user).update(consent_granted=True, has_seen_tutorial=True)
        self.client.force_login(self.user)
        self.html = self.client.get(reverse('account-list')).content.decode()

    def test_there_is_a_fallback_when_the_skeleton_script_is_not_loaded_yet(self):
        self.assertIn('tmr-fallback-loader', self.html)
        self.assertIn("typeof window.showSkeletonLoader === 'function'", self.html)
        css = open('static/skeleton-loader.css', encoding='utf-8').read()
        self.assertIn('#tmr-fallback-loader', css)

    def test_a_touch_pointerdown_does_not_start_the_loader(self):
        self.assertIn("e.pointerType === 'touch'", self.html)

    def test_the_progress_bar_restarts_on_every_tap(self):
        self.assertIn('const restartBar', self.html)
        self.assertNotIn("bar.style.width = '30%'", self.html)

    def test_htmx_updates_dim_the_content_and_always_clear_it(self):
        self.assertIn("classList.add('tmr-busy')", self.html)
        for event in ('htmx:afterSettle', 'htmx:responseError', 'htmx:sendError', 'htmx:timeout', 'htmx:swapError'):
            self.assertIn(f"'{event}', clearHtmxLoader", self.html)
        css = open('static/skeleton-loader.css', encoding='utf-8').read()
        self.assertIn('.tmr-busy', css)

    def test_data_href_elements_start_the_loader_too(self):
        self.assertIn('[data-href]', self.html)
