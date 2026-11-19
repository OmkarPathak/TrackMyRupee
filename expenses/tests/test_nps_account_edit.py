"""Editing an NPS account that has no rate or start date, and the details step coming into view on a refused save."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, UserProfile


class NPSAccountEditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('nps-edit-user', password='pass')
        profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        profile.tier = 'PRO'
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()
        self.client.force_login(self.user)
        self.account = Account.objects.create(user=self.user, name='My NPS', account_type='NPS', balance=100000, currency='₹')

    def edit(self, **extra):
        data = {'name': 'My NPS', 'account_type': 'NPS', 'currency': '₹', 'balance': '150000',
                'deposit_compounding': 'QUARTERLY'}
        data.update(extra)
        return self.client.post(reverse('account-edit', args=[self.account.pk]), data)

    def test_the_amount_can_be_changed_without_a_rate_or_start_date(self):
        response = self.edit()
        self.assertRedirects(response, reverse('account-list'), fetch_redirect_response=False)
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, 150000)

    def test_a_rate_needs_a_start_date_and_the_other_way_round(self):
        response = self.edit(deposit_rate='9')
        self.assertEqual(response.status_code, 200)
        self.assertIn('deposit_start_date', response.context['form'].errors)
        response = self.edit(deposit_start_date='2024-04-01')
        self.assertEqual(response.status_code, 200)
        self.assertIn('deposit_rate', response.context['form'].errors)

    def test_a_rate_with_a_start_date_is_accepted(self):
        response = self.edit(deposit_rate='9', deposit_start_date='2024-04-01')
        self.assertEqual(response.status_code, 302)

    def test_other_deposits_still_need_a_rate_and_start_date(self):
        fd = Account.objects.create(user=self.user, name='My FD', account_type='PPF', balance=1000, currency='₹')
        response = self.client.post(reverse('account-edit', args=[fd.pk]), {
            'name': 'My FD', 'account_type': 'PPF', 'currency': '₹', 'balance': '2000', 'deposit_compounding': 'ANNUAL'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('deposit_rate', response.context['form'].errors)
        self.assertIn('deposit_start_date', response.context['form'].errors)

    def test_a_mistake_on_the_details_step_opens_that_step(self):
        response = self.edit(deposit_rate='9')
        html = response.content.decode()
        self.assertIn("document.querySelector('#step2Content .text-danger')", html)
        self.assertIn('Enter the start date, or clear the rate.', html)
