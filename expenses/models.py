from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from datetime import timedelta
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.db import transaction
from .utils import get_exchange_rate

CURRENCY_CHOICES = [
    ('₹', _('Indian Rupee (₹)')),
    ('$', _('US Dollar ($)')),
    ('€', _('Euro (€)')),
    ('£', _('Pound Sterling (£)')),
    ('¥', _('Japanese Yen (¥)')),
    ('A$', _('Australian Dollar (A$)')),
    ('C$', _('Canadian Dollar (C$)')),
    ('CHF', _('Swiss Franc (CHF)')),
    ('元', _('Chinese Yuan (元)')),
    ('₩', _('South Korean Won (₩)')),
]

class Account(models.Model):
    ACCOUNT_TYPES = [
        ('CASH', _('Cash')),
        ('BANK', _('Bank Account')),
        ('CREDIT_CARD', _('Credit Card')),
        ('INVESTMENT', _('Investment Account')),
        ('OTHER', _('Other')),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='accounts')
    name = models.CharField(max_length=100, verbose_name=_('Account Name'))
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPES, default='BANK', verbose_name=_('Account Type'))
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name=_('Current Balance'))
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='unique_account_per_user')
        ]

    def __str__(self):
        return f"{self.name} ({self.currency}{self.balance})"

class Expense(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField(verbose_name=_('Date'))
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(verbose_name=_('Description'))
    category = models.CharField(max_length=255, verbose_name=_('Category'))
    
    PAYMENT_OPTIONS = [
        ('Cash', _('Cash')),
        ('Credit Card', _('Credit Card')),
        ('Debit Card', _('Debit Card')),
        ('UPI', _('UPI')),
        ('NetBanking', _('NetBanking')),
    ]
    payment_method = models.CharField(max_length=50, choices=PAYMENT_OPTIONS, default='Cash', verbose_name=_('Payment Method'))
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses', verbose_name=_('Account'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Handle balance reversal for updates
            if self.pk:
                old_instance = Expense.objects.get(pk=self.pk)
                if old_instance.account:
                    old_instance.account.balance += old_instance.amount
                    old_instance.account.save()

            if self.category:
                self.category = self.category.strip()
            
            # Multi-currency normalization
            base_currency = self.user.profile.currency
            if self.currency == base_currency:
                self.exchange_rate = Decimal('1.0')
                self.base_amount = self.amount
            else:
                self.exchange_rate = get_exchange_rate(self.currency, base_currency)
                self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))
                
            super().save(*args, **kwargs)
            
            # Apply new balance
            if self.account:
                self.account.refresh_from_db()
                self.account.balance -= self.amount
                self.account.save()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.account:
                self.account.balance += self.amount
                self.account.save()
            super().delete(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'date', 'amount', 'currency', 'description', 'category'],
                name='unique_expense'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'category']),
            models.Index(fields=['user', 'payment_method']),
            models.Index(fields=['user', 'date']),
        ]

    def __str__(self):
        return f"{self.date} - {self.description} - {self.amount}"

class Category(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255, verbose_name=_('Category Name'))
    icon = models.CharField(max_length=50, default='bi-tag', verbose_name=_('Icon'))
    limit = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name=_('Monthly Limit'))

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()
        super().save(*args, **kwargs)

    class Meta:
        verbose_name_plural = 'Categories'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                name='unique_category'
            )
        ]

    def __str__(self):
        return self.name

class Income(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField(verbose_name=_('Date'))
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(blank=True, null=True, verbose_name=_('Description'))
    source = models.CharField(max_length=255, verbose_name=_('Source')) # e.g. Salary, Freelance, Dividend
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='incomes', verbose_name=_('Account'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Handle balance reversal for updates
            if self.pk:
                old_instance = Income.objects.get(pk=self.pk)
                if old_instance.account:
                    old_instance.account.balance -= old_instance.amount
                    old_instance.account.save()

            if self.source:
                self.source = self.source.strip()
                
            # Multi-currency normalization
            base_currency = self.user.profile.currency
            if self.currency == base_currency:
                self.exchange_rate = Decimal('1.0')
                self.base_amount = self.amount
            else:
                self.exchange_rate = get_exchange_rate(self.currency, base_currency)
                self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))

            super().save(*args, **kwargs)

            # Apply new balance
            if self.account:
                self.account.refresh_from_db()
                self.account.balance += self.amount
                self.account.save()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.account:
                self.account.balance -= self.amount
                self.account.save()
            super().delete(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'date', 'amount', 'currency', 'source'],
                name='unique_income'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'source']),
            models.Index(fields=['user', 'date']),
        ]

    def __str__(self):
        return f"{self.date} - {self.source} - {self.amount}"

