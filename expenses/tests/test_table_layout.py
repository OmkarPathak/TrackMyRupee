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
        block = css[css.index('Data tables (All Transactions'):]
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
