from django.test import TestCase
from django.urls import reverse

class AuthThemeTest(TestCase):
    def test_login_page_theme(self):
        response = self.client.get(reverse('account_login'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify html element has data-bs-theme="light"
        self.assertIn('data-bs-theme="light"', content)
        # Verify JS forces light theme
        self.assertIn("document.documentElement.setAttribute('data-bs-theme', 'light');", content)

    def test_signup_page_theme(self):
        response = self.client.get(reverse('account_signup'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        
        # Verify html element has data-bs-theme="light"
        self.assertIn('data-bs-theme="light"', content)
        # Verify JS forces light theme
        self.assertIn("document.documentElement.setAttribute('data-bs-theme', 'light');", content)
