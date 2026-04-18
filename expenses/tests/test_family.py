from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from expenses.models import (
    Account,
    Expense,
    Family,
    FamilyInvitation,
    UserProfile,
)
from finance_tracker.plans import PLAN_DETAILS


class FamilyFeatureTest(TestCase):
    def setUp(self):
        # Create Family Owner (Admin)
        self.owner = User.objects.create_user(username="owner", password="password")
        self.owner_profile, _ = UserProfile.objects.get_or_create(user=self.owner)
        self.owner_profile.tier = "PRO"
        self.owner_profile.has_seen_tutorial = True
        self.owner_profile.save()
        
        self.family = Family.objects.create(name="Test Family", owner=self.owner)
        self.owner_profile.family = self.family
        self.owner_profile.save()

        # Create Family Member
        self.member = User.objects.create_user(username="member", password="password")
        self.member_profile, _ = UserProfile.objects.get_or_create(user=self.member)
        self.member_profile.family = self.family
        self.member_profile.tier = "FREE"
        self.member_profile.has_seen_tutorial = True
        self.member_profile.save()

        self.client_owner = Client()
        self.client_owner.login(username="owner", password="password")

        self.client_member = Client()
        self.client_member.login(username="member", password="password")

    def test_privacy_and_sharing(self):
        """Test that private data is hidden and shared data is visible in Family mode."""
        # Member creates a private expense
        private_expense = Expense.objects.create(
            user=self.member, date=date.today(), amount=Decimal("100.00"),
            description="Member Private Expense", category="Personal",
            is_shared=False, family=self.family
        )
        
        # Member creates a shared expense
        shared_expense = Expense.objects.create(
            user=self.member, date=date.today(), amount=Decimal("50.00"),
            description="Member Shared Expense", category="Family",
            is_shared=True, family=self.family
        )

        # Owner views Dashboard in Personal mode
        response = self.client_owner.get(reverse('home') + "?view_mode=personal&force_full_report=1")
        self.assertEqual(len(response.context['recent_activity']), 0)

        # Owner views Dashboard in Family mode
        response = self.client_owner.get(reverse('home') + "?view_mode=family&force_full_report=1")
        # Should see shared expense but NOT private one
        activity = response.context['recent_activity']
        self.assertEqual(len(activity), 1)
        self.assertEqual(activity[0].description, "Member Shared Expense")
        
        # Verify aggregation
        self.assertEqual(float(response.context['total_expenses']), 50.00)

    def test_account_privacy(self):
        """Admin should not see a member's private account balances/details."""
        private_acc = Account.objects.create(
            user=self.member, name="Member Private Acc", balance=1000,
            is_shared=False, family=self.family
        )
        shared_acc = Account.objects.create(
            user=self.member, name="Member Shared Acc", balance=500,
            is_shared=True, family=self.family
        )

        # Owner views Dashboard in Family mode
        response = self.client_owner.get(reverse('home') + "?view_mode=family")
        accounts = response.context['accounts']
        
        # Should only see the shared account
        account_names = [a.name for a in accounts]
        self.assertIn("Member Shared Acc", account_names)
        self.assertNotIn("Member Private Acc", account_names)

    def test_plan_limits_plus_tier(self):
        """PLUS tier should be limited to 1 family member."""
        self.owner_profile.tier = "PLUS"
        self.owner_profile.subscription_end_date = timezone.now() + timedelta(days=30)
        self.owner_profile.save()

        # Already has 1 member (self.member). Trying to invite/add another should fail.
        self.assertFalse(self.owner_profile.can_add_family_member())
        
        # Try to send an invite
        response = self.client_owner.post(reverse('family-settings'), {
            'action': 'invite', 'email': 'guest2@example.com'
        })
        # Should show error or redirect to pricing if limit hit
        # (Assuming the view redirects to pricing or shows message)
        self.assertEqual(FamilyInvitation.objects.filter(family=self.family).count(), 0)

    def test_family_owner_change_view_mode(self):
        """Test that the view mode persists or updates correctly via GET."""
        response = self.client_owner.get(reverse('home') + "?view_mode=family")
        self.assertEqual(response.context['view_mode'], 'family')
        
        response = self.client_owner.get(reverse('home') + "?view_mode=personal")
        self.assertEqual(response.context['view_mode'], 'personal')

    def test_data_isolation_between_families(self):
        """Data from Family A should never be visible to Family B members."""
        owner2 = User.objects.create_user(username="owner2", password="password")
        owner2_profile, _ = UserProfile.objects.get_or_create(user=owner2)
        owner2_profile.has_seen_tutorial = True
        family2 = Family.objects.create(name="Family 2", owner=owner2)
        owner2_profile.family = family2
        owner2_profile.save()
        
        # Shared expense in Family 1
        Expense.objects.create(
            user=self.member, date=date.today(), amount=Decimal("20.00"),
            description="Shared in Family 1", is_shared=True, family=self.family
        )

        client2 = Client()
        client2.login(username="owner2", password="password")
        
        # Owner 2 views Family view
        response = client2.get(reverse('home') + "?view_mode=family&force_full_report=1")
        self.assertEqual(len(response.context['recent_activity']), 0)
