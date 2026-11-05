"""Refund integrity found by the November audit: a refund never makes spending negative, whatever currency it is in,
a base-currency change keeps the net cost right, budgets and the calendar net capital-event refunds like the
analytics do, and converting an expense to a capital event keeps its refunds attached."""
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse

from expenses.budgets import monthly_spend_by_category
from expenses.calendar_data import build_month
from expenses.forms import ExpenseForm, IncomeForm
from expenses.models import CapitalEvent, Expense, Income
from expenses.views.settings import _renormalize_base_amounts

from .test_expense_refunds import Base, REFUND, today
from .test_income_capital_refunds import RefundLinkTestBase

D = Decimal


class TestRefundCaps(Base):
    def data(self, **extra):
        data = {'date': today().isoformat(), 'amount': '100', 'currency': '₹', 'account': self.account.pk,
                'source_type': REFUND, 'description': 'paid back'}
        data.update(extra)
        return data

    def test_a_refund_in_another_currency_is_compared_at_the_rate_saving_applies(self):
        e = self.expense(500)
        with patch('expenses.forms.get_exchange_rate', return_value=D('83')):
            too_much = IncomeForm(self.data(amount='100', currency='$', refund_for_expense=e.pk), user=self.user)
            self.assertFalse(too_much.is_valid())          # 100 x 83 = 8,300 against an expense of 500
            self.assertIn('amount', too_much.errors)
            fine = IncomeForm(self.data(amount='5', currency='$', refund_for_expense=e.pk), user=self.user)
            self.assertTrue(fine.is_valid(), fine.errors)  # 5 x 83 = 415

    def test_an_expense_cannot_be_edited_to_cost_less_than_what_came_back(self):
        e = self.expense(1000)
        self.refund(e, 800)
        e.refresh_from_db()
        data = {'date': today(), 'amount': '300', 'currency': '₹', 'description': e.description, 'category': e.category,
                'payment_method': 'Cash', 'account': self.account.pk}
        form = ExpenseForm(data, instance=e, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('amount', form.errors)
        data['amount'] = '900'
        self.assertTrue(ExpenseForm(data, instance=e, user=self.user).is_valid())


class TestCapitalEventRefundCap(RefundLinkTestBase):
    def data(self, **extra):
        data = {'date': today().isoformat(), 'amount': '25000', 'currency': '₹', 'account': self.account.pk,
                'source_type': REFUND, 'description': 'friends'}
        data.update(extra)
        return data

    def test_refunds_cannot_add_up_to_more_than_the_event_cost(self):
        event = self.capital(20000)
        self.income(15000, REFUND, link=event)
        over = IncomeForm(self.data(amount='6000', refund_for_capital_event=event.pk), user=self.user)
        self.assertFalse(over.is_valid())
        self.assertIn('5,000.00', over.errors['amount'][0])
        self.assertTrue(IncomeForm(self.data(amount='5000', refund_for_capital_event=event.pk), user=self.user).is_valid())


class TestBaseCurrencyChange(Base):
    def test_the_refunded_total_follows_the_new_currency(self):
        e = self.expense(8000)
        self.refund(e, 4000)
        with patch('expenses.views.settings.get_exchange_rate', return_value=D('0.012')):
            _renormalize_base_amounts(self.user, '$')
        e.refresh_from_db()
        self.assertEqual((e.base_amount, e.refunded_base), (D('96.00'), D('48.00')))
        self.assertEqual(e.net_base_amount, D('48.00'))


class TestBudgetsAndCalendarNetEventRefunds(RefundLinkTestBase):
    def capital(self, amount, excluded=True, when=None):
        event = super().capital(amount, excluded=excluded, when=when)
        CapitalEvent.objects.filter(pk=event.pk).update(exclude_from_budget=excluded)
        event.refresh_from_db()
        return event

    def test_a_budget_counted_event_comes_off_by_what_was_paid_back(self):
        event = self.capital(20000, excluded=False)
        self.income(15000, REFUND, link=event)
        spend, total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(total, D('5000'))

    def test_an_event_left_out_of_budgets_stays_out_and_so_does_its_refund(self):
        event = self.capital(20000, excluded=True)
        self.income(15000, REFUND, link=event)
        spend, total = monthly_spend_by_category(self.user, today().year, today().month)
        self.assertEqual(total, D('0'))

    def test_the_calendar_takes_the_refund_off_the_day_it_arrived(self):
        event = self.capital(20000, excluded=False)
        self.income(15000, REFUND, link=event)
        weeks = build_month(self.user, today().year, today().month, today())
        cell = next(c for w in weeks for c in w if c and c['date'] == today())
        self.assertEqual(cell['expense'], D('5000'))
        self.assertEqual(cell['income'], D('0'))


class TestConvertKeepsRefunds(Base):
    def test_refunds_follow_the_expense_into_the_capital_event(self):
        e = self.expense(8000)
        r = self.refund(e, 4000)
        cache.clear()
        response = self.client.post(reverse('expense-convert', args=[e.pk]))
        self.assertEqual(response.status_code, 302)
        r.refresh_from_db()
        event = CapitalEvent.objects.get(user=self.user)
        self.assertEqual(r.refund_for_capital_event_id, event.pk)
        self.assertIsNone(r.refund_for_expense_id)


class TestIncomeListShowsRefunds(Base):
    def test_a_refund_against_an_expense_says_so_and_is_not_counted_as_income(self):
        e = self.expense(8000, description='Goa trip')
        self.refund(e, 4000)
        self.income_row = Income.objects.create(user=self.user, date=today(), amount=D('1000'), source_type='Salary',
                                                currency='₹', account=self.account)
        response = self.client.get(reverse('income-list'), {'time_period': 'all'})
        self.assertContains(response, 'Pays back')
        self.assertContains(response, 'Goa trip')
        self.assertEqual(response.context['filtered_amount'], D('1000'))
        self.assertEqual(response.context['filtered_count'], 2)
