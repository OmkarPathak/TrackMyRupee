import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

import expenses.models as exp_models
from expenses.ledger_read_service import LedgerReadService
from expenses.models import (
    Account,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Income,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
    UserProfile,
)


class Command(BaseCommand):
    help = 'Sets up a read-only pro demo user with a rich, multi-month financial story'

    @staticmethod
    def _next_month_start(dt):
        return (dt.replace(day=28) + timedelta(days=4)).replace(day=1)

    @staticmethod
    def _calculate_emi(principal, annual_rate, months):
        principal = Decimal(str(principal or 0))
        annual_rate = Decimal(str(annual_rate or 0))
        if principal <= 0 or months <= 0:
            return Decimal('0.00')
        if annual_rate == 0:
            return (principal / Decimal(months)).quantize(Decimal('0.01'))

        monthly_rate = annual_rate / Decimal('12') / Decimal('100')
        one_plus_r_pow_n = (Decimal('1') + monthly_rate) ** int(months)
        emi = principal * monthly_rate * one_plus_r_pow_n / (one_plus_r_pow_n - Decimal('1'))
        return emi.quantize(Decimal('0.01'))

    def handle(self, *args, **kwargs):
        username = 'demo'
        
        # 1. Reset demo data (without deleting user to avoid protected FK issues)
        user_qs = User.objects.filter(username=username)
        if user_qs.exists():
            user = user_qs.first()

            with transaction.atomic():
                # Manually delete all items in reverse dependency graph.
                exp_models.GoalContribution.objects.filter(goal__user=user).delete()
                exp_models.SavingsGoal.objects.filter(user=user).delete()
                exp_models.Transfer.objects.filter(user=user).delete()
                exp_models.Expense.objects.filter(user=user).delete()
                exp_models.Income.objects.filter(user=user).delete()
                exp_models.LoanRepayment.objects.filter(loan__user=user).delete()
                exp_models.LoanInterestRate.objects.filter(loan__user=user).delete()
                exp_models.CapitalEvent.objects.filter(user=user).delete()
                exp_models.Loan.objects.filter(user=user).delete()
                exp_models.RecurringTransaction.objects.filter(user=user).delete()
                exp_models.Notification.objects.filter(user=user).delete()
                exp_models.NetWorthSnapshot.objects.filter(user=user).delete()
                exp_models.ConsentEvent.objects.filter(user=user).delete()
                exp_models.FinancialAuditLog.objects.filter(user=user).delete()
                exp_models.LedgerReconciliationReport.objects.filter(user=user).delete()

                # Clear ledger data before touching ledger accounts (PROTECT on JournalLine.ledger_account).
                exp_models.JournalLine.objects.filter(journal_entry__user=user).delete()
                exp_models.JournalEntry.objects.filter(user=user).delete()
                exp_models.LedgerAccount.objects.filter(user=user).delete()

                exp_models.Account.objects.filter(user=user).delete()
                exp_models.Category.objects.filter(user=user).delete()

            # Keep the same user row for stability in demo login flow.
            user.email = 'demo@example.com'
            user.set_password('demo_password_123')
            user.save(update_fields=['email', 'password'])
        else:
            user = User.objects.create_user(username=username, email='demo@example.com', password='demo_password_123')
        
        # Setup Profile as PRO
        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.has_seen_tutorial = True
        profile.tier = 'PRO'
        profile.is_lifetime = True
        profile.currency = '₹'
        profile.save()

        self.stdout.write(self.style.SUCCESS(f'Created user: {username} (PRO Tier)'))
        
        # 1.1 Setup Accounts
        # Keep starting balances at 0 and seed via income/transfers so account balances
        # are backed by ledger-posted transactions and reconcile cleanly.
        acc_main = Account.objects.create(
            user=user, 
            name="HDFC Bank (Main)", 
            account_type='BANK', 
            balance=Decimal('0.00'), 
            currency='₹'
        )
        acc_savings = Account.objects.create(
            user=user, 
            name="SBI Savings", 
            account_type='BANK', 
            balance=Decimal('0.00'), 
            currency='₹'
        )
        acc_cash = Account.objects.create(
            user=user, 
            name="Cash Wallet", 
            account_type='CASH', 
            balance=Decimal('0.00'), 
            currency='₹'
        )
        acc_invest = Account.objects.create(
            user=user, 
            name="Zerodha Demat", 
            account_type='INVESTMENT', 
            balance=Decimal('0.00'), 
            currency='₹'
        )

        self.stdout.write(self.style.SUCCESS('Created Bank and Cash Accounts'))
        
        # 2. Categories & Budgets
        categories_data = [
            # Needs
            {'name': 'Rent', 'limit': 18000, 'icon': 'bi-house-fill'},
            {'name': 'Groceries', 'limit': 6000, 'icon': 'bi-cart-fill'},
            {'name': 'Utilities', 'limit': 4000, 'icon': 'bi-lightning-charge-fill'},
            {'name': 'Transport', 'limit': 4000, 'icon': 'bi-car-front-fill'},
            
            # Wants
            {'name': 'Dining Out', 'limit': 5000, 'icon': 'bi-egg-fried'}, 
            {'name': 'Shopping', 'limit': 5000, 'icon': 'bi-bag-heart-fill'},
            {'name': 'Subscriptions', 'limit': 2000, 'icon': 'bi-tv-fill'},
            {'name': 'Travel', 'limit': 10000, 'icon': 'bi-airplane-fill'},
            
            # General / Investment-related
            {'name': 'Mutual Funds', 'limit': 15000, 'icon': 'bi-graph-up-arrow'},
            {'name': 'Stocks', 'limit': 5000, 'icon': 'bi-bank'},
            {'name': 'Other', 'limit': 3000, 'icon': 'bi-three-dots'},
        ]
        
        cat_objs = {}
        for c in categories_data:
            cat, created = Category.objects.get_or_create(
                user=user, 
                name=c['name'], 
                defaults={'limit': c['limit'], 'icon': c['icon']}
            )
            cat_objs[c['name']] = cat

        self.stdout.write(self.style.SUCCESS('Created Rich Categories'))

        # 3. Time Windows (Last 6 months + Next 6 months)
        today = date.today()
        current_month_start = today.replace(day=1)
        window_start = (current_month_start - timedelta(days=183)).replace(day=1)
        first_of_next_month = self._next_month_start(current_month_start)
        window_end_exclusive = first_of_next_month
        for _ in range(6):
            window_end_exclusive = self._next_month_start(window_end_exclusive)
        window_end = window_end_exclusive - timedelta(days=1)

        # 3.1 Seed opening balance through an Income (ledger-backed) instead of direct account balance.
        Income.objects.create(
            user=user,
            source='Opening Balance',
            amount=Decimal('1500000.00'),
            date=window_start - timedelta(days=1),
            description='Demo seed corpus',
            account=acc_main,
        )
        
        # 4. Income History
        income_sources = [
            {'source': '💼 Salary', 'amount': 45000, 'day': 1},
            {'source': '🚀 Freelance Gig', 'amount': 10000, 'day': 20},
        ]

        # Generate income for last 6 months + next 6 months window
        curr_month = window_start
        while curr_month <= window_end:
            for inc in income_sources:
                inc_date = curr_month.replace(day=inc['day'])
                if window_start <= inc_date <= window_end:
                    Income.objects.create(
                        user=user,
                        source=inc['source'],
                        amount=inc['amount'],
                        date=inc_date,
                        description=f"Indie Developer Income for {inc_date.strftime('%B %Y')}",
                        account=acc_main
                    )
            # Next Month
            curr_month = self._next_month_start(curr_month)

        self.stdout.write(self.style.SUCCESS('Generated 12-Month Income History (past 6 + future 6)'))

        # 5. Expenses (Structured but randomized)
        
        expense_patterns = [
            # Needs
            {'cat': 'Rent', 'amount': 18000, 'freq': 'MONTHLY', 'desc': '2BHK Rent in Pune'},
            {'cat': 'Groceries', 'amount': 1500, 'freq': 'WEEKLY', 'desc': 'Zepto/Blinkit Orders'},
            {'cat': 'Utilities', 'amount': 3200, 'freq': 'MONTHLY', 'desc': 'Electricity & Internet'},
            {'cat': 'Transport', 'amount': 600, 'freq': 'WEEKLY', 'desc': 'Uber/Auto Spends'},
            
            # Wants
            {'cat': 'Dining Out', 'amount': 2000, 'freq': 'WEEKLY', 'desc': 'Weekend Swiggy/Dining'},
            {'cat': 'Subscriptions', 'amount': 649, 'freq': 'MONTHLY', 'desc': 'Netflix Premium'},
            {'cat': 'Subscriptions', 'amount': 299, 'freq': 'MONTHLY', 'desc': 'Spotify Family'},
            {'cat': 'Shopping', 'amount': 3000, 'freq': 'MONTHLY', 'desc': 'Amazon/Myntra Shopping'},
        ]
        
        # Investment Transfers (SIPs)
        investment_patterns = [
            {'amount': 10000, 'freq': 'MONTHLY', 'desc': 'Nifty 50 Index Fund SIP'},
        ]

        curr_date = window_start
        while curr_date <= window_end:
            for pattern in expense_patterns:
                should_create = False
                if pattern['freq'] == 'MONTHLY' and curr_date.day == 5:
                    should_create = True
                elif pattern['freq'] == 'WEEKLY' and curr_date.weekday() == 6: # Every Sunday
                    should_create = True
                
                if should_create:
                    # Add some randomness to amount (except rent)
                    amt = pattern['amount']
                    if 'Rent' not in pattern['cat']:
                        variation = random.randint(-100, 300)
                        amt = Decimal(amt) + Decimal(variation)

                    # Determine Account
                    if pattern['cat'] in ['Groceries', 'Transport', 'Dining Out']:
                        selected_account = acc_cash
                    else:
                        selected_account = acc_main

                    Expense.objects.create(
                        user=user,
                        category=pattern['cat'],
                        amount=amt,
                        date=curr_date,
                        description=pattern['desc'],
                        payment_method='UPI' if 'Dining' in pattern['cat'] else 'Debit Card',
                        account=selected_account
                    )
                    
            for pattern in investment_patterns:
                if pattern['freq'] == 'MONTHLY' and curr_date.day == 5:
                    Transfer.objects.create(
                        user=user,
                        from_account=acc_main,
                        to_account=acc_invest,
                        amount=pattern['amount'],
                        date=curr_date,
                        description=pattern['desc']
                    )
            
            curr_date += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS('Generated Realistic Expense History and Investment Transfers'))
        
        # 5.1 Enforce "Today" and "Yesterday" Expenses for Dashboard KPI
        # We target ~₹12,000 in Dining Out for May to show the '33%' insight on ~₹36k total expenses
        today_expenses = [
            {'cat': 'Dining Out', 'amount': 3800, 'desc': 'Swiggy Dinner Party', 'date': today},
            {'cat': 'Transport', 'amount': 450, 'desc': 'Uber to Baner', 'date': today},
            {'cat': 'Groceries', 'amount': 850, 'desc': 'Zepto - Weekend stock', 'date': today - timedelta(days=1)},
            {'cat': 'Dining Out', 'amount': 2200, 'desc': 'Lunch at Blue Frog', 'date': today - timedelta(days=1)},
        ]
        
        for te in today_expenses:
            Expense.objects.get_or_create(
                user=user,
                category=te['cat'],
                amount=Decimal(str(te['amount'])),
                date=te['date'],
                description=te['desc'],
                payment_method='UPI',
                account=acc_cash if te['cat'] in ['Groceries', 'Transport', 'Dining Out'] else acc_main
            )
        
        self.stdout.write(self.style.SUCCESS('Injected Current Day Data for ROI Visibility'))

        # 5.2 Keep current-month Financial Health strong for demo consistency.
        # Target >= 28% savings rate so the dashboard typically lands in "Excellent".
        month_start = today.replace(day=1)
        current_month_income = (
            Income.objects.filter(user=user, date__gte=month_start)
            .aggregate(total=Sum('amount'))
            .get('total')
            or Decimal('0.00')
        )
        current_month_expenses = (
            Expense.objects.filter(user=user, date__gte=month_start)
            .aggregate(total=Sum('amount'))
            .get('total')
            or Decimal('0.00')
        )

        target_savings_rate = Decimal('0.28')
        if current_month_income > 0:
            current_savings_rate = (current_month_income - current_month_expenses) / current_month_income
        else:
            current_savings_rate = Decimal('-1.00')

        if current_savings_rate < target_savings_rate:
            required_income = (
                current_month_expenses / (Decimal('1.00') - target_savings_rate)
                if current_month_expenses > 0
                else Decimal('0.00')
            )
            top_up_income = (required_income - current_month_income + Decimal('1500.00')).quantize(Decimal('0.01'))

            if top_up_income > 0:
                Income.objects.create(
                    user=user,
                    source='Performance Bonus',
                    amount=top_up_income,
                    date=max(month_start + timedelta(days=2), today - timedelta(days=1)),
                    description='Q2 Performance Bonus',
                    account=acc_main,
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Adjusted current-month income by {profile.currency}{top_up_income} for strong demo financial health'
                    )
                )

        # 6. Savings Goals & Contributions
        goals = [
            {'name': 'Emergency Fund', 'target': 100000, 'current': 0, 'icon': '🛡️', 'color': 'success'},
            {'name': 'New iPad Pro', 'target': 80000, 'current': 0, 'icon': '📱', 'color': 'info'},
        ]

        for g_data in goals:
            goal = SavingsGoal.objects.create(
                user=user,
                name=g_data['name'],
                target_amount=Decimal(g_data['target']),
                icon=g_data['icon'],
                color=g_data['color'],
                target_date=today + timedelta(days=random.randint(180, 500))
            )

            # Add periodic contributions to show progress
            parts = []
            if 'Emergency' in goal.name:
                parts = [Decimal('15000.00'), Decimal('25000.00'), Decimal('20000.00')]
            elif 'iPad' in goal.name:
                parts = [Decimal('4000.00'), Decimal('6000.00'), Decimal('5000.00')]
            
            if parts:
                for i, part in enumerate(parts):
                    contrib_date = today - timedelta(days=30 * i + 5)
                    # We create a Transfer to represent the movement of money to the savings account
                    Transfer.objects.create(
                        user=user,
                        from_account=acc_main,
                        to_account=acc_savings,
                        amount=part,
                        date=contrib_date,
                        description=f"Savings for {goal.name}"
                    )
                    # Link to savings account
                    GoalContribution.objects.create(
                        goal=goal,
                        amount=part,
                        date=contrib_date,
                        account=acc_savings
                    )

        # Monthly ATM Withdrawals
        curr_month = window_start
        while curr_month <= window_end:
            withdrawal_date = curr_month.replace(day=10)
            if window_start <= withdrawal_date <= window_end:
                Transfer.objects.create(
                    user=user,
                    from_account=acc_main,
                    to_account=acc_cash,
                    amount=Decimal('25000.00'),
                    date=withdrawal_date,
                    description="ATM Withdrawal"
                )
            # Next Month
            curr_month = self._next_month_start(curr_month)

        self.stdout.write(self.style.SUCCESS('Created Savings Goals, Contributions and Monthly Transfers'))

        # 7. Demo Loans & Repayments
        home_loan_start = (today.replace(day=1) - timedelta(days=240)).replace(day=7)
        personal_loan_start = (today.replace(day=1) - timedelta(days=150)).replace(day=12)

        home_loan = Loan.objects.create(
            user=user,
            name='Home Renovation Loan',
            loan_type='HOME',
            initial_principal=Decimal('300000.00'),
            duration_months=60,
            start_date=home_loan_start,
            currency='₹',
        )
        LoanInterestRate.objects.create(
            loan=home_loan,
            interest_rate=Decimal('9.25'),
            effective_date=home_loan_start,
        )

        personal_loan = Loan.objects.create(
            user=user,
            name='Travel Personal Loan',
            loan_type='PERSONAL',
            initial_principal=Decimal('120000.00'),
            duration_months=24,
            start_date=personal_loan_start,
            currency='₹',
        )
        LoanInterestRate.objects.create(
            loan=personal_loan,
            interest_rate=Decimal('13.50'),
            effective_date=personal_loan_start,
        )

        def add_repayments(loan, annual_rate, months_to_add, day_of_month):
            remaining = Decimal(str(loan.initial_principal))
            emi = self._calculate_emi(remaining, annual_rate, loan.duration_months)
            repayment_month = loan.start_date

            for _ in range(months_to_add):
                payment_date = repayment_month.replace(day=min(day_of_month, 28))
                if payment_date > window_end:
                    break

                monthly_rate = Decimal(str(annual_rate)) / Decimal('12') / Decimal('100')
                interest = (remaining * monthly_rate).quantize(Decimal('0.01'))
                principal = (emi - interest).quantize(Decimal('0.01'))

                if principal > remaining:
                    principal = remaining
                    emi_amount = (principal + interest).quantize(Decimal('0.01'))
                else:
                    emi_amount = emi

                LoanRepayment.objects.create(
                    loan=loan,
                    from_account=acc_main,
                    amount=emi_amount,
                    principal_portion=principal,
                    interest_portion=interest,
                    date=payment_date,
                )
                remaining = (remaining - principal).quantize(Decimal('0.01'))
                repayment_month = self._next_month_start(repayment_month)

        add_repayments(home_loan, Decimal('9.25'), months_to_add=18, day_of_month=7)
        add_repayments(personal_loan, Decimal('13.50'), months_to_add=18, day_of_month=12)

        self.stdout.write(self.style.SUCCESS('Created demo loans with repayment history'))

        # 8. Recurring Transactions (The Alerts)
        
        # Fiber Internet (Due in 3 days)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=1179,
            description='Airtel Broadband',
            category='Utilities',
            frequency='MONTHLY',
            start_date=window_start,
            last_processed_date=today - timedelta(days=27),
            payment_method='UPI',
            account=acc_main
        )

        # Gym (Currently Inactive to show cancelled subs)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=2500,
            description='Gold\'s Gym Membership',
            category='Health' if 'Health' in cat_objs else 'Other',
            frequency='MONTHLY',
            start_date=window_start - timedelta(days=100),
            last_processed_date=window_start - timedelta(days=10),
            is_active=False,
            account=acc_main
        )

        # SaaS Income (Freelance Retainer)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='INCOME',
            amount=15000,
            description='Design Consultant Retainer',
            source='🚀 Freelance Gig',
            frequency='MONTHLY',
            start_date=window_start,
            last_processed_date=today - timedelta(days=10),
            account=acc_main
        )

        self.stdout.write(self.style.SUCCESS('Created Complex Recurring Transactions'))

        # 9. Capital Events
        # Adding some sample capital events but excluding them from net worth to keep current logic stable.
        CapitalEvent.objects.create(
            user=user,
            amount=Decimal('500000.00'),
            date=today - timedelta(days=240),
            subtype='loan_down_payment',
            note='Home Loan Down Payment',
            account=acc_main,
            linked_loan=home_loan,
            exclude_from_averages=True,
            exclude_from_budget=True,
            include_in_net_worth=False
        )

        CapitalEvent.objects.create(
            user=user,
            amount=Decimal('85000.00'),
            date=today - timedelta(days=60),
            subtype='medical_lump_sum',
            note='Dad\'s Surgery Out-of-pocket',
            account=acc_savings,
            exclude_from_averages=True,
            exclude_from_budget=True,
            include_in_net_worth=False
        )

        CapitalEvent.objects.create(
            user=user,
            amount=Decimal('150000.00'),
            date=today - timedelta(days=10),
            subtype='investment_lump_sum',
            note='Lump Sum in Parag Parikh Flexi Cap',
            account=acc_invest,
            exclude_from_averages=True,
            exclude_from_budget=True,
            include_in_net_worth=False
        )
        
        self.stdout.write(self.style.SUCCESS('Created Capital Events (Excluded from Net Worth)'))

        # 10. Net worth floor: guarantee strictly positive demo net worth.
        # This protects the live demo from rendering negative net worth due to liabilities.
        target_floor = Decimal('250000.00')
        current_net_worth, _ = LedgerReadService.get_net_worth(user)
        if current_net_worth <= target_floor:
            top_up = (target_floor - current_net_worth + Decimal('50000.00')).quantize(Decimal('0.01'))
            Income.objects.create(
                user=user,
                source='Net Worth Buffer',
                amount=top_up,
                date=today,
                description='Demo safeguard top-up to keep net worth positive',
                account=acc_main,
            )
            self.stdout.write(self.style.SUCCESS(f'Applied net worth safeguard top-up: {profile.currency}{top_up}'))

        self.stdout.write(self.style.SUCCESS('--- DEMO SETUP COMPLETE ---'))
