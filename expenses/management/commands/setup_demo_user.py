from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from expenses.models import Category, Expense, Income, UserProfile, RecurringTransaction
from datetime import date, timedelta
import random

class Command(BaseCommand):
    help = 'Sets up a read-only demo user with story-driven data'

    def handle(self, *args, **kwargs):
        username = 'demo'
        
        # 1. Reset User
        User.objects.filter(username=username).delete()
        user = User.objects.create_user(username=username, email='demo@example.com', password='demo_password_123')
        
        # Setup Profile
        if not hasattr(user, 'profile'):
            UserProfile.objects.create(user=user)
        user.profile.has_seen_tutorial = True # Skip tutorial for demo to get straight to value
        user.profile.save()

        self.stdout.write(self.style.SUCCESS(f'Created user: {username}'))

        # 2. Categories & Budgets
        categories_data = [
            {'name': 'Rent', 'limit': 15000, 'icon': 'bi-house'},
            {'name': 'Groceries', 'limit': 6000, 'icon': 'bi-cart3'},
            {'name': 'Dining Out', 'limit': 3000, 'icon': 'bi-egg-fried'}, 
            {'name': 'Transport', 'limit': 4000, 'icon': 'bi-car-front'},
            {'name': 'Entertainment', 'limit': 2000, 'icon': 'bi-film'},
            {'name': 'Health', 'limit': 5000, 'icon': 'bi-suit-heart'},
        ]
        
        cat_objs = {}
        for c in categories_data:
            cat = Category.objects.create(user=user, name=c['name'], limit=c['limit'], icon=c['icon'])
            cat_objs[c['name']] = cat

        self.stdout.write(self.style.SUCCESS('Created Categories'))

        # 3. Income (Regular Salary)
        today = date.today()
        # Income for this month and last
        for i in range(3):
            month_date = today.replace(day=1) - timedelta(days=30 * i)
            Income.objects.create(
                user=user,
                source='💼 Salary',
                amount=50000,
                date=month_date,
                description='Monthly Salary'
            )
        
        self.stdout.write(self.style.SUCCESS('Created Income'))

        # 4. Expenses (The Story)
        
        # Rent (Fixed)
        Expense.objects.create(user=user, category=cat_objs['🏠 Rent'].name, amount=15000, date=today, description='Monthly Rent')

        # Dining Out (The Breach)
        # Budget is 3000. Let's spend 3500 across a few entries.
        Expense.objects.create(user=user, category=cat_objs['🍔 Dining Out'].name, amount=1200, date=today, description='Weekend Dinner')
        Expense.objects.create(user=user, category=cat_objs['🍔 Dining Out'].name, amount=800, date=today - timedelta(days=2), description='Lunch with flow')
        Expense.objects.create(user=user, category=cat_objs['🍔 Dining Out'].name, amount=1600, date=today - timedelta(days=5), description='Treat for friends') # Breaches here

        # Groceries (Safe)
        Expense.objects.create(user=user, category=cat_objs['🥦 Groceries'].name, amount=2000, date=today - timedelta(days=1), description='Weekly stocking')
        Expense.objects.create(user=user, category=cat_objs['🥦 Groceries'].name, amount=1500, date=today - timedelta(days=8), description='Fruits & Veggies')

        self.stdout.write(self.style.SUCCESS('Created Expenses (Story Scenarios)'))

        # 5. Recurring Transactions (Subscriptions)
        
        # Netflix (Renewing Soon - due in ~2 days)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=649,
            description='Netflix Premium',
            category=cat_objs['🎬 Entertainment'].name,
            frequency='MONTHLY',
            start_date=today - timedelta(days=28), 
            last_processed_date=today - timedelta(days=28),
        )

        # Rent (Recurring, matches the expense above)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=15000,
            description='Monthly Rent',
            category=cat_objs['🏠 Rent'].name,
            frequency='MONTHLY',
            start_date=today,
            last_processed_date=today,
        )
        
        # Amazon Prime (Yearly, Safe)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=1499,
            description='Amazon Prime',
            category=cat_objs['🎬 Entertainment'].name,
            frequency='YEARLY',
            start_date=today - timedelta(days=100),
            last_processed_date=today - timedelta(days=100),
        )

        # Gym (Cancelled)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=2000,
            description='Gold\'s Gym',
            category=cat_objs['💊 Health'].name,
            frequency='MONTHLY',
            start_date=today - timedelta(days=200),
            last_processed_date=today - timedelta(days=60),
            is_active=False
        )
        
        # Internet (Safe)
        RecurringTransaction.objects.create(
            user=user,
            transaction_type='EXPENSE',
            amount=1200,
            description='Fiber Internet',
            category=cat_objs['🏠 Rent'].name, # Using Rent category loosely or logic
            frequency='MONTHLY',
            start_date=today - timedelta(days=15),
            last_processed_date=today - timedelta(days=15),
        )

        self.stdout.write(self.style.SUCCESS('Created Recurring Transactions'))
