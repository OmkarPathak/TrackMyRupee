from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from expenses.models import Expense, Income, UserProfile
from expenses.utils import get_exchange_rate


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

class CurrencySettingsCollisionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='collisionuser', password='password')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'currency': '₹'})
        self.client = Client()
        self.client.login(username='collisionuser', password='password')

    @patch('expenses.models.get_exchange_rate')
    def test_currency_change_collision_handling(self, mock_get_rate):
        """Verify that CurrencyUpdateView handles IntegrityError during re-normalization."""
        from unittest.mock import patch

        from django.db import IntegrityError
        from django.urls import reverse

        # Mock exchange rate to return something different
        mock_get_rate.return_value = Decimal('80.0')

        # Create two expenses that WOULD collide if their fields were identical
        # Case: User manually created two very similar transactions (maybe with different currencies or before constraints)
        # We'll mock the save() method to raise IntegrityError for the second record
        e1 = Expense.objects.create(
            user=self.user, date='2024-01-01', amount=100, currency='₹', description='D', category='C'
        )
        e2 = Expense.objects.create(
            user=self.user, date='2024-01-02', amount=100, currency='₹', description='D', category='C'
        )

        with patch.object(Expense, 'save') as mock_save:
            # First call succeeds, second raises IntegrityError
            mock_save.side_effect = [None, IntegrityError("Duplicate")]
            
            url = reverse('currency-settings')
            response = self.client.post(url, {'currency': '$'})
            
            self.assertEqual(response.status_code, 302)
            # Verify that messages.warning was called (via checking response or similar)
            # However, simpler check: did it crash? No.
            self.assertEqual(mock_save.call_count, 2)
