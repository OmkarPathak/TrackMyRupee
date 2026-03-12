from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from expenses.models import Category, Expense, Income, UserProfile, RecurringTransaction, SavingsGoal, GoalContribution
from datetime import date, timedelta, datetime
from django.utils import timezone
from decimal import Decimal
import random

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

        # 2. Categories & Budgets (Needs vs Wants vs Investments)
        categories_data = [
            # Needs
            {'name': 'Rent', 'limit': 25000, 'icon': 'bi-house-fill', 'is_investment': False},
            {'name': 'Groceries', 'limit': 8000, 'icon': 'bi-cart-fill', 'is_investment': False},
            {'name': 'Utilities', 'limit': 5000, 'icon': 'bi-lightning-charge-fill', 'is_investment': False},
            {'name': 'Transport', 'limit': 6000, 'icon': 'bi-car-front-fill', 'is_investment': False},
            
            # Wants
            {'name': 'Dining Out', 'limit': 5000, 'icon': 'bi-egg-fried', 'is_investment': False}, 
            {'name': 'Shopping', 'limit': 7000, 'icon': 'bi-bag-heart-fill', 'is_investment': False},
            {'name': 'Subscriptions', 'limit': 3000, 'icon': 'bi-tv-fill', 'is_investment': False},
            {'name': 'Travel', 'limit': 15000, 'icon': 'bi-airplane-fill', 'is_investment': False},
            
            # Investments (Excluded from standard expenses for story-telling)
            {'name': 'Mutual Funds', 'limit': 20000, 'icon': 'bi-graph-up-arrow', 'is_investment': True},
            {'name': 'Stocks', 'limit': 10000, 'icon': 'bi-bank', 'is_investment': True},
            {'name': 'Savings Transfer', 'limit': None, 'icon': 'bi-piggy-bank-fill', 'is_investment': True}, # For Goal contributions
        ]
        
        cat_objs = {}
        for c in categories_data:
            cat, created = Category.objects.get_or_create(
                user=user, 
                name=c['name'], 
                defaults={'limit': c['limit'], 'icon': c['icon'], 'is_investment': c['is_investment']}
            )
            cat_objs[c['name']] = cat

        self.stdout.write(self.style.SUCCESS('Created Rich Categories'))

        # 3. Time Windows (Last 3 months)
        today = date.today()
        three_months_ago = (today.replace(day=1) - timedelta(days=62)).replace(day=1) # Approx 3 months ago start
        
        # 4. Income History
        income_sources = [
            {'source': '💼 Salary', 'amount': 85000, 'day': 5},
            {'source': '🚀 Freelance Gig', 'amount': 25000, 'day': 20},
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
                        description=f"{inc['source']} for {inc_date.strftime('%B %Y')}"
                    )
            # Next Month
            next_month = curr_month.replace(day=28) + timedelta(days=4)
            curr_month = next_month.replace(day=1)

        self.stdout.write(self.style.SUCCESS('Generated 3-Month Income History'))

        # 5. Expenses (Structured but randomized)
        
        expense_patterns = [
            # Needs
            {'cat': 'Rent', 'amount': 25000, 'freq': 'MONTHLY', 'desc': 'Apartment Rent'},
            {'cat': 'Groceries', 'amount': 2000, 'freq': 'WEEKLY', 'desc': 'Weekly Groceries'},
            {'cat': 'Utilities', 'amount': 4500, 'freq': 'MONTHLY', 'desc': 'Electricity & Water'},
            {'cat': 'Transport', 'amount': 800, 'freq': 'WEEKLY', 'desc': 'Fuel/Cab Spends'},
            
            # Wants
            {'cat': 'Dining Out', 'amount': 1500, 'freq': 'WEEKLY', 'desc': 'Weekend Dinner'},
            {'cat': 'Subscriptions', 'amount': 649, 'freq': 'MONTHLY', 'desc': 'Netflix Premium'},
            {'cat': 'Subscriptions', 'amount': 299, 'freq': 'MONTHLY', 'desc': 'Spotify Family'},
            {'cat': 'Shopping', 'amount': 4000, 'freq': 'MONTHLY', 'desc': 'Amazon/Myntra Shopping'},
            
            # Investments (SIPs)
            {'cat': 'Mutual Funds', 'amount': 15000, 'freq': 'MONTHLY', 'desc': 'Nifty 50 Index Fund SIP'},
            {'cat': 'Stocks', 'amount': 5000, 'freq': 'MONTHLY', 'desc': 'Monthly Bluechip Portfolio'},
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
                        variation = random.randint(-200, 500)
                        amt = Decimal(amt) + Decimal(variation)

                    Expense.objects.create(
                        user=user,
                        category=pattern['cat'],
                        amount=amt,
                        date=curr_date,
                        description=pattern['desc'],
                        payment_method='UPI' if 'Dining' in pattern['cat'] else 'Debit Card'
                    )
            curr_date += timedelta(days=1)

        self.stdout.write(self.style.SUCCESS('Generated Realistic Expense History'))

        # 6. Savings Goals & Contributions
        goals = [
            {'name': 'Emergency Fund', 'target': 200000, 'current': 0, 'icon': '🛡️', 'color': 'success'},
            {'name': 'New MacBook Pro', 'target': 180000, 'current': 0, 'icon': '💻', 'color': 'info'},
            {'name': 'Maldives Trip', 'target': 300000, 'current': 0, 'icon': '🏝️', 'color': 'warning'},
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
            # GoalContribution also creates an Expense under "Savings Transfer"
            total_contrib = 0
            if 'Emergency' in goal.name:
                total_contrib = 140000 # 70%
            elif 'MacBook' in goal.name:
                total_contrib = 45000 # 25%
            
            if total_contrib > 0:
                # Break it into 3 monthly parts
                part = Decimal(total_contrib) / 3
                for i in range(3):
                    contrib_date = today - timedelta(days=30 * i + 5)
                    GoalContribution.objects.create(
                        goal=goal,
                        amount=part,
                        date=contrib_date
                    )

        self.stdout.write(self.style.SUCCESS('Created Savings Goals with Progress'))

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
            payment_method='UPI'
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
            is_active=False
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
        )

        self.stdout.write(self.style.SUCCESS('Created Complex Recurring Transactions'))

        self.stdout.write(self.style.SUCCESS('--- DEMO SETUP COMPLETE ---'))
