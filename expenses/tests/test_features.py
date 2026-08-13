import io
import zipfile
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import (
    Account,
    Category,
    Expense,
    Loan,
    LoanRepayment,
    Notification,
    RecurringTransaction,
)


class BaseFeatureTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='password')
        self.client = Client()
        self.client.login(username='testuser', password='password')
        profile = self.user.profile
        profile.tier = 'PLUS'
        profile.save()

class HealthCheckTest(TestCase):
    def test_health_check_endpoint(self):
        url = reverse('health_check')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get('status'), 'healthy')

class SettingsViewTest(BaseFeatureTest):
    def test_currency_update(self):
        url = reverse('currency-settings')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        data = {'currency': '$'}
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.currency, '$')

    def test_user_delete(self):
        url = reverse('user-delete')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.filter(username='testuser').exists())

class FeatureViewTest(BaseFeatureTest):
    def test_calendar_default(self):
        url = reverse('calendar')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_calendar_month(self):
        url = reverse('calendar-month', kwargs={'year': 2025, 'month': 1})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_calendar_htmx_returns_partial_only(self):
        url = reverse('calendar')
        response = self.client.get(url, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'expenses/partials/_calendar_content.html')
        self.assertTemplateNotUsed(response, 'base.html')

    def test_budget_view(self):
        url = reverse('budget')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_export_expenses(self):
        # Upgrade user to Plus with lifetime access to bypass date check
        self.user.profile.tier = 'PLUS'
        self.user.profile.is_lifetime = True
        self.user.profile.save()
        
        # Create some data
        Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Test', currency='₹')
        
        url = reverse('export-expenses')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode('utf-8')
        self.assertIn('Food', content)
        self.assertIn('100', content)
        self.assertIn('Currency', content)
        self.assertIn('Base Amount', content)

    def test_export_data_includes_loan_repayments(self):
        self.user.profile.tier = 'PLUS'
        self.user.profile.is_lifetime = True
        self.user.profile.save()

        account = Account.objects.create(
            user=self.user,
            name='Loan Pay Account',
            account_type='BANK',
            balance=Decimal('50000.00'),
            currency='₹',
        )
        loan = Loan.objects.create(
            user=self.user,
            name='Home Loan',
            loan_type='HOME',
            initial_principal=Decimal('2000000.00'),
            duration_months=240,
            start_date=date.today(),
            currency='₹',
        )
        LoanRepayment.objects.create(
            loan=loan,
            from_account=account,
            amount=Decimal('25000.00'),
            principal_portion=Decimal('20000.00'),
            interest_portion=Decimal('5000.00'),
            date=date.today(),
        )

        response = self.client.post(reverse('export-data'), {'entities': ['loan_repayments']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        content = response.content.decode('utf-8')
        self.assertIn('Loan', content)
        self.assertIn('Home Loan', content)
        self.assertIn('Principal Portion', content)

    def test_export_data_zip_contains_loan_repayments_csv(self):
        self.user.profile.tier = 'PLUS'
        self.user.profile.is_lifetime = True
        self.user.profile.save()

        Category.objects.get_or_create(user=self.user, name='Food')
        Expense.objects.create(user=self.user, date=date.today(), amount=100, category='Food', description='Zip Test', currency='₹')

        response = self.client.post(reverse('export-data'), {'entities': ['expenses', 'loan_repayments']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/x-zip-compressed')

        with zipfile.ZipFile(io.BytesIO(response.content), 'r') as zip_file:
            names = set(zip_file.namelist())
            self.assertIn('expenses.csv', names)
            self.assertIn('loan_repayments.csv', names)

class RecurringCRUDTest(BaseFeatureTest):
    def test_create_recurring(self):
        # Upgrade user to Pro for unlimited recurring transactions
        self.user.profile.tier = 'PRO'
        self.user.profile.is_lifetime = True
        self.user.profile.save()

        # Ensure category exists for form choice validation
        Category.objects.get_or_create(user=self.user, name='Entertainment')
        
        url = reverse('recurring-create')
        data = {
            'transaction_type': 'EXPENSE',
            'amount': 500,
            'description': 'Netflix',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'category': 'Entertainment',
            'payment_method': 'Cash',
            'currency': '₹'
        }
        response = self.client.post(url, data)
        if response.status_code == 200:
             self.fail(f"Form errors: {response.context['form'].errors}")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecurringTransaction.objects.count(), 1)

    def test_update_recurring(self):
        Category.objects.get_or_create(user=self.user, name='Entertainment')
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=500, description='Netflix',
            frequency='MONTHLY', start_date=date.today(), category='Entertainment', currency='₹'
        )
        url = reverse('recurring-edit', kwargs={'pk': rt.pk})
        data = {
            'transaction_type': 'EXPENSE',
            'amount': 600,
            'description': 'Netflix Premium',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'category': 'Entertainment',
            'payment_method': 'Credit Card',
            'currency': '₹'
        }
        response = self.client.post(url, data)
        if response.status_code == 200:
             self.fail(f"Form errors: {response.context['form'].errors}")
        self.assertEqual(response.status_code, 302)
        rt.refresh_from_db()
        self.assertEqual(rt.amount, 600)

    def test_delete_recurring(self):
        rt = RecurringTransaction.objects.create(
            user=self.user, transaction_type='EXPENSE', amount=500, description='Netflix',
            frequency='MONTHLY', start_date=date.today(), currency='₹'
        )
        url = reverse('recurring-delete', kwargs={'pk': rt.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecurringTransaction.objects.count(), 0)

class NotificationViewTest(BaseFeatureTest):
    def test_notification_list(self):
        Notification.objects.create(user=self.user, title='Title', message='Msg')
        url = reverse('notification-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # Check if list is present (paginated_list or object_list)
        self.assertTrue(len(response.context['object_list']) > 0)

    def test_mark_all_read(self):
        Notification.objects.create(user=self.user, title='Title', message='Msg', is_read=False)
        url = reverse('mark-all-read')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Notification.objects.filter(is_read=False).exists())
