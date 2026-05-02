import random
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from expenses.models import (
    Account,
    Category,
    Expense,
    GoalContribution,
    Income,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
    UserProfile,
)


class Command(BaseCommand):
    help = 'Sets up a read-only pro demo user with a rich, multi-month financial story'

    def handle(self, *args, **kwargs):
        username = 'demo'
        
        # 1. Reset User
        User.objects.filter(username=username).delete()
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
        acc_main = Account.objects.create(
            user=user, 
            name="HDFC Bank (Main)", 
            account_type='BANK', 
            balance=Decimal('150000.00'), 
            currency='₹'
        )
        acc_savings = Account.objects.create(
            user=user, 
            name="SBI Savings", 
            account_type='BANK', 
            balance=Decimal('80000.00'), 
            currency='₹'
        )
        acc_cash = Account.objects.create(
            user=user, 
            name="Cash Wallet", 
            account_type='CASH', 
            balance=Decimal('5000.00'), 
            currency='₹'
        )
        acc_invest = Account.objects.create(
            user=user, 
            name="Zerodha Demat", 
            account_type='INVESTMENT', 
            balance=Decimal('120000.00'), 
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

        # 3. Time Windows (Last 3 months)
        today = date.today()
        three_months_ago = (today.replace(day=1) - timedelta(days=62)).replace(day=1) 
        
        # 4. Income History
        income_sources = [
            {'source': '💼 Salary', 'amount': 45000, 'day': 1},
            {'source': '🚀 Freelance Gig', 'amount': 10000, 'day': 20},
        ]

        # Generate income for past 3 months
        curr_month = three_months_ago
        while curr_month <= today:
            for inc in income_sources:
                inc_date = curr_month.replace(day=inc['day'])
                if inc_date <= today:
                    Income.objects.create(
                        user=user,
                        source=inc['source'],
                        amount=inc['amount'],
                        date=inc_date,
                        description=f"Indie Developer Income for {inc_date.strftime('%B %Y')}",
                        account=acc_main
                    )
            # Next Month
            next_month = curr_month.replace(day=28) + timedelta(days=4)
            curr_month = next_month.replace(day=1)

        self.stdout.write(self.style.SUCCESS('Generated 3-Month Income History'))

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
            
            # Investments (SIPs)
            {'cat': 'Mutual Funds', 'amount': 10000, 'freq': 'MONTHLY', 'desc': 'Nifty 50 Index Fund SIP'},
        ]

        curr_date = three_months_ago
        while curr_date <= today:
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
            curr_date += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS('Generated Realistic Expense History'))
        
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
            total_contrib = 0
            if 'Emergency' in goal.name:
                total_contrib = 60000 # ~20k/month
            elif 'MacBook' in goal.name:
                total_contrib = 15000 # ~5k/month
            
            if total_contrib > 0:
                # Break it into 3 monthly parts
                part = Decimal(total_contrib) / 3
                for i in range(3):
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
                    # Contribution now specifies an account and deducts balance directly
                    # We deduct from the savings account where the money was moved
                    GoalContribution.objects.create(
                        goal=goal,
                        amount=part,
                        date=contrib_date,
                        account=acc_savings
                    )

        # Monthly ATM Withdrawals
        curr_month = three_months_ago
        while curr_month <= today:
            withdrawal_date = curr_month.replace(day=10)
            if withdrawal_date <= today:
                Transfer.objects.create(
                    user=user,
                    from_account=acc_main,
                    to_account=acc_cash,
                    amount=Decimal('15000.00'),
                    date=withdrawal_date,
                    description="ATM Withdrawal"
                )
            # Next Month
            next_month = curr_month.replace(day=28) + timedelta(days=4)
            curr_month = next_month.replace(day=1)

        self.stdout.write(self.style.SUCCESS('Created Savings Goals, Contributions and Monthly Transfers'))

        # 7. Recurring Transactions (The Alerts)
        
        # Fiber Internet (Due in 3 days)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=1179,
            description='Airtel Broadband',
            category='Utilities',
            frequency='MONTHLY',
            start_date=three_months_ago,
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
            category='Health' if 'Health' in cat_objs else 'Wants',
            frequency='MONTHLY',
            start_date=three_months_ago - timedelta(days=100),
            last_processed_date=three_months_ago - timedelta(days=10),
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
            start_date=three_months_ago,
            last_processed_date=today - timedelta(days=10),
            account=acc_main
        )

        self.stdout.write(self.style.SUCCESS('Created Complex Recurring Transactions'))

        self.stdout.write(self.style.SUCCESS('--- DEMO SETUP COMPLETE ---'))
