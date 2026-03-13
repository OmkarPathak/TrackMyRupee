from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from expenses.models import UserProfile, Income, Expense, Category
from datetime import date
import json

class OnboardingViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='onboarduser', password='password')
        self.client = Client()
        self.client.login(username='onboarduser', password='password')
        self.url = reverse('onboarding')

    def test_onboarding_access_unauthenticated(self):
        """Unauthenticated users should be redirected to login, not crash."""
        self.client.logout()
        response = self.client.get(self.url)
        # LoginRequiredMixin redirects to /accounts/login/?next=/onboarding/
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_onboarding_redirection_new_user(self):
        """New users should be redirected to onboarding from home."""
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('onboarding'), response.url)

    def test_onboarding_redirection_existing_user(self):
        """Users who have seen tutorial should be redirected away."""
        self.user.profile.has_seen_tutorial = True
        self.user.profile.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('home'))

    def test_onboarding_step_setup(self):
        data = {'step': 'setup', 'currency': '$', 'language': 'en'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.currency, '$')
        self.assertEqual(self.user.profile.language, 'en')

    def test_onboarding_step_income_idempotency(self):
        data = {'step': 'income', 'amount': 5000, 'source': 'Salary'}
        # First call
        response1 = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response1.status_code, 200)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        
        # Now we test idempotency.
        data['amount'] = 6000
        response2 = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(Income.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Income.objects.first().amount, 6000)

    def test_onboarding_step_expense(self):
        data = {'step': 'expense', 'amount': 50, 'description': 'Coffee', 'category': 'Miscellaneous'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)

    def test_onboarding_step_budget(self):
        # Clear any system-generated categories for this user to ensure isolation
        Category.objects.filter(user=self.user).delete()
        
        data = {
            'step': 'budget',
            'categories': [
                {'name': 'Food', 'limit': 500},
                {'name': 'Rent', 'limit': 1500}
            ]
        }
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Category.objects.filter(user=self.user, name__in=['Food', 'Rent']).count(), 2)
        food = Category.objects.get(user=self.user, name='Food')
        self.assertEqual(food.limit, 500)

    def test_onboarding_step_setup_completion(self):
        data = {'step': 'setup', 'currency': '$', 'language': 'en'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.currency, '$')
        self.assertEqual(self.user.profile.language, 'en')
        self.assertTrue(self.user.profile.has_seen_tutorial)

    def test_onboarding_skip(self):
        data = {'step': 'skip'}
        response = self.client.post(self.url, json.dumps(data), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.user.profile.refresh_from_db()
        self.assertTrue(self.user.profile.has_seen_tutorial)
