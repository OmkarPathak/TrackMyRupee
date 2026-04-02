from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from expenses.models import Notification, RecurringTransaction, UserProfile


class SendNotificationsCommandTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password', email='test@example.com')
        
        # Ensure UserProfile exists (handle signal creation if any)
        if not hasattr(self.user, 'profile'):
             UserProfile.objects.create(user=self.user)
        self.profile = self.user.profile

        # Create a transaction due in 3 days
        self.due_date = timezone.now().date() + timedelta(days=3)
        # We need a start date such that next_due_date logic lands on due_date.
        # Simplest is if start_date IS due_date (and last_processed is None)
        
        self.rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='Test Recurring',
            frequency='MONTHLY',
            start_date=self.due_date,
            is_active=True
        )
        
        # Clear outbox (which contains the welcome email from user creation in setUp)
        mail.outbox.clear()

    def test_notification_creation(self):
        """Test that a notification is created for due transaction"""
        call_command('send_notifications')
        
        # Check Notification DB
        self.assertTrue(Notification.objects.filter(
            user=self.user, 
            related_transaction=self.rt,
            title__contains='Upcoming Expense'
        ).exists())

    def test_email_sending_requires_plus(self):
        """Test that email is NOT sent for free tier users"""
        self.profile.tier = 'FREE'
        self.profile.save()
        
        call_command('send_notifications')
        
        self.assertEqual(len(mail.outbox), 0)

    def test_email_sending_for_plus_user(self):
        """Test that email IS sent for Plus/Pro users"""
        self.profile.tier = 'PLUS'
        # Ensure valid subscription
        self.profile.subscription_end_date = timezone.now() + timedelta(days=30)
        self.profile.save()
        
        call_command('send_notifications')
        
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Upcoming Expense', mail.outbox[0].subject)
        # Plain text body contains consolidated alert text
        self.assertIn('Upcoming Expense: Test Recurring', mail.outbox[0].body)

    def test_duplicate_notification_prevention(self):
        """Test that we don't send duplicate notifications on the same day"""
        # Run once
        call_command('send_notifications')
        count_first = Notification.objects.count()
        
        # Run again immediately
        call_command('send_notifications')
        count_second = Notification.objects.count()
        
        self.assertEqual(count_first, count_second)

    def test_cleanup_old_notifications(self):
        """Test deletion of notifications older than 90 days"""
        old_date = timezone.now() - timedelta(days=91)
        n = Notification.objects.create(
            user=self.user,
            title='Old Notification',
            message='Old',
        )
        # Hack to overwrite auto_now_add
        Notification.objects.filter(pk=n.pk).update(created_at=old_date)
        
        call_command('send_notifications')
        
        self.assertFalse(Notification.objects.filter(pk=n.pk).exists())
