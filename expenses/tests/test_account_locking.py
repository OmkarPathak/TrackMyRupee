from datetime import date, timedelta
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from expenses.models import Account, UserProfile, Expense
from finance_tracker.plans import PLAN_DETAILS
from expenses.forms import ExpenseForm

class AccountLockingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="locker", password="password")
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.tier = "FREE"
        self.profile.save()
        
        self.client = Client()
        self.client.login(username="locker", password="password")
        
        # Create accounts. Limit for FREE is 2.
        # Account 1: Oldest
        self.acc1 = Account.objects.create(user=self.user, name="Oldest", balance=100)
        # Account 2: Second Oldest
        self.acc2 = Account.objects.create(user=self.user, name="Second", balance=200)
        # Account 3: Extra (will be locked)
        self.acc3 = Account.objects.create(user=self.user, name="Extra", balance=300)

    def test_is_account_locked_logic(self):
        """Verify the model logic for identifying locked accounts."""
        self.assertFalse(self.profile.is_account_locked(self.acc1))
        self.assertFalse(self.profile.is_account_locked(self.acc2))
        self.assertTrue(self.profile.is_account_locked(self.acc3))
        
        # Upgrade to PRO (unlimited)
        self.profile.tier = "PRO"
        self.profile.subscription_end_date = timezone.now() + timedelta(days=30)
        self.profile.save()
        self.assertFalse(self.profile.is_account_locked(self.acc3))

    def test_account_detail_locked(self):
        """Locked accounts should not be viewable."""
        # acc3 is locked for FREE
        response = self.client.get(reverse('account-detail', args=[self.acc3.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('pricing'), response.url)
        
        # acc1 is NOT locked
        response = self.client.get(reverse('account-detail', args=[self.acc1.pk]))
        self.assertEqual(response.status_code, 200)

    def test_account_update_locked(self):
        """Locked accounts should not be editable."""
        response = self.client.get(reverse('account-edit', args=[self.acc3.pk]))
        self.assertEqual(response.status_code, 302)
        
        response = self.client.post(reverse('account-edit', args=[self.acc3.pk]), {'name': 'New Name'})
        self.assertEqual(response.status_code, 302)
        self.acc3.refresh_from_db()
        self.assertEqual(self.acc3.name, "Extra")

    def test_account_delete_locked(self):
        """Locked accounts should not be deletable."""
        response = self.client.post(reverse('account-delete', args=[self.acc3.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Account.objects.filter(pk=self.acc3.pk).exists())

    def test_form_filtering_locked_accounts(self):
        """Forms should not show locked accounts in dropdowns."""
        form = ExpenseForm(user=self.user)
        queryset = form.fields['account'].queryset
        self.assertIn(self.acc1, queryset)
        self.assertIn(self.acc2, queryset)
        self.assertNotIn(self.acc3, queryset)

    def test_account_list_annotation(self):
        """Account list should mark accounts as locked."""
        response = self.client.get(reverse('account-list'))
        accounts = response.context['accounts']
        
        # Find accounts in the context and check is_locked attribute
        acc1_ctx = next(a for a in accounts if a.pk == self.acc1.pk)
        acc2_ctx = next(a for a in accounts if a.pk == self.acc2.pk)
        acc3_ctx = next(a for a in accounts if a.pk == self.acc3.pk)
        
        self.assertFalse(acc1_ctx.is_locked)
        self.assertFalse(acc2_ctx.is_locked)
        self.assertTrue(acc3_ctx.is_locked)
