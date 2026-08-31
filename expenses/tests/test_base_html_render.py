from django.test import Client, TestCase
from django.contrib.auth.models import User
from expenses.models import Account, Category, Expense

class BaseHtmlRenderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='render_test_user', email='render@example.com', password='password123')
        profile = self.user.profile
        profile.consent_granted = True
        profile.has_seen_tutorial = True
        profile.save()

        self.account = Account.objects.create(user=self.user, name='Main Account', balance=5000)
        self.category = Category.objects.create(user=self.user, name='Food', category_type='expense')
        Expense.objects.create(user=self.user, amount=150, account=self.account, category=self.category, description='Groceries', date='2026-08-01')
        self.client.login(username='render_test_user', password='password123')

    def test_base_html_render_attributes(self):
        # 1. Test Dashboard Page
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Verify Bootstrap CSS non-blocking attributes & noscript fallback
        self.assertIn('media="print" onload="this.media=\'all\'"', content)
        self.assertIn('<noscript>', content)
        self.assertIn('bootstrap@5.3.3', content)

        # Verify defer on chart.js and htmx.org
        self.assertIn('<script defer src="https://cdn.jsdelivr.net/npm/chart.js"></script>', content)
        self.assertIn('<script defer src="https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js"></script>', content)

        # Verify htmx.config wrapped in DOMContentLoaded listener
        self.assertIn("document.addEventListener('DOMContentLoaded', function ()", content)
        self.assertIn("htmx.config.defaultFocus = false;", content)

    def test_account_detail_render(self):
        # 2. Test Account Detail Page
        response = self.client.get(f'/account/{self.account.id}/')
        self.assertEqual(response.status_code, 200)

    def test_htmx_partial_swap_render(self):
        # 3. Test HTMX partial swap request
        response = self.client.get('/expenses/', HTTP_HX_REQUEST='true', HTTP_HX_TARGET='expense-list-shell')
        self.assertEqual(response.status_code, 200)
