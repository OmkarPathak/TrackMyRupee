from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from expenses.account_valuation import get_baseline, get_current, get_display_value
from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    AssetValuation,
    Category,
    Expense,
    Holding,
    Loan,
    LoanInterestRate,
    LoanScheduleInstallment,
    PhysicalAsset,
    Valuation,
)


class FullPortfolioNetWorthIntegrationTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testfullportfolio", password="password")
        profile = self.user.profile
        profile.tier = "PRO"
        profile.currency = "₹"
        profile.save()

        today = date.today()

        # 1. HDFC Salary Account (SALARY_ACCOUNT) — Current ₹85,000
        self.acct_salary = Account.objects.create(
            user=self.user,
            name="HDFC Salary Account",
            account_type="SALARY_ACCOUNT",
            currency="₹",
            balance=Decimal("85000.00"),
            show_accrued_balance=True,
        )

        # 2. ICICI FD (FD) — Principal ₹5,00,000, 7.5% p.a., quarterly compounding, 500 days ago
        self.acct_fd = Account.objects.create(
            user=self.user,
            name="ICICI FD (3yr, 7.5%)",
            account_type="FD",
            currency="₹",
            balance=Decimal("500000.00"),
            deposit_principal=Decimal("500000.00"),
            deposit_rate=Decimal("7.50"),
            deposit_start_date=today - timedelta(days=500),
            deposit_compounding="QUARTERLY",
            show_accrued_balance=True,
        )

        # 3. ICICI RD (RD) — ₹5,000/mo, 18 installments = ₹90,000 principal
        self.acct_rd = Account.objects.create(
            user=self.user,
            name="ICICI RD",
            account_type="RD",
            currency="₹",
            balance=Decimal("90000.00"),
            deposit_principal=Decimal("90000.00"),
            deposit_rate=Decimal("6.50"),
            deposit_start_date=today - timedelta(days=540),
            deposit_compounding="QUARTERLY",
            rd_installment_amount=Decimal("5000.00"),
            rd_installment_day=5,
            show_accrued_balance=True,
        )

        # 4. ICICI Direct (DEMAT) — 2 holdings + ₹1,200 pending SIP cash
        self.acct_demat = Account.objects.create(
            user=self.user,
            name="ICICI Direct",
            account_type="DEMAT",
            currency="₹",
            balance=Decimal("1200.00"),
            show_accrued_balance=True,
        )
        self.holding1 = Holding.objects.create(
            account=self.acct_demat,
            instrument_name="Fund A",
            instrument_type="MUTUAL_FUND",
            units=Decimal("120.50"),
            avg_cost=Decimal("42.10"),
            currency="₹",
            is_active=True,
        )
        Valuation.objects.create(
            holding=self.holding1,
            value=Decimal("6150.00"),
            as_of_date=today,
        )
        self.holding2 = Holding.objects.create(
            account=self.acct_demat,
            instrument_name="Fund B",
            instrument_type="MUTUAL_FUND",
            units=Decimal("300.00"),
            avg_cost=Decimal("18.00"),
            currency="₹",
            is_active=True,
        )
        # holding2 has no Valuation rows (falls back to cost basis 300 * 18 = 5400)

        # 5. Flat in Pune (REAL_ESTATE) — Acquisition cost ₹65,00,000, latest valuation ₹92,00,000
        self.flat_asset = PhysicalAsset.objects.create(
            user=self.user,
            name="Pune Apartment",
            asset_class="REAL_ESTATE",
            acquisition_cost=Decimal("6500000.00"),
            acquisition_date=today - timedelta(days=1000),
            currency="₹",
        )
        AssetValuation.objects.create(
            asset=self.flat_asset,
            value=Decimal("9200000.00"),
            as_of_date=today,
        )
        self.acct_flat = Account.objects.create(
            user=self.user,
            name="Flat in Pune",
            account_type="REAL_ESTATE",
            currency="₹",
            linked_physical_asset=self.flat_asset,
            show_accrued_balance=True,
        )

        # 6. Traditional endowment policy (LIFE_INSURANCE) — 6 annual premiums @ ₹25,000 = ₹1,50,000, surrender valuation ₹95,000
        self.policy_asset = PhysicalAsset.objects.create(
            user=self.user,
            name="LIC Policy",
            asset_class="INSURANCE",
            policy_number="POL999888",
            premium_amount=Decimal("25000.00"),
            premium_frequency="ANNUAL",
            policy_start_date=today - timedelta(days=2000),
            sum_assured=Decimal("500000.00"),
            currency="₹",
        )
        AssetValuation.objects.create(
            asset=self.policy_asset,
            value=Decimal("95000.00"),
            as_of_date=today,
        )
        self.acct_policy = Account.objects.create(
            user=self.user,
            name="Traditional endowment policy",
            account_type="LIFE_INSURANCE",
            currency="₹",
            linked_physical_asset=self.policy_asset,
            show_accrued_balance=True,
        )
        self.cat_insurance = Category.objects.create(user=self.user, name="Insurance Premium")
        for i in range(6):
            Expense.objects.create(
                user=self.user,
                account=self.acct_policy,
                amount=Decimal("25000.00"),
                currency="₹",
                category="Insurance Premium",
                category_fk=self.cat_insurance,
                linked_physical_asset=self.policy_asset,
                date=today - timedelta(days=365 * (6 - i)),
            )

        # 7. HDFC Credit Card (CREDIT_CARD) — Owed ₹4,150
        self.acct_cc = Account.objects.create(
            user=self.user,
            name="HDFC Credit Card",
            account_type="CREDIT_CARD",
            currency="₹",
            balance=Decimal("-4150.00"),
            show_accrued_balance=True,
        )

        # 8. Home Loan (HOME_LOAN) — Initial ₹30,00,000, outstanding ₹27,15,000
        self.home_loan = Loan.objects.create(
            user=self.user,
            name="Home Loan",
            loan_type="HOME",
            repayment_type="EMI",
            initial_principal=Decimal("3000000.00"),
            duration_months=240,
            start_date=today - timedelta(days=1000),
            currency="₹",
        )
        LoanInterestRate.objects.create(
            loan=self.home_loan,
            interest_rate=Decimal("8.50"),
            effective_date=today - timedelta(days=1000),
        )
        LoanScheduleInstallment.objects.create(
            loan=self.home_loan,
            installment_no=36,
            due_date=today - timedelta(days=30),
            scheduled_principal=Decimal("6000.00"),
            scheduled_interest=Decimal("20000.00"),
            scheduled_balance=Decimal("2715000.00"),
            is_paid=True,
        )
        self.acct_loan = Account.objects.create(
            user=self.user,
            name="Home Loan Account",
            account_type="HOME_LOAN",
            currency="₹",
            balance=Decimal("-2715000.00"),
            linked_loan=self.home_loan,
            show_accrued_balance=True,
        )

    @override_settings(NET_WORTH_EXTENDED_MODELS_ENABLED=True)
    def test_full_portfolio_net_worth_toggle_on_and_off(self):
        """
        SPEC §6: Integration test asserting both toggle-on (accrued) and toggle-off (baseline) totals to the rupee.
        """
        # Toggle ON (all show_accrued_balance = True)
        net_worth_on, base_balances_on = LedgerReadService.get_net_worth(self.user)
        expected_fd_current = get_current(self.acct_fd)
        expected_rd_current = get_current(self.acct_rd)
        expected_demat_current = get_current(self.acct_demat)

        expected_on = (
            Decimal("85000.00")              # Salary
            + expected_fd_current            # FD current accrued
            + expected_rd_current            # RD current accrued
            + expected_demat_current         # Demat (6150 + 5400 + 0 uninvested cash)
            + Decimal("9200000.00")          # Flat valuation
            + Decimal("95000.00")            # Policy surrender valuation
            - Decimal("4150.00")             # Credit card owed
            - Decimal("2715000.00")          # Home loan outstanding
        )
        self.assertEqual(net_worth_on, expected_on)

        # Toggle OFF (set show_accrued_balance = False on all accounts)
        Account.objects.filter(user=self.user).update(show_accrued_balance=False)

        net_worth_off, base_balances_off = LedgerReadService.get_net_worth(self.user)
        expected_off = (
            Decimal("85000.00")              # Salary (no baseline, uses current)
            + Decimal("500000.00")           # FD baseline principal
            + get_baseline(self.acct_rd)      # RD baseline installments
            + Decimal("10473.05")            # Demat baseline cost basis (without uninvested cash)
            + Decimal("6500000.00")          # Flat acquisition cost
            + Decimal("150000.00")           # Policy cumulative premiums paid
            - Decimal("4150.00")             # Credit card (liability unaffected by toggle)
            - Decimal("2715000.00")          # Loan outstanding (liability unaffected by toggle)
        )
        self.assertEqual(net_worth_off, expected_off)

    def test_unaffected_strategies_suppress_baseline(self):
        """SPEC §0 & §6: BALANCE, REVOLVING_CREDIT, LOAN_OUTSTANDING baseline evaluates to None."""
        self.assertIsNone(get_baseline(self.acct_salary))
        self.assertIsNone(get_baseline(self.acct_cc))
        self.assertIsNone(get_baseline(self.acct_loan))

        # get_display_value returns get_current when baseline is None regardless of show_accrued_balance
        self.acct_salary.show_accrued_balance = False
        self.assertEqual(get_display_value(self.acct_salary), get_current(self.acct_salary))
        self.acct_cc.show_accrued_balance = False
        self.assertEqual(get_display_value(self.acct_cc), get_current(self.acct_cc))
        self.acct_loan.show_accrued_balance = False
        self.assertEqual(get_display_value(self.acct_loan), get_current(self.acct_loan))