class Transfer(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    from_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transfers_out', verbose_name=_('From Account'))
    to_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transfers_in', verbose_name=_('To Account'))
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_('Amount'))
    date = models.DateField(default=timezone.now, verbose_name=_('Date'))
    description = models.TextField(blank=True, null=True, verbose_name=_('Description'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            # Revert-and-Apply pattern for updates
            if self.pk:
                old_instance = Transfer.objects.get(pk=self.pk)
                # Revert old
                old_instance.from_account.balance += old_instance.amount
                old_instance.from_account.save()
                old_instance.to_account.balance -= old_instance.amount
                old_instance.to_account.save()

            super().save(*args, **kwargs)

            # Apply new
            # Refresh accounts to get reverted balances
            self.from_account.refresh_from_db()
            self.to_account.refresh_from_db()
            
            self.from_account.balance -= self.amount
            self.from_account.save()
            self.to_account.balance += self.amount
            self.to_account.save()

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            self.from_account.balance += self.amount
            self.from_account.save()
            self.to_account.balance -= self.amount
            self.to_account.save()
            super().delete(*args, **kwargs)

    def __str__(self):
        return f"{self.date} - Transfer {self.amount} from {self.from_account.name} to {self.to_account.name}"

class RecurringTransaction(models.Model):
    FREQUENCY_CHOICES = [
        ('DAILY', _('Daily')),
        ('WEEKLY', _('Weekly')),
        ('MONTHLY', _('Monthly')),
        ('YEARLY', _('Yearly')),
    ]
    TRANSACTION_TYPE_CHOICES = [
        ('EXPENSE', _('Expense')),
        ('INCOME', _('Income')),
        ('TRANSFER', _('Transfer')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES, verbose_name=_('Transaction Type'))
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(verbose_name=_('Description'))
    category = models.CharField(max_length=255, blank=True, null=True, verbose_name=_('Category'))
    source = models.CharField(max_length=255, blank=True, null=True, verbose_name=_('Source'))
    
    payment_method = models.CharField(max_length=50, choices=Expense.PAYMENT_OPTIONS, default='Cash', verbose_name=_('Payment Method'))
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Account'))

    # Transfer-specific fields
    from_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transfers_out', verbose_name=_('From Account'))
    to_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transfers_in', verbose_name=_('To Account'))

    frequency = models.CharField(max_length=10, choices=FREQUENCY_CHOICES, verbose_name=_('Frequency'))
    start_date = models.DateField(verbose_name=_('Start Date'))
    last_processed_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @staticmethod
    def get_next_date(current_date, frequency):
        if frequency == 'DAILY':
            return current_date + timedelta(days=1)
        elif frequency == 'WEEKLY':
            return current_date + timedelta(weeks=1)
        elif frequency == 'MONTHLY':
            month = current_date.month % 12 + 1
            year = current_date.year + (current_date.month // 12)
            try:
                return current_date.replace(year=year, month=month)
            except ValueError:
                # Handle Feb 29/30/31
                next_month = current_date + timedelta(days=31)
                return next_month.replace(day=1) - timedelta(days=1)
        elif frequency == 'YEARLY':
            try:
                return current_date.replace(year=current_date.year + 1)
            except ValueError:
                return current_date.replace(year=current_date.year + 1, month=2, day=28)
        return current_date + timedelta(days=365)

    @property
    def next_due_date(self):
        if not self.last_processed_date or self.last_processed_date < self.start_date:
            return self.start_date
        return self.get_next_date(self.last_processed_date, self.frequency)

    def save(self, *args, **kwargs):
        # Multi-currency normalization
        base_currency = self.user.profile.currency
        if self.currency == base_currency:
            self.exchange_rate = Decimal('1.0')
            self.base_amount = self.amount
        else:
            self.exchange_rate = get_exchange_rate(self.currency, base_currency)
            self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))
            
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'transaction_type', 'amount', 'currency', 'description', 'frequency', 'start_date'],
                name='unique_recurring_transaction'
            )
        ]

    def __str__(self):
        return f"{self.transaction_type} - {self.description} ({self.frequency})"
        
