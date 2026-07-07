from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import Notification


class SecurityRedirectsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password')
        self.client.login(username='testuser', password='password')

    def test_notification_open_redirect(self):
        # Create a notification with an external link (Open Redirect attempt)
        notification = Notification.objects.create(
            user=self.user,
            title='Test',
            message='Test',
            link='https://evil.com/phishing'
        )

        response = self.client.get(reverse('notification-redirect', kwargs={'pk': notification.pk}))
        
        # Should redirect to the safe fallback 'notification-list' instead of evil.com
        self.assertRedirects(response, reverse('notification-list'), target_status_code=200)

    def test_notification_safe_redirect(self):
        # Valid internal redirect
        notification = Notification.objects.create(
            user=self.user,
            title='Test',
            message='Test',
            link='/expenses/'
        )
        response = self.client.get(reverse('notification-redirect', kwargs={'pk': notification.pk}))
        
        # Should allow redirect to internal paths
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/expenses/')

    def test_income_create_open_redirect(self):
        # Attempt to supply a malicious next parameter during GET
        response = self.client.get(reverse('income-create') + '?next=https://evil.com/phishing')
        
        # Ensure the view renders properly with context (it shouldn't crash, but the context next_url might be passed)
        self.assertEqual(response.status_code, 200)
        
        # Now submit the form (POST) with malicious next
        post_data = {
            'amount': 100,
            'source': 'Test',
            'date': '2026-07-01'
        }
        response = self.client.post(reverse('income-create') + '?next=https://evil.com/phishing', data=post_data)
        
        # Should ignore evil.com. If form is valid, it redirects (302). 
        # If invalid, it returns 200 but renders the page, still not redirecting to evil.com.
        self.assertFalse(
            response.status_code == 302 and response.url.startswith('https://evil.com'), 
            "Open redirect successful!"
        )
