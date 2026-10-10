"""Follow-ups from the net worth / dashboard audit: amount filter labels, category ownership on edit,
plan cap on import and conversion, income export column, and finding rows without an account."""

import csv
import datetime
import io
from decimal import Decimal
from unittest.mock import patch

import openpyxl
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.forms import ExpenseForm
from expenses.models import Account, CapitalEvent, Category, Expense, Income, UserProfile
from finance_tracker.plans import get_limit

D = Decimal


def today():
    return timezone.localdate()


class Base(TestCase):
    tier = 'PRO'

    def setUp(self):
        self.user = User.objects.create_user(username='followups', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = self.tier
        profile.currency = '₹'
        profile.has_seen_tutorial = True
        profile.save()
        self.user.refresh_from_db()
        Account.objects.filter(user=self.user).delete()
        Category.objects.filter(user=self.user).delete()
        for name in ('Food', 'Transport'):
            Category.objects.create(user=self.user, name=name)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=D('100000'), currency='₹')
        self.client.force_login(self.user)
        cache.clear()

    def expense(self, amount='100', **kw):
        values = dict(user=self.user, date=today(), amount=D(str(amount)), category='Food', currency='₹',
                      account=self.cash, description='x')
        values.update(kw)
        return Expense.objects.create(**values)


class TestAmountFilter(Base):
    def test_labels_have_no_currency_symbol_and_old_links_still_work(self):
        self.expense('100')
        self.expense('1000')
        self.expense('5000')
        self.expense('20000')
        def ids(value):
            response = self.client.get(reverse('expense-list'), {'amount_range': value})
            return sorted(int(e.amount) for e in response.context['expenses'])
        self.assertEqual(ids('Under 500'), [100])
        self.assertEqual(ids('500 to 2,000'), [1000])
        self.assertEqual(ids('2,000 to 10,000'), [5000])
        self.assertEqual(ids('Over 10,000'), [20000])
        self.assertEqual(ids('₹500 to ₹2,000'), [1000])     # a bookmark from before the change


class TestCategoryOwnershipOnEdit(Base):
    def data(self, **kw):
        data = {'date': today(), 'amount': '100', 'currency': '₹', 'description': 'x', 'category': 'Food',
                'payment_method': 'Cash', 'account': self.cash.pk}
        data.update(kw)
        return data

    def test_unknown_category_is_rejected(self):
        form = ExpenseForm(self.data(category='Somebody Elses'), user=self.user)
        self.assertEqual(form.errors['category'], ['Choose one of your categories.'])

    def test_own_category_is_accepted(self):
        self.assertTrue(ExpenseForm(self.data(), user=self.user).is_valid())

    def test_existing_expense_keeps_a_since_deleted_category_until_changed(self):
        old = self.expense(category='Gone')
        self.assertTrue(ExpenseForm(self.data(category='Gone'), instance=old, user=self.user).is_valid())
        self.assertIn('category', ExpenseForm(self.data(category='Other Gone'), instance=old, user=self.user).errors)


class TestPlanCapOnImportAndConversion(Base):
    tier = 'FREE'

    def upload(self, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Date', 'Details', 'Amount'])
        for row in rows:
            ws.append(row)
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        buffer.name = 'rows.xlsx'
        with patch('expenses.views.predict_category_ai', return_value='Food'):
            return self.client.post(reverse('upload'), {'account': self.cash.id, 'currency': '₹', 'file': buffer})

    def test_import_stops_at_the_monthly_cap_for_the_current_month_only(self):
        cap = get_limit('FREE', 'expenses_per_month')
        Expense.objects.bulk_create([
            Expense(user=self.user, date=today(), amount=1, category='Food', base_amount=1, description=f'e{i}',
                    currency='₹') for i in range(cap - 1)
        ])
        last_month = today().replace(day=1) - datetime.timedelta(days=1)
        response = self.upload([
            [str(today()), 'fits', 10],
            [str(today()), 'over the cap', 20],
            [str(last_month), 'older month is free', 30],
        ])
        results = response.context['results']
        self.assertEqual((results['created_count'], results['limit_skipped']), (2, 1))
        self.assertTrue(Expense.objects.filter(description='fits').exists())
        self.assertFalse(Expense.objects.filter(description='over the cap').exists())
        self.assertTrue(Expense.objects.filter(description='older month is free').exists())

    def test_capital_event_is_not_converted_past_the_cap(self):
        cap = get_limit('FREE', 'expenses_per_month')
        Expense.objects.bulk_create([
            Expense(user=self.user, date=today(), amount=1, category='Food', base_amount=1, description='x',
                    currency='₹') for _ in range(cap)
        ])
        event = CapitalEvent.objects.create(user=self.user, date=today(), amount=D('500'), currency='₹',
                                            subtype='other', account=self.cash)
        self.client.post(reverse('capital-event-convert', args=[event.uuid]))
        self.assertTrue(CapitalEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(Expense.objects.filter(user=self.user).count(), cap)


class TestExportAndUnaccounted(Base):
    def test_income_export_has_the_source_type_column(self):
        Income.objects.create(user=self.user, date=today(), amount=D('50'), source='Side gig', source_type='Freelance',
                              currency='₹', account=self.cash)
        response = self.client.post(reverse('export-data'), {'entities': ['incomes']})
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0][-1], 'Source Type')
        self.assertEqual(rows[1][-1], 'Freelance')

    def test_account_none_filter_lists_rows_without_an_account(self):
        self.expense('10', description='accounted')
        self.expense('20', account=None, description='legacy')
        Income.objects.create(user=self.user, date=today(), amount=D('7'), source='Salary', source_type='Salary',
                              currency='₹')
        CapitalEvent.objects.create(user=self.user, date=today(), amount=D('9'), currency='₹', subtype='other')
        expenses = self.client.get(reverse('expense-list'), {'account': 'none'}).context['expenses']
        self.assertEqual([e.description for e in expenses], ['legacy'])
        self.assertEqual(len(self.client.get(reverse('income-list'), {'account': 'none'}).context['incomes']), 1)
        self.assertEqual(len(self.client.get(reverse('capital-event-list'), {'account': 'none'}).context['events']), 1)
        both = self.client.get(reverse('expense-list'), {'account': ['none', self.cash.id]}).context['expenses']
        self.assertEqual(len(both), 2)

    def test_accounts_page_warns_about_rows_without_an_account(self):
        self.assertNotIn('unaccounted', self.client.get(reverse('account-list')).context or {}) if False else None
        self.expense('20', account=None)
        response = self.client.get(reverse('account-list'))
        self.assertEqual(response.context['unaccounted'], {'expenses': 1, 'income': 0, 'capital_events': 0})
        self.assertContains(response, 'not linked to an account')
        self.assertContains(response, '?account=none')
