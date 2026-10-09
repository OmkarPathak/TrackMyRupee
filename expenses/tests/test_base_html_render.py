from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Category, Expense, UserProfile


class BaseHtmlRenderTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='render_test_user', email='render@example.com', password='password123')
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True, consent_granted=True)

        self.account = Account.objects.create(user=self.user, name='Main Account', balance=5000)
        self.category, _ = Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(user=self.user, amount=150, account=self.account, category='Food', description='Groceries', date='2026-08-01')
        self.client.login(username='render_test_user', password='password123')

    def test_base_html_render_attributes(self):
        # 1. Test Dashboard Page
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Verify Bootstrap CSS link presence (self-hosted; the JS bundle still comes from the CDN)
        self.assertIn('vendor/bootstrap/bootstrap.min', content)
        self.assertIn('vendor/bootstrap/bootstrap.bundle.min', content)

        # Verify defer on the pinned, self-hosted chart.js and on htmx.org
        self.assertRegex(content, r'<script defer src="/static/vendor/chartjs/chart-4\.4\.7\.umd[^"]*\.js"></script>')
        self.assertRegex(content, r'<script defer src="/static/vendor/htmx/htmx-2\.0\.4\.min[^"]*\.js"></script>')

        # Verify htmx.config wrapped in DOMContentLoaded listener
        self.assertIn("document.addEventListener('DOMContentLoaded', function ()", content)
        self.assertIn("htmx.config.defaultFocus = false;", content)

    def test_account_detail_render(self):
        # 2. Test Account Detail Page
        response = self.client.get(reverse('account-detail', kwargs={'pk': self.account.pk}))
        self.assertEqual(response.status_code, 200)

    def test_htmx_partial_swap_render(self):
        # 3. Test HTMX partial swap request
        response = self.client.get(reverse('expense-list'), HTTP_HX_REQUEST='true', HTTP_HX_TARGET='expense-list-shell')
        self.assertEqual(response.status_code, 200)

    def test_critical_assets_are_self_hosted(self):
        """First paint must not depend on a third-party CSS/font origin."""
        content = self.client.get(reverse('home')).content.decode('utf-8')
        self.assertNotIn('fonts.googleapis.com', content)
        self.assertNotIn('fonts.gstatic.com', content)
        self.assertNotIn('cdn.jsdelivr.net', content)
        self.assertNotIn('bootstrap@5.3.3/dist/css', content)
        self.assertNotIn('bootstrap-icons@1.11.3/font/bootstrap-icons.min.css', content)
        self.assertIn('vendor/fonts', content)
        self.assertIn('vendor/bootstrap-icons/bootstrap-icons.min', content)

    def test_entrance_animations_never_start_invisible(self):
        """opacity:0 elements are not LCP candidates, so .fade-in / .nw-animate-in must stay transform-only."""
        import re
        content = self.client.get(reverse('home')).content.decode('utf-8')
        for selector in ('.fade-in', '.nw-animate-in'):
            for rule in re.findall(re.escape(selector) + r'\s*\{([^}]*)\}', content):
                self.assertNotRegex(rule, r'opacity\s*:\s*0\s*;', selector)

    def test_tour_assets_only_load_when_tour_will_start(self):
        UserProfile.objects.filter(user=self.user).update(has_seen_tutorial=True)
        content = self.client.get(reverse('home')).content.decode('utf-8')
        self.assertNotIn('driver.js', content)
        self.assertNotIn('tutorial.js', content)

        content = self.client.get(reverse('home'), {'tour': 'true'}).content.decode('utf-8')
        self.assertIn('driver.js', content)
        self.assertIn('tutorial.js', content)
