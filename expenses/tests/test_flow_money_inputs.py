"""Amount fields in the Guided Setup forms are grouped as the person types (25,00,000) like the app's other forms.

They use the app's MoneyInput, which static/js/money_input.js turns into a grouped field that posts the plain number.
The percentages, day numbers and month counts stay plain number fields."""
from django import forms
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.flows.registry import FlowRegistry
from expenses.forms import MoneyInput
from expenses.models import Account, UserProfile

NOT_AN_AMOUNT = ('rate', 'percent', 'ratio', 'share')


def decimal_fields(form_class):
    for name, field in form_class().fields.items():
        if isinstance(field, forms.DecimalField):
            yield name, field


class EveryFlowTests(TestCase):
    def test_every_amount_field_of_every_setup_is_a_money_input(self):
        wrong = []
        for key, flow in FlowRegistry.all().items():
            for name, field in decimal_fields(flow.form_class):
                if any(word in name for word in NOT_AN_AMOUNT):
                    continue
                if not isinstance(field.widget, MoneyInput):
                    wrong.append(f'{key}.{name}')
        self.assertEqual(wrong, [], 'these amount fields are plain number inputs: ' + ', '.join(wrong))

    def test_rates_stay_plain_number_fields(self):
        for key, flow in FlowRegistry.all().items():
            for name, field in decimal_fields(flow.form_class):
                if 'rate' in name:
                    self.assertNotIsInstance(field.widget, MoneyInput, f'{key}.{name}')

    def test_day_and_month_fields_stay_plain_number_fields(self):
        for flow in FlowRegistry.all().values():
            for name, field in flow.form_class().fields.items():
                if type(field) is forms.IntegerField:           # (DecimalField is a subclass of it)
                    self.assertNotIsInstance(field.widget, MoneyInput, name)


class PostedWithCommasTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('commas', password='pw')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)
        self.cash = Account.objects.create(user=self.user, name='Cash', account_type='CASH_WALLET',
                                           balance=3000000, currency='₹')
        self.client.force_login(self.user)

    def fd_form(self, **extra):
        data = {'name': 'FD', 'principal': '25,00,000', 'annual_rate': '7.1', 'deposit_start_date': '2026-10-01',
                'maturity_date': '2029-10-01', 'deposit_compounding': 'QUARTERLY', 'from_account': self.cash.pk}
        data.update(extra)
        return FlowRegistry.get('fd').form_class(data=data, user=self.user)

    def test_an_amount_typed_with_commas_is_accepted_as_the_plain_number(self):
        form = self.fd_form()
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertEqual(form.cleaned_data['principal'], 2500000)

    def test_the_payout_amount_accepts_commas_too(self):
        form = self.fd_form(interest_payout='MONTHLY', payout_amount='14,791.67')
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertEqual(str(form.cleaned_data['payout_amount']), '14791.67')

    def test_a_plain_number_still_works(self):
        self.assertTrue(self.fd_form(principal='2500000').is_valid())

    def test_the_setup_page_marks_the_amount_fields_for_grouping(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        principal = html[html.index('name="principal"') - 200:html.index('name="principal"') + 200]
        self.assertIn('data-money', principal)
        rate = html[html.index('name="annual_rate"') - 200:html.index('name="annual_rate"') + 200]
        self.assertNotIn('data-money', rate)

    def test_the_page_loads_the_script_that_groups_them(self):
        html = self.client.get(reverse('flow-detail', kwargs={'key': 'fd'})).content.decode()
        self.assertIn('js/money_input.js', html)
