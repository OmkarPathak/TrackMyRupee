from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import IntegrityError
from django.test import Client, TestCase

from expenses.models import Expense, Income, Notification, RecurringTransaction, UserProfile
from expenses.utils import get_exchange_rate
from expenses.views.settings import (
    dispatch_currency_recalculation,
    recalculate_user_transactions,
)


class CurrencyConversionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        if not hasattr(self.user, 'profile'):
            UserProfile.objects.create(user=self.user, currency='₹') # Base is INR
        else:
            self.user.profile.currency = '₹'
            self.user.profile.save()
        
        # Clear cache before each test
        cache.clear()

    @patch('requests.get')
    def test_identity_conversion(self, mock_get):
        """Converting same currency should return 1.0 and not call API."""
        rate = get_exchange_rate('₹', '₹')
        self.assertEqual(rate, Decimal('1.0'))
        mock_get.assert_not_called()

    @patch('requests.get')
    def test_successful_conversion_api(self, mock_get):
        """Test successful API call and base_amount calculation."""
        # Mock API response for USD to INR
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'rates': {'INR': 83.50}}
        mock_get.return_value = mock_response

        # 1. Test utility directly
        rate = get_exchange_rate('$', '₹')
        self.assertEqual(rate, Decimal('83.50'))
        
        # 2. Test Model Integration (Expense)
        obj = Expense.objects.create(
            user=self.user,
            date='2024-01-01',
            amount=Decimal('10.00'),
            currency='$',
            description='Test USD Expense',
            category='Food'
        )
        self.assertEqual(obj.exchange_rate, Decimal('83.50'))
        self.assertEqual(obj.base_amount, Decimal('835.00'))

    @patch('requests.get')
    def test_conversion_caching(self, mock_get):
        """Verify that exchange rates are cached and API is only called once."""
        from django.core.cache import cache
        cache.clear()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {'rates': {'INR': 80.00}}
        mock_get.return_value = mock_response

        # First call hits API
        rate1 = get_exchange_rate('$', '₹')
        self.assertEqual(rate1, Decimal('80.00'))
        self.assertEqual(mock_get.call_count, 1)

        # Second call hits cache
        rate2 = get_exchange_rate('$', '₹')
        self.assertEqual(rate2, Decimal('80.00'))
        self.assertEqual(mock_get.call_count, 1)

    @patch('requests.get')
    def test_api_failure_fallback(self, mock_get):
        """If both providers fail, conversion should fail closed."""
        mock_get.side_effect = requests.exceptions.HTTPError("API Down")

        with self.assertRaises(RuntimeError):
            get_exchange_rate('$', '₹')

        with self.assertRaises(RuntimeError):
            Expense.objects.create(
                user=self.user,
                date='2024-01-01',
                amount=Decimal('50.00'),
                currency='$',
                description='Fallback Test'
            )

    @patch('requests.get')
    def test_malformed_json_fallback(self, mock_get):
        """Malformed responses should fail conversion instead of assuming 1.0."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_get.return_value = mock_response

        with self.assertRaises(RuntimeError):
            get_exchange_rate('$', '₹')

    @patch('requests.get')
    def test_precision_preservation(self, mock_get):
        """Ensure high precision rates are saved correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # A rate with many decimal places
        complex_rate = 83.123456
        mock_response.json.return_value = {'rates': {'INR': complex_rate}}
        mock_get.return_value = mock_response

        obj = Income.objects.create(
            user=self.user,
            date='2024-01-01',
            amount=Decimal('100.00'),
            currency='$',
            source='Freelance'
        )
        self.assertEqual(obj.exchange_rate, Decimal('83.123456'))
        # 100 * 83.123456 = 8312.3456 -> Rounded to 8312.35 in DecimalField(decimal_places=2)
        self.assertEqual(obj.base_amount, Decimal('8312.35'))

    def test_unsupported_currency_symbol(self, mock_get=None):
        """Unknown symbols should fail if no provider can resolve them."""
        # 'XYZ' is not in our mapping
        with self.assertRaises(RuntimeError):
            get_exchange_rate('XYZ', 'INR')

    @patch('expenses.utils.get_exchange_rate')
    def test_historical_normalization_on_currency_change(self, mock_get_rate):
        """Verify that changing user currency re-calculates base_amount for existing records."""
        # Mock rate: 1 USD -> INR = 80, 1 USD -> USD = 1.0
        def side_effect(frm, to):
            if frm == '$' and to == '₹': return Decimal('80.0')
            if frm == '$' and to == '$': return Decimal('1.0')
            return Decimal('1.0')
        mock_get_rate.side_effect = side_effect
        
        # Patch models AND view logic to be absolutely sure
        with patch('expenses.models.get_exchange_rate', side_effect=side_effect):
            expense = Expense.objects.create(
                user=self.user,
                date=date.today(),
                amount=Decimal('10.00'),
                currency='$',
                description='Normalization Test'
            )
            # Initial base_amount = 10 * 80 = 800
            self.assertEqual(expense.base_amount, Decimal('800.00'))
            
            # Action: Change user base currency to USD ($)
            self.user.profile.currency = '$'
            self.user.profile.save()
            
            # Trigger re-normalization
            expense.save() 
            
            expense.refresh_from_db()
            # New base_amount should be 10.00
            self.assertEqual(expense.base_amount, Decimal('10.00'))

class CurrencyRecalculationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='recalcuser', password='password')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client = Client()
        self.client.login(username='recalcuser', password='password')

    @patch('expenses.views.settings.dispatch_currency_recalculation')
    def test_currency_view_immediate_profile_update_and_dispatch(self, mock_dispatch):
        """CurrencyUpdateView immediately updates UserProfile.currency and dispatches background recalculation."""
        from django.urls import reverse
        mock_dispatch.return_value = True

        url = reverse('currency-settings')
        response = self.client.post(url, {'currency': '$'}, follow=True)
        self.assertEqual(response.status_code, 200)

        # Profile is updated immediately
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.currency, '$')

        # Background recalculation was dispatched with old and new currency
        mock_dispatch.assert_called_once_with(self.user.id, '₹', '$')

        # Check user-facing informational message
        messages = list(response.context['messages'])
        self.assertTrue(any('historical transactions are being recalculated' in m.message.lower() for m in messages))

    @patch('expenses.models.get_exchange_rate')
    def test_successful_recalculation_all_models(self, mock_get_rate):
        """recalculate_user_transactions successfully updates Expense, Income, RecurringTransaction, clears lock, and sends notification."""
        def rate_side_effect(frm, to):
            if frm == '$' and to == '₹':
                return Decimal('80.0')
            if frm == '$' and to == '$':
                return Decimal('1.0')
            return Decimal('1.0')
        mock_get_rate.side_effect = rate_side_effect

        # User starts with INR base currency
        # Create 10 expenses, 5 incomes, 5 recurring transactions with currency='$'
        expenses = []
        for i in range(10):
            expenses.append(Expense.objects.create(
                user=self.user, date=date.today(), amount=Decimal('10.00'), currency='$',
                description=f'Expense {i}', category='Food'
            ))
        incomes = []
        for i in range(5):
            incomes.append(Income.objects.create(
                user=self.user, date=date.today(), amount=Decimal('20.00'), currency='$',
                source='Salary'
            ))
        recurring = []
        for i in range(5):
            recurring.append(RecurringTransaction.objects.create(
                user=self.user, transaction_type='EXPENSE', amount=Decimal('15.00'), currency='$',
                description=f'Sub {i}', frequency='MONTHLY', start_date=date.today()
            ))

        # Check initial values (all converted to INR: rate 80)
        for e in expenses:
            self.assertEqual(e.base_amount, Decimal('800.00'))
        for inc in incomes:
            self.assertEqual(inc.base_amount, Decimal('1600.00'))
        for r in recurring:
            self.assertEqual(r.base_amount, Decimal('1200.00'))

        # Set user profile currency to '$' and run recalculation
        self.user.profile.currency = '$'
        self.user.profile.save()

        success = recalculate_user_transactions(self.user.id, '₹', '$')
        self.assertTrue(success)

        # Verify all transactions are updated to new base currency (rate 1.0)
        for e in expenses:
            e.refresh_from_db()
            self.assertEqual(e.exchange_rate, Decimal('1.0'))
            self.assertEqual(e.base_amount, Decimal('10.00'))

        for inc in incomes:
            inc.refresh_from_db()
            self.assertEqual(inc.exchange_rate, Decimal('1.0'))
            self.assertEqual(inc.base_amount, Decimal('20.00'))

        for r in recurring:
            r.refresh_from_db()
            self.assertEqual(r.exchange_rate, Decimal('1.0'))
            self.assertEqual(r.base_amount, Decimal('15.00'))

        # Check lock is cleared
        self.assertIsNone(cache.get(f'currency_recalc_lock_{self.user.id}'))

        # Check completion notification created
        notif = Notification.objects.filter(user=self.user, notification_type='SYSTEM').latest('created_at')
        self.assertIn("Completed", notif.title)
        self.assertIn("$", notif.message)

    @patch('expenses.models.get_exchange_rate')
    def test_forced_failure_atomicity_and_rollback(self, mock_get_rate):
        """
        When recalculation fails partway through, the entire transaction rolls back atomically.
        No transactions are left partially updated in the new currency, UserProfile.currency is
        reverted to old_currency, lock is deleted, and a failure notification is posted.
        """
        def rate_side_effect(frm, to):
            if frm == '$' and to == '₹':
                return Decimal('80.0')
            if frm == '$' and to == '$':
                return Decimal('1.0')
            return Decimal('1.0')
        mock_get_rate.side_effect = rate_side_effect

        # Create 10 expenses in USD under INR base currency
        expenses = []
        for i in range(10):
            expenses.append(Expense.objects.create(
                user=self.user, date=date.today(), amount=Decimal('10.00'), currency='$',
                description=f'Expense {i}', category='Food'
            ))

        for e in expenses:
            self.assertEqual(e.base_amount, Decimal('800.00'))

        # User changes currency to USD
        self.user.profile.currency = '$'
        self.user.profile.save()

        # Simulate failure on the 6th expense save
        real_save = Expense.save
        call_count = [0]
        def faulty_save(instance, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 6:
                raise IntegrityError("Simulated database constraint failure during recalculation")
            return real_save(instance, *args, **kwargs)

        with patch.object(Expense, 'save', side_effect=faulty_save, autospec=True):
            success = recalculate_user_transactions(self.user.id, '₹', '$')
            self.assertFalse(success)

        # ATOMICITY ASSERTION:
        # All 10 expenses MUST still have base_amount == 800.00 (none partially committed)
        for i, e in enumerate(expenses):
            e.refresh_from_db()
            self.assertEqual(
                e.base_amount,
                Decimal('800.00'),
                f"Expense {i} was partially committed! Atomicity failed."
            )

        # User profile currency must be reverted back to old_currency ('₹')
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.currency, '₹')

        # Failure notification posted
        notif = Notification.objects.filter(user=self.user, notification_type='SYSTEM').latest('created_at')
        self.assertIn("Failed", notif.title)
        self.assertIn("reverted", notif.message.lower())

        # Lock is released
        self.assertIsNone(cache.get(f'currency_recalc_lock_{self.user.id}'))

    def test_concurrency_lock_prevents_duplicate_runs(self):
        """dispatch_currency_recalculation skips if a lock already exists for this user."""
        lock_key = f'currency_recalc_lock_{self.user.id}'
        cache.set(lock_key, 1, timeout=600)

        # Dispatch should reject running
        dispatched = dispatch_currency_recalculation(self.user.id, '₹', '$')
        self.assertFalse(dispatched)

        # Form submission should warn about recalculation in progress
        from django.urls import reverse
        url = reverse('currency-settings')
        response = self.client.post(url, {'currency': '$'}, follow=True)
        messages = list(response.context['messages'])
        self.assertTrue(any('already in progress' in m.message.lower() for m in messages))
