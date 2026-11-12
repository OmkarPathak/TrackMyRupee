"""A brand-new person: create an account, finish the welcome set-up, land on a working app."""
from decimal import Decimal

from django.contrib.auth.models import User
from django.urls import reverse

from e2e.base import E2ETestCase


class SignUpTests(E2ETestCase):
    def credentials(self, page, name, email=None):
        """Step 1 of the sign-up form: who you are."""
        self.open(page, '/accounts/signup/')
        page.fill('input[name="email"]', email or f'{name}@example.com')
        page.fill('input[name="username"]', name)
        page.fill('input[name="password1"]', 'Sup3r-secret-pw!')
        page.fill('input[name="password2"]', 'Sup3r-secret-pw!')
        page.click('#btn_continue')
        page.wait_for_selector('#consent_section', state='visible')   # step 2: the consent

    def test_signing_up_creates_the_account_records_consent_and_starts_the_welcome(self):
        from expenses.models import ConsentEvent
        page = self.new_page()
        self.credentials(page, 'fresh-face')
        page.check('#id_confirm_age')
        page.click('#btn_submit_consent')
        page.wait_for_url(lambda url: '/accounts/signup' not in url)
        user = User.objects.get(username='fresh-face')
        self.assertTrue(user.profile.consent_granted)
        self.assertTrue(ConsentEvent.objects.filter(user=user, action='GRANTED').exists())
        self.assertIn('/onboarding', page.url)                    # a new person starts at the welcome

    def test_the_button_stays_off_until_the_age_is_confirmed(self):
        page = self.new_page()
        self.credentials(page, 'no-consent')
        self.assertTrue(page.locator('#btn_submit_consent').is_disabled())
        page.check('#id_confirm_age')
        self.assertFalse(page.locator('#btn_submit_consent').is_disabled())
        page.uncheck('#id_confirm_age')
        self.assertTrue(page.locator('#btn_submit_consent').is_disabled())
        self.assertFalse(User.objects.filter(username='no-consent').exists())

    def test_a_username_that_is_taken_is_refused(self):
        User.objects.create_user('taken', 'taken@example.com', 'pass12345!')
        page = self.new_page()
        self.credentials(page, 'taken', email='other@example.com')
        page.check('#id_confirm_age')
        page.click('#btn_submit_consent')
        page.wait_for_selector('text=already')
        self.assertEqual(User.objects.filter(username='taken').count(), 1)


class WelcomeSetUpTests(E2ETestCase):
    def test_finishing_the_welcome_creates_the_account_salary_and_first_expense(self):
        from e2e.base import make_user
        from expenses.models import Account, Expense, Income, UserProfile
        user = make_user('welcome')
        UserProfile.objects.filter(user=user).update(has_seen_tutorial=False)
        user = User.objects.get(pk=user.pk)
        page = self.new_page(user)
        self.open(page, reverse('onboarding'))
        page.click('#primaryBtn')                                  # 1. how do you earn: the default is fine
        page.wait_for_selector('#step2Panel', state='visible')
        page.fill('#salaryInput', '60000')                         # 2. salary, where it lands, the balance
        page.click('[data-bank="HDFC"]')
        page.fill('#balanceInput', '35000')
        page.click('#primaryBtn')
        page.wait_for_selector('#step3Panel', state='visible')
        page.fill('#quickAddInput', 'swiggy dinner 450')           # 3. the first expense
        page.click('#quickAddGoBtn')
        page.wait_for_selector('#understoodCard', state='visible')
        page.click('#primaryBtn')
        page.wait_for_selector('#finalPanel', state='visible')
        self.assertTrue(Account.objects.filter(user=user, name__icontains='HDFC').exists())
        self.assertTrue(Expense.objects.filter(user=user, amount=Decimal('450')).exists())
        self.assertTrue(Income.objects.filter(user=user, amount=Decimal('60000')).exists())
