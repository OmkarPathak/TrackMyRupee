import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from expenses.models import Account, Loan, LoanInterestRate, LoanRepayment
from expenses.services import LoanService


class LoanServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='loan_test', password='password')
        self.user.profile.tier = 'PLUS'
        self.user.profile.save(update_fields=['tier'])
        self.account = Account.objects.create(
            user=self.user,
            name='Test Bank',
            account_type='BANK',
            balance=Decimal('100000.00'),
            currency='₹'
        )
        self.loan = Loan.objects.create(
            user=self.user,
            name='Test Home Loan',
            loan_type='HOME',
            initial_principal=Decimal('50000.00'),
            duration_months=12,
            start_date=datetime.date(2023, 1, 1),
            currency='₹'
        )
        self.rate = LoanInterestRate.objects.create(
            loan=self.loan,
            interest_rate=Decimal('12.00'),
            effective_date=datetime.date(2023, 1, 1)
        )

    def test_calculate_emi(self):
        # 50,000 at 12% for 12 months -> EMI = P*r*(1+r)^n/((1+r)^n-1)
        # r = 0.01 (1%), P = 50000, n = 12
        # EMI ~ 4442.44
        emi = LoanService.calculate_emi(50000, 12, 12)
        self.assertAlmostEqual(emi, 4442.44, places=1)
        
        # Test 0 interest
        emi_zero_interest = LoanService.calculate_emi(50000, 0, 12)
        self.assertEqual(emi_zero_interest, 50000 / 12.0)

    def test_amortization_schedule(self):
        # Schedule with no payments made
        schedule = LoanService.generate_amortization_schedule(self.loan)
        # Should generate 12 payments because duration is 12 and 0 payments made, 
        # wait, the logic uses `today` to find months_passed. This might be problematic in tests if `today` > 2023.
        # Let's mock today or update the loan start date to today
        
        self.loan.start_date = datetime.date.today()
        self.loan.save()
        
        schedule = LoanService.generate_amortization_schedule(self.loan)
        self.assertEqual(len(schedule), 12)
        
        first_month = schedule[0]
        self.assertAlmostEqual(first_month['emi'], 4442.44, places=1)
        self.assertAlmostEqual(first_month['interest'], 500.00, places=1)  # 50000 * 0.01
        self.assertAlmostEqual(first_month['principal'], 3942.44, places=1)

    def test_repayment_deducts_from_account(self):
        initial_balance = self.account.balance
        
        repayment = LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.account,
            amount=Decimal('4442.44'),
            principal_portion=Decimal('3942.44'),
            interest_portion=Decimal('500.00'),
            date=datetime.date.today()
        )
        
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, initial_balance - Decimal('4442.44'))
        
        # Test reverse on delete
        repayment.delete()
        self.account.refresh_from_db()
        self.assertEqual(self.account.balance, initial_balance)

    def test_repayment_update_without_account(self):
        repayment = LoanRepayment.objects.create(
            loan=self.loan,
            from_account=None,
            amount=Decimal('4442.44'),
            principal_portion=Decimal('3942.44'),
            interest_portion=Decimal('500.00'),
            date=datetime.date.today(),
        )

        repayment.amount = Decimal('4300.00')
        repayment.principal_portion = Decimal('3800.00')
        repayment.interest_portion = Decimal('500.00')
        repayment.save()

        repayment.refresh_from_db()
        self.assertEqual(repayment.amount, Decimal('4300.00'))
        self.assertIsNone(repayment.from_account)

    def test_floating_interest_rate(self):
        # Start loan today
        self.loan.start_date = datetime.date.today()
        self.loan.save()
        
        # Change rate
        LoanInterestRate.objects.create(
            loan=self.loan,
            interest_rate=Decimal('24.00'), # 2% per month
            effective_date=datetime.date.today()
        )
        
        schedule = LoanService.generate_amortization_schedule(self.loan)
        first_month = schedule[0]
        # At 24% (2% per month), interest on 50000 is 1000
        self.assertAlmostEqual(first_month['interest'], 1000.00, places=1)
        
    def test_total_liabilities(self):
        # Make a repayment to reduce principal
        LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.account,
            amount=Decimal('4442.44'),
            principal_portion=Decimal('3942.44'),
            interest_portion=Decimal('500.00'),
            date=datetime.date.today()
        )
        
        # Expected remaining principal: 50000 - 3942.44 = 46057.56
        total = LoanService.get_total_liabilities(self.user)
        self.assertAlmostEqual(float(total), 46057.56, places=1)

    def test_loan_create_page_shows_emi_preview_calculator(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('loan-create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="emi-preview-value"')
        self.assertContains(response, 'id="emi-preview-hint"')
        self.assertContains(response, 'function calculateEmi')

    def test_free_tier_redirected_from_loan_pages(self):
        self.user.profile.tier = 'FREE'
        self.user.profile.save(update_fields=['tier'])
        self.client.force_login(self.user)

        response = self.client.get(reverse('loan-list'))
        self.assertRedirects(response, reverse('pricing'))

    def test_loan_views_include_chart_data(self):
        self.client.force_login(self.user)

        # Test LoanListView context contains portfolio & loan comparison chart data
        list_response = self.client.get(reverse('loan-list'))
        self.assertEqual(list_response.status_code, 200)
        self.assertIn('portfolio_breakdown_chart', list_response.context)
        self.assertIn('loan_comparison_chart', list_response.context)
        self.assertContains(list_response, 'id="portfolioBreakdownChart"')
        self.assertContains(list_response, 'id="loanComparisonChart"')

        # Test LoanDetailView context contains breakdown & amortization chart data
        detail_response = self.client.get(reverse('loan-detail', kwargs={'pk': self.loan.pk}))
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn('breakdown_chart_data', detail_response.context)
        self.assertIn('amortization_chart_data', detail_response.context)
        self.assertContains(detail_response, 'id="loanBreakdownChart"')
        self.assertContains(detail_response, 'id="loanAmortizationChart"')

    def test_loan_repayment_date_format_dd_mm_yyyy(self):
        """Test that date in DD/MM/YYYY format (e.g., 21/08/2026) is accepted cleanly by LoanRepaymentForm."""
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('loan-repayment-create', kwargs={'pk': self.loan.uuid}),
            {
                'from_account': self.account.id,
                'amount': '1000.00',
                'principal_portion': '900.00',
                'interest_portion': '100.00',
                'date': '21/08/2026',
            }
        )
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))

        repayment = self.loan.repayments.latest('created_at')
        self.assertEqual(repayment.date, datetime.date(2026, 8, 21))
        self.assertEqual(repayment.amount, Decimal('1000.00'))

    def test_loan_repayment_auto_cap_principal_on_final_payoff(self):
        """Test that submitting an amount higher than remaining principal auto-caps principal portion to exact remaining principal without errors."""
        self.client.force_login(self.user)
        # Reduce remaining principal down to 3.95
        LoanRepayment.objects.create(
            loan=self.loan,
            from_account=self.account,
            amount=Decimal('49996.05'),
            principal_portion=Decimal('49996.05'),
            interest_portion=Decimal('0.00'),
            date=datetime.date(2026, 8, 1)
        )
        self.assertEqual(self.loan.remaining_principal, Decimal('3.95'))

        # Submit higher amount (4.50 with 4.47 principal)
        response = self.client.post(
            reverse('loan-repayment-create', kwargs={'pk': self.loan.uuid}),
            {
                'from_account': self.account.id,
                'amount': '4.50',
                'principal_portion': '4.47',
                'interest_portion': '0.03',
                'date': '21/08/2026',
            }
        )
        self.assertRedirects(response, reverse('loan-detail', kwargs={'pk': self.loan.uuid}))

        repayment = self.loan.repayments.latest('created_at')
        self.assertEqual(repayment.principal_portion, Decimal('3.95'))
        self.assertEqual(repayment.interest_portion, Decimal('0.03'))
        self.assertEqual(repayment.amount, Decimal('3.98'))
        self.assertEqual(self.loan.remaining_principal, Decimal('0.00'))

