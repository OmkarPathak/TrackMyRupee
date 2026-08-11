import io
from datetime import date, timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase

from expenses.models import Expense


class SecurityReportsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', email='test@example.com', password='password')
        self.user.profile.is_verified = True
        self.user.profile.save()
        
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)
        
        today = date.today()
        prev_month_date = (today.replace(day=1) - timedelta(days=15)).replace(day=15)

        # Log a massive expense to ensure this becomes the 'top category'
        Expense.objects.create(
            user=self.user,
            amount=50000,
            date=prev_month_date, # Will be captured by month calculation
            category='<script>alert("XSS")</script>',
            description='Malicious Expense'
        )

    def test_monthly_report_xss_escaping(self):
        from django.core import mail
        
        # Run the command to send the email (which goes to outbox in tests)
        out = io.StringIO()
        call_command('send_monthly_report', '--user-id', str(self.user.id), stdout=out)
        
        # Verify an email was sent (the outbox might have welcome emails too, so get the last one)
        self.assertGreaterEqual(len(mail.outbox), 1)
        email = mail.outbox[-1]
        
        # Verify the malicious payload was HTML escaped in the output
        html_content = getattr(email, 'alternatives', [(email.body,)])[0][0]
        
        self.assertNotIn('<script>alert("XSS")</script>', html_content, "XSS payload was found unescaped in the report output!")
        self.assertIn('&lt;script&gt;alert(&quot;XSS&quot;)&lt;/script&gt;', html_content, "XSS payload was correctly escaped.")
