from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from expenses.forms import (
    CapitalEventForm,
    ContactForm,
    HoldingForm,
    LoanRepaymentForm,
    RecurringTransactionForm,
    SavingsGoalForm,
)
from expenses.models import Account, Category, Loan, SavingsGoal

User = get_user_model()


def make_user(name='f1'):
    return User.objects.create_user(name, f'{name}@example.com', 'pw12345!x')


class RecurringCategoryTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=1000)
        Category.objects.get_or_create(user=self.user, name='Food')

    def data(self, **kw):
        d = {'transaction_type': 'EXPENSE', 'amount': '10', 'currency': self.user.profile.currency,
             'account': self.account.pk, 'category': 'Food', 'frequency': 'MONTHLY',
             'description': 'Rent', 'start_date': timezone.localdate().isoformat(), 'is_active': 'on', 'payment_method': 'Cash'}
        d.update(kw)
        return d

    def test_own_category_accepted(self):
        form = RecurringTransactionForm(self.data(), user=self.user)
        self.assertTrue(form.is_valid(), form.errors)

    def test_made_up_category_rejected(self):
        form = RecurringTransactionForm(self.data(category='Invented'), user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('category', form.errors)

    def test_other_users_category_rejected(self):
        other = make_user('f2')
        Category.objects.get_or_create(user=other, name='SecretCat')
        form = RecurringTransactionForm(self.data(category='SecretCat'), user=self.user)
        self.assertIn('category', form.errors)


class SavingsGoalColorTest(TestCase):
    def test_color_must_be_a_listed_theme(self):
        user = make_user()
        base = {'name': 'Trip', 'target_amount': '100', 'currency': user.profile.currency, 'icon': '✈', 'color': 'primary'}
        self.assertTrue(SavingsGoalForm(base, user=user).is_valid())
        form = SavingsGoalForm({**base, 'color': 'x" onmouseover="alert(1)'}, user=user)
        self.assertFalse(form.is_valid())
        self.assertIn('color', form.errors)


class LoanRepaymentPortionTest(TestCase):
    def setUp(self):
        self.user = make_user()
        self.account = Account.objects.create(user=self.user, name='Bank', account_type='BANK', balance=100000)
        self.loan = Loan.objects.create(user=self.user, name='L', loan_type='PERSONAL', initial_principal=Decimal('10000'),
                                        duration_months=12, start_date=timezone.localdate() - timedelta(days=30),
                                        currency=self.user.profile.currency)

    def form(self, **kw):
        d = {'from_account': self.account.pk, 'amount': '100', 'date': timezone.localdate().isoformat()}
        d.update(kw)
        return LoanRepaymentForm(d, user=self.user, loan=self.loan)

    def test_principal_larger_than_amount_is_rejected(self):
        form = self.form(principal_portion='150')
        self.assertFalse(form.is_valid())
        self.assertIn('principal_portion', form.errors)

    def test_interest_larger_than_amount_is_rejected(self):
        form = self.form(interest_portion='150')
        self.assertIn('interest_portion', form.errors)

    def test_consistent_split_still_valid(self):
        form = self.form(principal_portion='80', interest_portion='20')
        self.assertTrue(form.is_valid(), form.errors)


class CapitalEventLockedAccountTest(TestCase):
    def test_locked_account_not_selectable(self):
        from finance_tracker.plans import get_limit
        user = make_user()
        limit = get_limit(user.profile.active_tier, 'accounts')
        if limit == -1:
            self.skipTest('unlimited accounts on this tier')
        accounts = [Account.objects.create(user=user, name=f'A{i}', account_type='BANK', balance=0)
                    for i in range(limit + 1)]
        # an opening 'Cash' account may already exist, so compute the locked one from the form itself
        qs_ids = set(CapitalEventForm(user=user).fields['account'].queryset.values_list('id', flat=True))
        all_ids = set(Account.objects.filter(user=user, is_active=True).values_list('id', flat=True))
        self.assertEqual(len(qs_ids), limit)
        self.assertTrue(all_ids - qs_ids)
        self.assertTrue(accounts)


class HoldingFormTest(TestCase):
    base = {'instrument_name': 'F', 'instrument_type': 'MUTUAL_FUND', 'units': '1', 'avg_cost': '10', 'currency': '₹'}

    def test_negative_values_rejected(self):
        self.assertIn('units', HoldingForm({**self.base, 'units': '-1'}).errors)
        self.assertIn('avg_cost', HoldingForm({**self.base, 'avg_cost': '-5'}).errors)

    def test_isin_validated_and_normalised(self):
        self.assertIn('isin', HoldingForm({**self.base, 'isin': 'abc'}).errors)
        form = HoldingForm({**self.base, 'isin': ' inf179k01vq6 '})
        form.is_valid()
        self.assertNotIn('isin', form.errors)
        self.assertEqual(form.cleaned_data['isin'], 'INF179K01VQ6')

    def test_blank_isin_is_none(self):
        form = HoldingForm({**self.base, 'isin': ''})
        form.is_valid()
        self.assertIsNone(form.cleaned_data['isin'])


@override_settings(RECAPTCHA_PUBLIC_KEY='', RECAPTCHA_PRIVATE_KEY='', CONTACT_EMAIL='inbox@example.com', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ContactDeliveryTest(TestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('contact')
        self.data = {'name': 'Asha\nBcc: x@y.z', 'email': 'asha@example.org', 'website': '',
                     'subject': 'Hello\r\nBcc: evil@example.com', 'message': 'Please help me with my account.'}

    def test_message_is_actually_emailed(self):
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['inbox@example.com'])
        self.assertEqual(msg.reply_to, ['asha@example.org'])
        self.assertIn('Please help me', msg.body)

    def test_header_injection_is_neutralised(self):
        self.client.post(self.url, self.data)
        msg = mail.outbox[0]
        self.assertNotIn('\n', msg.subject)
        self.assertEqual(msg.bcc, [])

    def test_honeypot_sends_nothing(self):
        self.client.post(self.url, {**self.data, 'website': 'spam.example'})
        self.assertEqual(len(mail.outbox), 0)

    def test_delivery_failure_is_reported_not_hidden(self):
        from unittest.mock import patch
        with patch('django.core.mail.EmailMessage.send', side_effect=OSError('smtp down')):
            response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "couldn")

    def test_overlong_message_rejected(self):
        form = ContactForm({**self.data, 'message': 'x' * 5001})
        self.assertIn('message', form.errors)
