from datetime import date
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from expenses.models import UserProfile, Expense
from expenses.forms import CustomSignupForm


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
            pass
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
        """Test that withdrawing consent deletes the user account and redirects to landing page."""
        self.client.login(username='consentuser', password='password123')
        
        # Grant consent first
        self.profile.consent_granted = True
        self.profile.save()
        
        # Create some dummy data to ensure deletion cascades
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='lunch')
        self.assertEqual(Expense.objects.filter(user=self.user).count(), 1)
        
        response = self.client.post(reverse('withdraw-consent'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith(reverse('landing')))
        
        # Check that user and all data is deleted
        self.assertFalse(User.objects.filter(username='consentuser').exists())
        self.assertEqual(Expense.objects.count(), 0)
