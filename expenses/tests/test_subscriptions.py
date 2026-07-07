import json
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from expenses.models import (
    Account,
    Category,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    UserProfile,
)
from finance_tracker.plans import PLAN_DETAILS


class SubscriptionTierTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='subuser', password='password', email='sub@example.com')
        # Clear categories created by signals
        Category.objects.filter(user=self.user).delete()
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.client = Client()
        self.client.login(username='subuser', password='password')

    def setup_tier(self, tier):
        self.profile.tier = tier
        if tier != 'FREE':
            self.profile.is_lifetime = True
        else:
            self.profile.is_lifetime = False
        self.profile.save()

    def test_free_tier_category_limit(self):
        """Free tier should limit to the configured categories."""
        self.setup_tier('FREE')
        limit = PLAN_DETAILS['FREE']['limits']['budget_categories']
        if limit == -1: return # Skip if unlimited
        for i in range(limit):
            Category.objects.create(user=self.user, name=f'Cat {i}')
        
        # Adding one more category should fail (AJAX)
        response = self.client.post(
            reverse('category-create-ajax'),
            data=json.dumps({'name': f'Cat {limit+1}'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(json.loads(response.content)['success'])

    def test_plus_tier_category_limit(self):
        """Plus tier should limit to the configured categories."""
        self.setup_tier('PLUS')
        limit = PLAN_DETAILS['PLUS']['limits']['budget_categories']
        if limit == -1: return # Skip if unlimited
        for i in range(limit):
            Category.objects.create(user=self.user, name=f'Cat {i}')
        
        # Adding one more category should fail
        response = self.client.post(reverse('category-create'), {'name': f'Cat {limit+1}'})
        # Should redirect back with error message (or to pricing page depending on view logic)
        self.assertEqual(response.status_code, 302)
        
        # Verify extra was NOT created
        self.assertEqual(Category.objects.filter(user=self.user).count(), limit)

    def test_pro_tier_category_limit(self):
        """Pro tier should have unlimited categories."""
        self.setup_tier('PRO')
        for i in range(12): # More than Plus limit
            Category.objects.create(user=self.user, name=f'Cat {i}')
        
        # Should still be able to add more via AJAX
        response = self.client.post(
            reverse('category-create-ajax'),
            data=json.dumps({'name': 'Cat Extra'}),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(json.loads(response.content)['success'])

    def test_free_tier_recurring_limit(self):
        """Free tier should respect recurring transaction limits."""
        self.setup_tier('FREE')
        limit = PLAN_DETAILS['FREE']['limits']['recurring_transactions']
        if limit == -1: return
        Category.objects.create(user=self.user, name='Food')
        
        # Fill up to limit
        for i in range(limit):
            RecurringTransaction.objects.create(
                user=self.user, transaction_type='EXPENSE', amount=100, 
                description=f'RT {i}', frequency='MONTHLY', start_date=date.today(), category='Food'
            )
            
        # Try one more
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE', 'amount': 100, 'description': 'Limit Check',
            'frequency': 'MONTHLY', 'start_date': date.today(), 'category': 'Food',
            'currency': '₹', 'payment_method': 'Cash'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('pricing', response.url)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), limit)

    def test_plus_tier_recurring_limit(self):
        """Plus tier should respect the configured recurring transaction limit."""
        self.setup_tier('PLUS')
        limit = PLAN_DETAILS['PLUS']['limits']['recurring_transactions']
        if limit == -1: return
        Category.objects.create(user=self.user, name='Food')
        for i in range(limit):
            RecurringTransaction.objects.create(
                user=self.user,
                transaction_type='EXPENSE',
                amount=100,
                description=f'RT {i}',
                frequency='MONTHLY',
                start_date=date.today(),
                category='Food'
            )
        
        # Try one more
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE',
            'amount': 100,
            'description': 'Limit Check Extra',
            'frequency': 'MONTHLY',
            'start_date': date.today(),
            'category': 'Food',
            'currency': '₹',
            'payment_method': 'Cash'
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('pricing', response.url)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user).count(), limit)

    def test_export_access(self):
        """Only Plus/Pro can export."""
        # Free
        self.setup_tier('FREE')
        response = self.client.get(reverse('export-expenses'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('pricing', response.url)
        
        # Plus
        self.setup_tier('PLUS')
        response = self.client.get(reverse('export-expenses'))
        self.assertEqual(response.status_code, 200) # CSV returned
        self.assertEqual(response['Content-Type'], 'text/csv')

    def test_email_reminder_gate(self):
        """Only Plus/Pro receive email reminders."""
        from io import StringIO

        from django.core.management import call_command

        from expenses.models import Notification, RecurringTransaction
        
        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            description='Due Soon',
            frequency='MONTHLY',
            start_date=date.today(),
            last_processed_date=None
        )
        # Mock next_due_date to be exactly 3 days from now
        from datetime import timedelta
        target_due = date.today() + timedelta(days=3)
        
        # Adjust start_date and last_processed_date so next_due_date is exactly target_due
        # If monthly, and next is target_due, then last was target_due - 1 month
        def get_last_month(d):
            new_month = d.month - 1
            new_year = d.year
            if new_month == 0:
                new_month = 12
                new_year -= 1
            
            # Handle day out of range (e.g. March 31 -> Feb 28/29)
            new_day = d.day
            while True:
                try:
                    return date(new_year, new_month, new_day)
                except ValueError:
                    new_day -= 1
        
        rt.start_date = get_last_month(target_due)
        rt.last_processed_date = get_last_month(target_due)
        rt.save()
        
        # Free Tier
        self.setup_tier('FREE')
        Notification.objects.filter(user=self.user).delete()
        out = StringIO()
        call_command('send_notifications', stdout=out)
        self.assertIn('Skipping email for subuser (Free Tier)', out.getvalue())
        
        # Plus Tier
        self.setup_tier('PLUS')
        Notification.objects.filter(user=self.user).delete()
        out = StringIO()
        call_command('send_notifications', stdout=out)
        self.assertIn('Sent consolidated email to sub@example.com', out.getvalue())

    def test_ai_prediction_gate(self):
        """Only users with ai_insights access can access AI category prediction."""
        for tier in ['FREE', 'PLUS', 'PRO']:
            self.setup_tier(tier)
            has_access = PLAN_DETAILS[tier]['limits']['ai_insights']
            
            with patch('expenses.views.predict_category_ai') as mock_ai:
                mock_ai.return_value = 'Food'
                response = self.client.get(reverse('predict-category'), {'description': 'Milk'})
                
                if has_access:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()['category'], 'Food')
                else:
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(response.json()['error'], 'AI Insights is a paid feature.')

    def test_recurring_create_form_shows_loan_type(self):
        """Loan Repayment type should be available when creating a recurring transaction."""
        self.setup_tier('PLUS')
        response = self.client.get(reverse('recurring-create'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'value="LOAN"')

    def test_recurring_expense_edit_still_works(self):
        """Editing recurring EXPENSE should keep working after LOAN type support."""
        self.setup_tier('PLUS')

        category1 = Category.objects.create(user=self.user, name='Food')
        category2 = Category.objects.create(user=self.user, name='Utilities')
        account = Account.objects.create(user=self.user, name='Cash Box', account_type='CASH', balance=10000, currency='₹')

        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount=100,
            currency='₹',
            account=account,
            category=category1.name,
            frequency='MONTHLY',
            start_date=date.today(),
            description='Monthly test expense',
            payment_method='Cash',
            is_active=True,
        )

        response = self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}), {
            'transaction_type': 'EXPENSE',
            'amount': '250.00',
            'currency': '₹',
            'account': account.pk,
            'category': category2.name,
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
            'frequency': 'WEEKLY',
            'start_date': date.today().isoformat(),
            'description': 'Updated recurring expense',
            'is_active': 'on',
            'payment_method': 'UPI',
        })

        self.assertEqual(response.status_code, 302)
        rt.refresh_from_db()
        self.assertEqual(rt.transaction_type, 'EXPENSE')
        self.assertEqual(float(rt.amount), 250.0)
        self.assertEqual(rt.category, category2.name)
        self.assertEqual(rt.frequency, 'WEEKLY')
        self.assertEqual(rt.description, 'Updated recurring expense')
        self.assertEqual(rt.payment_method, 'UPI')

    def test_recurring_loan_repayment_uses_configured_amount(self):
        """Recurring loan repayment should post the configured amount, not a recalculated EMI."""
        self.setup_tier('PLUS')

        account = Account.objects.create(
            user=self.user,
            name='Main Bank',
            account_type='BANK',
            balance=1000000,
            currency='₹',
        )
        loan = Loan.objects.create(
            user=self.user,
            name='Home',
            loan_type='HOME',
            initial_principal=8000000,
            duration_months=240,
            start_date=date.today().replace(day=1),
            currency='₹',
        )
        LoanInterestRate.objects.create(loan=loan, interest_rate=8.8, effective_date=loan.start_date)

        RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='LOAN',
            amount=10000,
            currency='₹',
            account=account,
            loan=loan,
            frequency='DAILY',
            start_date=date.today() - timedelta(days=17),
            description='Daily loan repayment',
            is_active=True,
        )

        from expenses.views import process_user_recurring_transactions

        process_user_recurring_transactions(self.user)

        repayments = LoanRepayment.objects.filter(loan=loan).order_by('date')
        self.assertEqual(repayments.count(), 18)
        self.assertTrue(all(float(r.amount) == 10000.0 for r in repayments))
        self.assertLess(sum(float(r.interest_portion) for r in repayments), 50000.0)
        self.assertGreater(sum(float(r.principal_portion) for r in repayments), 130000.0)

    def test_loan_repayment_create_view_posts_successfully(self):
        """The direct loan repayment create view should save repayments without loan validation errors."""
        self.setup_tier('PLUS')

        account = Account.objects.create(
            user=self.user,
            name='Main Bank',
            account_type='BANK',
            balance=500000,
            currency='₹',
        )
        loan = Loan.objects.create(
            user=self.user,
            name='Home',
            loan_type='HOME',
            initial_principal=8000000,
            duration_months=240,
            start_date=date.today().replace(day=1),
            currency='₹',
        )
        LoanInterestRate.objects.create(loan=loan, interest_rate=8.8, effective_date=loan.start_date)

        response = self.client.post(reverse('loan-repayment-create', kwargs={'pk': loan.pk}), {
            'from_account': account.pk,
            'amount': '10000.00',
            'principal_portion': '9000.00',
            'interest_portion': '1000.00',
            'date': date.today().isoformat(),
        })

        self.assertEqual(response.status_code, 302)
        repayment = LoanRepayment.objects.get(loan=loan)
        self.assertEqual(float(repayment.amount), 10000.0)
        self.assertEqual(repayment.loan_id, loan.pk)

    def test_recurring_capital_event_create_view_posts_successfully(self):
        """Creating a CAPITAL subscription view posts successfully."""
        self.setup_tier('PLUS')
        account = Account.objects.create(user=self.user, name='Cash Box', account_type='CASH', balance=10000, currency='₹')

        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'CAPITAL',
            'amount': '25000.00',
            'currency': '₹',
            'account': account.pk,
            'capital_subtype': 'loan_prepayment',
            'frequency': 'YEARLY',
            'start_date': date.today().isoformat(),
            'description': 'Yearly loan prepayment',
            'is_active': 'on',
            'exclude_from_averages': 'on',
            'exclude_from_budget': 'on',
            'include_in_net_worth': 'on',
            'payment_method': 'Cash',
            'category': '',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })

        self.assertEqual(response.status_code, 302)
        rt = RecurringTransaction.objects.get(user=self.user, transaction_type='CAPITAL')
        self.assertEqual(float(rt.amount), 25000.0)
        self.assertEqual(rt.capital_subtype, 'loan_prepayment')
        self.assertTrue(rt.exclude_from_averages)
        self.assertTrue(rt.exclude_from_budget)
        self.assertTrue(rt.include_in_net_worth)

    def test_recurring_capital_event_edit_works(self):
        """Editing a CAPITAL subscription works."""
        self.setup_tier('PLUS')
        account = Account.objects.create(user=self.user, name='Cash Box', account_type='CASH', balance=10000, currency='₹')

        rt = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='CAPITAL',
            amount=5000,
            currency='₹',
            account=account,
            capital_subtype='large_purchase',
            frequency='MONTHLY',
            start_date=date.today(),
            description='Recurring large purchase',
            is_active=True,
        )

        response = self.client.post(reverse('recurring-edit', kwargs={'pk': rt.pk}), {
            'transaction_type': 'CAPITAL',
            'amount': '6000.00',
            'currency': '₹',
            'account': account.pk,
            'capital_subtype': 'medical_lump_sum',
            'frequency': 'MONTHLY',
            'start_date': date.today().isoformat(),
            'description': 'Updated description',
            'is_active': 'on',
            'exclude_from_averages': 'on',
            'exclude_from_budget': 'on',
            'include_in_net_worth': 'on',
            'payment_method': 'Cash',
            'category': '',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })

        self.assertEqual(response.status_code, 302)
        rt.refresh_from_db()
        self.assertEqual(float(rt.amount), 6000.0)
        self.assertEqual(rt.capital_subtype, 'medical_lump_sum')
        self.assertEqual(rt.description, 'Updated description')

    def test_recurring_end_date_validation(self):
        """Form validation should fail if end_date is before start_date."""
        self.setup_tier('PLUS')
        account = Account.objects.create(user=self.user, name='Cash Box', account_type='CASH', balance=10000, currency='₹')

        # 1. Invalid end date (before start date)
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE',
            'amount': '100.00',
            'currency': '₹',
            'account': account.pk,
            'category': 'Bills',
            'frequency': 'MONTHLY',
            'start_date': date(2026, 6, 15).isoformat(),
            'end_date': date(2026, 6, 14).isoformat(),  # 1 day before start
            'description': 'Temporary expense',
            'is_active': 'on',
            'payment_method': 'Cash',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIn('end_date', form.errors)

        # 2. Valid end date (after start date)
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE',
            'amount': '100.00',
            'currency': '₹',
            'account': account.pk,
            'category': 'Bills',
            'frequency': 'MONTHLY',
            'start_date': date(2026, 6, 15).isoformat(),
            'end_date': date(2026, 6, 16).isoformat(),  # 1 day after start
            'description': 'Temporary expense',
            'is_active': 'on',
            'payment_method': 'Cash',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })
        self.assertEqual(response.status_code, 302)
        rt = RecurringTransaction.objects.get(user=self.user, description='Temporary expense')
        self.assertEqual(rt.end_date, date(2026, 6, 16))

    def test_active_recurring_transaction_uniqueness(self):
        """Form validation should block duplicate active transactions but allow duplicates if older ones are inactive."""
        self.setup_tier('PLUS')
        account = Account.objects.create(user=self.user, name='Cash Box', account_type='CASH', balance=10000, currency='₹')

        # Create first active subscription
        rt_first = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount='150.00',
            currency='₹',
            account=account,
            category='Bills',
            frequency='MONTHLY',
            start_date=date(2026, 6, 15),
            description='Unique Sub',
            is_active=True,
        )

        # Attempt to create second active subscription with identical details
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE',
            'amount': '150.00',
            'currency': '₹',
            'account': account.pk,
            'category': 'Bills',
            'frequency': 'MONTHLY',
            'start_date': date(2026, 6, 15).isoformat(),
            'description': 'Unique Sub',
            'is_active': 'on',
            'payment_method': 'Cash',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertTrue(len(form.non_field_errors()) > 0)

        # Deactivate first subscription
        rt_first.is_active = False
        rt_first.save()

        # Try again - should succeed since the first one is now inactive
        response = self.client.post(reverse('recurring-create'), {
            'transaction_type': 'EXPENSE',
            'amount': '150.00',
            'currency': '₹',
            'account': account.pk,
            'category': 'Bills',
            'frequency': 'MONTHLY',
            'start_date': date(2026, 6, 15).isoformat(),
            'description': 'Unique Sub',
            'is_active': 'on',
            'payment_method': 'Cash',
            'source': '',
            'from_account': '',
            'to_account': '',
            'loan': '',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(RecurringTransaction.objects.filter(user=self.user, description='Unique Sub').count(), 2)

    def test_active_recurring_transaction_sorting(self):
        """Active subscriptions on the list view should be sorted by next due date (upcoming first)."""
        self.setup_tier('PLUS')
        account = Account.objects.create(user=self.user, name='Bank Account', account_type='BANK', balance=10000, currency='₹')

        # Sub 1: Next due date in 20 days
        rt_far = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount='100.00',
            currency='₹',
            account=account,
            category='Bills',
            frequency='MONTHLY',
            start_date=date.today() + timedelta(days=20),
            description='Far Subscription',
            is_active=True,
        )

        # Sub 2: Next due date in 10 days
        rt_near = RecurringTransaction.objects.create(
            user=self.user,
            transaction_type='EXPENSE',
            amount='200.00',
            currency='₹',
            account=account,
            category='Bills',
            frequency='MONTHLY',
            start_date=date.today() + timedelta(days=10),
            description='Near Subscription',
            is_active=True,
        )

        response = self.client.get(reverse('recurring-list'))
        self.assertEqual(response.status_code, 200)
        active_subs = list(response.context['active_subs'])
        
        # Verify that "Near Subscription" comes before "Far Subscription"
        near_index = next(i for i, sub in enumerate(active_subs) if sub.description == 'Near Subscription')
        far_index = next(i for i, sub in enumerate(active_subs) if sub.description == 'Far Subscription')
        self.assertLess(near_index, far_index)
