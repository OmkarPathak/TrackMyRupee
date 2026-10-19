"""All Transactions can be narrowed to categories, so a chart on the Analytics page can open "every transaction
behind this bar". Only expenses and capital events have a category; everything else drops out of the list."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, CapitalEvent, Expense, Income, Transfer

D = Decimal
TODAY = date(2026, 3, 10)


class CategoryFilterOnAllTransactionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('cf', password='pw-cf-12345')
        profile = self.user.profile
        profile.has_seen_tutorial = True
        profile.consent_granted = True
        profile.save()
        self.client.login(username='cf', password='pw-cf-12345')
        self.bank = Account.objects.create(user=self.user, name='Bank', account_type='SAVINGS_ACCOUNT', balance=D('100000'))
        self.other = Account.objects.create(user=self.user, name='Other', account_type='SAVINGS_ACCOUNT', balance=D('1000'))
        Expense.objects.create(user=self.user, date=TODAY, amount=D('500'), category='Dining out', description='lunch', account=self.bank)
        Expense.objects.create(user=self.user, date=TODAY, amount=D('900'), category='Shopping', description='shirt', account=self.bank)
        Income.objects.create(user=self.user, date=TODAY, amount=D('5000'), source='Pay', account=self.bank)
        Transfer.objects.create(user=self.user, date=TODAY, amount=D('100'), from_account=self.bank, to_account=self.other)
        CapitalEvent.objects.create(user=self.user, date=TODAY, amount=D('20000'), category='Dining out', account=self.bank)

    def _rows(self, **params):
        query = {'time_period': 'custom', 'start_date': '2026-03-01', 'end_date': '2026-03-31', **params}
        response = self.client.get(reverse('all-transactions'), query)
        self.assertEqual(response.status_code, 200)
        return response, [(t['type'], t['cat']) for t in response.context['transactions']]

    def test_without_a_category_every_kind_of_row_is_listed(self):
        _, rows = self._rows()
        self.assertEqual({r[0] for r in rows}, {'EXPENSE', 'INCOME', 'TRANSFER', 'CAPITAL_EVENT'})

    def test_a_category_keeps_its_expenses_and_capital_events_only(self):
        _, rows = self._rows(category='Dining out')
        self.assertEqual(sorted(r[0] for r in rows), ['CAPITAL_EVENT', 'EXPENSE'])

    def test_several_categories_can_be_chosen(self):
        _, rows = self._rows(category=['Dining out', 'Shopping'])
        self.assertEqual(len(rows), 3)

    def test_it_combines_with_the_account_filter(self):
        _, rows = self._rows(category='Dining out', account=str(self.other.pk))
        self.assertEqual(rows, [])

    def test_it_shows_as_a_chip_in_the_toolbar_state(self):
        response, _ = self._rows(category='Shopping')
        self.assertEqual(response.context['applied_state']['filters']['category'], ['Shopping'])

    def test_the_category_filter_is_offered_in_the_toolbar(self):
        response, _ = self._rows()
        self.assertIn('category', [f.key for f in response.context['filter_config'].filters])