class UserProfile(models.Model):
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('hi', 'Hindi'),
        ('mr', 'Marathi'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹')
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default='en')
    has_seen_tutorial = models.BooleanField(default=False)

    # Subscription Fields
    TIER_CHOICES = [
        ('FREE', 'Free'),
        ('PLUS', 'Plus'),
        ('PRO', 'Pro'),
    ]
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default='FREE')
    subscription_end_date = models.DateTimeField(null=True, blank=True)
    is_lifetime = models.BooleanField(default=False)
    razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
    has_used_trial = models.BooleanField(default=False)

    # Lifecycle email drip tracking
    last_drip_email_day = models.IntegerField(default=0)
    expiry_reminder_sent = models.BooleanField(default=False)

    @property
    def is_pro(self):
        """Check if user has active Pro access (either lifetime or valid subscription)."""
        if self.tier == 'PRO':
            if self.is_lifetime:
                return True
            if not self.subscription_end_date:
                return True
            if self.subscription_end_date > timezone.now():
                return True
        return False
    
    @property
    def is_plus(self):
        """Check if user has active Plus access (or higher)."""
        if self.tier in ['PLUS', 'PRO']:
            if self.is_lifetime:
                return True
            if not self.subscription_end_date:
                return True # Assume active if no end date set manually
            if self.subscription_end_date > timezone.now():
                return True
        return False

    @property
    def active_tier(self):
        """Returns the actual active tier string (respecting subscription expiry)."""
        if self.is_pro:
            return 'PRO'
        if self.is_plus:
            return 'PLUS'
        return 'FREE'

    @property
    def active_tier_display(self):
        """Returns the actual active tier display name (respecting subscription expiry)."""
        tier = self.active_tier
        return dict(self.TIER_CHOICES).get(tier, 'Free')

    @property
    def subscription_expired(self):
        """Check if the user was on a paid tier but it has now expired."""
        if self.tier in ['PRO', 'PLUS'] and self.active_tier == 'FREE':
            if self.subscription_end_date and self.subscription_end_date < timezone.now():
                return True
        return False

    @property
    def last_tier_display(self):
        """Returns the display name of the tier the user was on before expiry."""
        return dict(self.TIER_CHOICES).get(self.tier, 'Pro')

    @property
    def can_start_trial(self):
        """Check if user is eligible to start a free 7-day Pro trial."""
        return self.tier == 'FREE' and not self.has_used_trial

    def __str__(self):
        return f"{self.user.username}'s Profile ({self.tier})"

class PaymentHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    order_id = models.CharField(max_length=100)
    payment_id = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    tier = models.CharField(max_length=10) # PLUS, PRO
    duration = models.CharField(max_length=10, default='YEARLY')  # MONTHLY, YEARLY
    status = models.CharField(max_length=20, default='PENDING') # PENDING, SUCCESS, FAILED
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.tier} ({self.duration}) - {self.status}"

class SubscriptionPlan(models.Model):
    DURATION_CHOICES = [
        ('MONTHLY', 'Monthly'),
        ('YEARLY', 'Yearly'),
    ]
    TIER_CHOICES = [
        ('PLUS', 'Plus'),
        ('PRO', 'Pro'),
    ]
    tier = models.CharField(max_length=10, choices=TIER_CHOICES)
    duration = models.CharField(max_length=10, choices=DURATION_CHOICES, default='YEARLY')
    name = models.CharField(max_length=100)
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price in INR")
    features = models.TextField(help_text="Comma separated features", blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['tier', 'duration'],
                name='unique_plan_tier_duration'
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.get_duration_display()}) - ₹{self.price}"

class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Optional link to the transaction that triggered it
    related_transaction = models.ForeignKey('RecurringTransaction', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.title}"

class SavingsGoal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='savings_goals')
    name = models.CharField(max_length=255, verbose_name=_('Goal Name'))
    target_amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_('Target Amount'))
    current_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'), verbose_name=_('Current Amount'))
    target_date = models.DateField(blank=True, null=True, verbose_name=_('Target Date'))
    icon = models.CharField(max_length=10, default='🎯', verbose_name=_('Icon'))
    color = models.CharField(max_length=20, default='primary', verbose_name=_('Color Theme'))
    is_completed = models.BooleanField(default=False)
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def progress_percentage(self):
        if self.target_amount > 0:
            percentage = (self.current_amount / self.target_amount) * 100
            if percentage > 100:
                return 100
            return round(percentage, 1)
        return 0
        
    def save(self, *args, **kwargs):
        if self.current_amount >= self.target_amount and self.target_amount > 0:
            self.is_completed = True
        else:
            self.is_completed = False
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class GoalContribution(models.Model):
    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name='contributions')
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name=_('Contribution Amount'))
    date = models.DateField(default=timezone.now, verbose_name=_('Date'))
    
    # Link to the generated Expense to keep them in sync
    expense = models.OneToOneField('Expense', on_delete=models.SET_NULL, null=True, blank=True, related_name='goal_contribution')
    
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        super().save(*args, **kwargs)
        
        # Update goal's current amount
        if is_new:
            self.goal.current_amount += self.amount
            self.goal.save()
            
            # Create the matching Expense record
            expense = Expense.objects.create(
                user=self.goal.user,
                date=self.date,
                amount=self.amount,
                currency=self.goal.currency,
                description=f"Contribution to Savings Goal: {self.goal.name}",
                category="Savings Transfer",
                payment_method='Cash' # default for internal
            )
            # Link them without triggering infinite save loop
            GoalContribution.objects.filter(pk=self.pk).update(expense=expense)
            self.expense = expense
            
    def delete(self, *args, **kwargs):
        # Update goal's current amount when deleting a contribution
        self.goal.current_amount -= self.amount
        self.goal.save()
        
        # Delete the linked expense if it exists
        if self.expense:
            self.expense.delete()
            
        super().delete(*args, **kwargs)

    def __str__(self):
        return f"+{self.amount} to {self.goal.name} on {self.date}"
