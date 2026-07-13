from datetime import date

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.forms import CustomSignupForm
from expenses.models import Expense, UserProfile


class DPDPAComplianceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='consentuser', email='consent@example.com', password='password123')
        self.client = Client()
        # By default, a newly created user profile starts with consent_granted = False
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.consent_granted = False
        self.profile.has_seen_tutorial = True  # Avoid onboarding redirect
        self.profile.save()

    def test_signup_form_consent_fields(self):
        """Test that CustomSignupForm contains the consent checkboxes and requires them."""
        form = CustomSignupForm()
        self.assertIn('consent_email', form.fields)
        self.assertIn('consent_transactions', form.fields)
        self.assertIn('consent_device', form.fields)
        
        self.assertTrue(form.fields['consent_email'].required)
        self.assertTrue(form.fields['consent_transactions'].required)
        self.assertTrue(form.fields['consent_device'].required)

    def test_signup_form_saves_consent(self):
        """Test that CustomSignupForm correctly saves consent version and timestamp on UserProfile."""
        form_data = {
            'consent_email': True,
            'consent_transactions': True,
            'consent_device': True,
        }
        form = CustomSignupForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        
        # We need a dummy request for allauth signup form save
        class DummyRequest:
            META = {}
        request = DummyRequest()
        
        form.signup(request, self.user)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.consent_granted)
        self.assertEqual(self.profile.consent_version, 'v1.0')
        self.assertIsNotNone(self.profile.consent_timestamp)

    def test_middleware_redirects_unconsented_user(self):
        """Test that a logged-in user without consent is redirected to `/consent/` when accessing app pages."""
        self.client.login(username='consentuser', password='password123')
        
        # Accessing dashboard should redirect to consent
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('dpdp_consent')))

    def test_middleware_allows_exempt_urls(self):
        """Test that a logged-in user without consent can still access exempt pages like logout or the consent view itself."""
        self.client.login(username='consentuser', password='password123')
        
        # Accessing consent view directly should be allowed (status 200)
        response = self.client.get(reverse('dpdp_consent'))
        self.assertEqual(response.status_code, 200)
        
        # Accessing public page should be allowed without redirection
        response = self.client.get(reverse('privacy-policy'))
        self.assertEqual(response.status_code, 200)

    def test_standalone_consent_view_get(self):
        """Test that accessing `/consent/` renders the consent template."""
        self.client.login(username='consentuser', password='password123')
        response = self.client.get(reverse('dpdp_consent'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'expenses/dpdp_consent.html')

    def test_standalone_consent_view_post_success(self):
        """Test that submitting `/consent/` with all toggles checks consent and redirects to dashboard."""
        self.client.login(username='consentuser', password='password123')
        response = self.client.post(reverse('dpdp_consent'), {
            'consent_email': 'on',
            'consent_transactions': 'on',
            'consent_device': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse('home')))
        
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.consent_granted)
        self.assertEqual(self.profile.consent_version, 'v1.0')
        self.assertIsNotNone(self.profile.consent_timestamp)

    def test_standalone_consent_view_post_failure(self):
        """Test that submitting `/consent/` without all toggles fails and redirects back with error."""
        self.client.login(username='consentuser', password='password123')
        response = self.client.post(reverse('dpdp_consent'), {
            'consent_email': 'on',
            # consent_transactions and consent_device missing
        })
        self.assertEqual(response.status_code, 200) # Re-renders page
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.consent_granted)

    def test_withdraw_consent_deletes_account(self):
        """Test that withdrawing consent deletes the user account, logs audit record, sends confirmation email, and redirects."""
        from django.core import mail

        from expenses.models import DeletionRequestAuditLog

        self.client.login(username='consentuser', password='password123')

        # Grant consent first
        self.profile.consent_granted = True
        self.profile.save()

        # Create some dummy data to ensure deletion cascades
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='lunch')
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)

        # Clear outbox before action
        mail.outbox = []

        response = self.client.post(reverse('withdraw-consent'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse('landing')))

        # Check that user and all data is deleted
        self.assertFalse(User.objects.filter(username='consentuser').exists())
        self.assertEqual(Expense.objects.count(), 0)

        # Verify Audit Log
        self.assertTrue(DeletionRequestAuditLog.objects.filter(email='consent@example.com', username='consentuser').exists())

        # Verify Email
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['consent@example.com'])
        self.assertEqual(email.subject, 'Account Deleted - TrackMyRupee')
        self.assertIn('Your account has been deleted. All personal data will be permanently removed within 7 days.', email.body)
        self.assertIn('retained for 5 years', email.body)
        self.assertIn('cannot be deleted on request', email.body)

    def test_account_delete_logs_audit_and_sends_email(self):
        """Test that deleting account logs audit record, sends confirmation email, and redirects."""
        from django.core import mail

        from expenses.models import DeletionRequestAuditLog

        self.client.login(username='consentuser', password='password123')

        # Clear outbox before action
        mail.outbox = []

        response = self.client.post(reverse('user-delete'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse('landing')))

        # Check that user is deleted
        self.assertFalse(User.objects.filter(username='consentuser').exists())

        # Verify Audit Log
        self.assertTrue(DeletionRequestAuditLog.objects.filter(email='consent@example.com', username='consentuser').exists())

        # Verify Email
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, ['consent@example.com'])
        self.assertEqual(email.subject, 'Account Deleted - TrackMyRupee')
        self.assertIn('Your account has been deleted. All personal data will be permanently removed within 7 days.', email.body)

    def test_settings_home_shows_dpdpa_summary(self):
        """Test that the settings home view includes DPDPA user data summary in its context and template."""
        # Create some accounts/transactions
        from expenses.models import Account, Expense
        Account.objects.create(user=self.user, name="Savings Account", account_type="BANK", balance=1000, currency="₹")
        Expense.objects.create(user=self.user, date=date.today(), amount=50, category="Food", description="chai", currency="₹")

        self.client.login(username='consentuser', password='password123')

        # Grant consent first to bypass middleware
        self.profile.consent_granted = True
        self.profile.save()

        response = self.client.get(reverse('settings-home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Your Data Summary')
        self.assertContains(response, 'Correct My Data')
        self.assertContains(response, 'Export My Data')
        self.assertEqual(response.context['num_accounts'], 1)
        self.assertEqual(response.context['num_transactions'], 1)


