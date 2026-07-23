import random
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from expenses.models import (
    Account,
    Category,
    Expense,
    Income,
    Notification,
    UserProfile,
)


class Command(BaseCommand):
    help = 'Populates trackmyrupee user with meaningful data for the last 12 months'

    def handle(self, *args, **kwargs):
        # 1. Ensure User 'trackmyrupee' exists and is PRO
        user, created = User.objects.get_or_create(username='trackmyrupee', defaults={
            'email': 'trackmyrupee@example.com',
            'is_active': True
        })
        if created:
            user.set_password('password123')
            user.save()
            self.stdout.write(self.style.SUCCESS("Created user trackmyrupee with password 'password123'"))
        else:
            self.stdout.write(self.style.SUCCESS("User trackmyrupee already exists"))

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.tier = 'PRO'
        profile.is_lifetime = True
        profile.currency = '₹'
        profile.save()
        
        self.stdout.write(self.style.SUCCESS("Set trackmyrupee to PRO tier"))

        # Clear existing data to make it idempotent
        Expense.objects.filter(user=user).delete()
        Income.objects.filter(user=user).delete()
        Account.objects.filter(user=user).delete()
        Category.objects.filter(user=user).delete()
        Notification.objects.filter(user=user).delete()

        # 2. Generate 7 accounts
        accounts_data = [
            {'name': 'Main Bank', 'type': 'CURRENT_ACCOUNT', 'balance': '150000.00'},
            {'name': 'Savings Bank', 'type': 'SAVINGS_ACCOUNT', 'balance': '450000.00'},
            {'name': 'Credit Card', 'type': 'CREDIT_CARD', 'balance': '-25000.00'},
            {'name': 'Fixed Deposit', 'type': 'FD', 'balance': '300000.00'},
            {'name': 'Mutual Funds', 'type': 'MUTUAL_FUND', 'balance': '850000.00'},
            {'name': 'Cash Wallet', 'type': 'CASH_WALLET', 'balance': '5000.00'},
            {'name': 'Emergency Fund', 'type': 'SAVINGS_ACCOUNT', 'balance': '200000.00'},
        ]
        accounts = {}
        for acc in accounts_data:
            account = Account.objects.create(
                user=user,
                name=acc['name'],
                account_type=acc['type'],
                balance=Decimal(acc['balance']),
                currency='₹'
            )
            accounts[acc['name']] = account

        self.stdout.write(self.style.SUCCESS("Created 7 accounts"))

        # 3. Categories
        categories = [
            ('Housing', 'bi-house', 30000),
            ('Groceries', 'bi-cart', 15000),
            ('Utilities', 'bi-lightning', 5000),
            ('Transportation', 'bi-car-front', 8000),
            ('Dining Out', 'bi-cup-hot', 10000),
            ('Entertainment', 'bi-film', 5000),
            ('Health', 'bi-heart-pulse', 4000),
            ('Shopping', 'bi-bag', 12000),
        ]
        for name, icon, limit in categories:
            Category.objects.create(user=user, name=name, icon=icon, limit=limit)

        # 4. Generate 12 months of data
        today = timezone.now().date()
        current_year = today.year
        current_month = today.month
        
        # Seed to make data look nice and slightly varied but reproducible could be good, but random is fine.
        random.seed(42)

        for i in range(13): # 0 to 12 (includes current month)
            # calculate year and month
            total_months = current_month - i - 1
            y = current_year + total_months // 12
            m = total_months % 12 + 1
            
            if m == 12:
                days_in_month = 31
            else:
                days_in_month = (date(y, m+1, 1) - date(y, m, 1)).days

            # Income: Salary (Fixed)
            salary_date = date(y, m, 1)
            Income.objects.create(
                user=user, date=salary_date, amount=Decimal('120000.00'),
                description='Monthly Salary', source='Salary', account=accounts['Main Bank'], currency='₹'
            )

            # Income: Freelance (Random)
            if random.random() > 0.4: # 60% chance
                fl_date = date(y, m, random.randint(10, 25))
                amount = Decimal(random.randint(15000, 40000))
                Income.objects.create(
                    user=user, date=fl_date, amount=amount,
                    description='Freelance Project', source='Freelance', account=accounts['Savings Bank'], currency='₹'
                )
                
            # Expenses
            # Housing (Fixed rent)
            Expense.objects.create(
                user=user, date=date(y, m, 5), amount=Decimal('25000.00'),
                description='Rent', category='Housing', payment_method='NetBanking', account=accounts['Main Bank'], currency='₹'
            )

            # Utilities
            Expense.objects.create(
                user=user, date=date(y, m, 8), amount=Decimal(random.randint(3000, 5000)),
                description='Electricity & Internet', category='Utilities', payment_method='Credit Card', account=accounts['Credit Card'], currency='₹'
            )

            # Groceries (Multiple times)
            for _ in range(4):
                Expense.objects.create(
                    user=user, date=date(y, m, random.randint(1, days_in_month)), amount=Decimal(random.randint(1500, 4000)),
                    description='Supermarket', category='Groceries', payment_method='UPI', account=accounts['Main Bank'], currency='₹'
                )

            # Dining Out
            for _ in range(random.randint(3, 8)):
                Expense.objects.create(
                    user=user, date=date(y, m, random.randint(1, days_in_month)), amount=Decimal(random.randint(800, 2500)),
                    description='Restaurant/Cafe', category='Dining Out', payment_method='Credit Card', account=accounts['Credit Card'], currency='₹'
                )

            # Transportation
            Expense.objects.create(
                user=user, date=date(y, m, random.randint(1, 15)), amount=Decimal(random.randint(2000, 4000)),
                description='Fuel', category='Transportation', payment_method='Credit Card', account=accounts['Credit Card'], currency='₹'
            )
            
            # Entertainment
            Expense.objects.create(
                user=user, date=date(y, m, random.randint(10, 25)), amount=Decimal(random.randint(1000, 3000)),
                description='Movies/Events', category='Entertainment', payment_method='UPI', account=accounts['Main Bank'], currency='₹'
            )
            
            # Shopping
            if random.random() > 0.5:
                Expense.objects.create(
                    user=user, date=date(y, m, random.randint(1, days_in_month)), amount=Decimal(random.randint(2000, 8000)),
                    description='Amazon / Myntra', category='Shopping', payment_method='Credit Card', account=accounts['Credit Card'], currency='₹'
                )

        self.stdout.write(self.style.SUCCESS("Generated 12 months of income and expenses"))

        # 5. Notifications
        Notification.objects.create(
            user=user, title='Milestone Reached! 🎉', message='You have saved ₹5,00,000 in your Emergency Fund.',
            notification_type='MILESTONE', link='/goals/'
        )
        Notification.objects.create(
            user=user, title='Budget Alert: Dining Out ⚠️', message='You have spent 85% of your Dining Out budget this month.',
            notification_type='ANALYTICS', link='/expenses/?category=Dining%20Out'
        )
        Notification.objects.create(
            user=user, title='Upcoming Recurring Payment 💳', message='Netflix Subscription (₹649) is due tomorrow.',
            notification_type='RECURRING', link='/expenses/'
        )
        Notification.objects.create(
            user=user, title='Welcome to TrackMyRupee Pro! 🚀', message='You now have access to premium features like AI Insights and advanced charts.',
            notification_type='SYSTEM', link='/pricing/'
        )

        self.stdout.write(self.style.SUCCESS("Created 4 notifications"))
        self.stdout.write(self.style.SUCCESS("Successfully populated trackmyrupee user!"))
