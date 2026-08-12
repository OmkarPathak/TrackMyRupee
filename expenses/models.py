import logging
import uuid
from datetime import date, timedelta
from decimal import Decimal

import sentry_sdk
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.postgres.indexes import GinIndex
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from finance_tracker.plans import get_limit

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

CAPITAL_SUBTYPE_CHOICES = [
    ('loan_down_payment', _('Loan Down Payment')),
    ('loan_prepayment', _('Loan Prepayment')),
    ('large_purchase', _('Large Purchase')),
    ('medical_lump_sum', _('Medical Lump Sum')),
    ('gift_given', _('Gift Given')),
    ('gift_received', _('Gift Received')),
    ('investment_lump_sum', _('Investment Lump Sum')),
    ('other', _('Other')),
]

logger = logging.getLogger(__name__)


def _build_ledger_version(instance, action):
    action_prefix = (action or 'OP').upper()
    updated_at = getattr(instance, 'updated_at', None)
    created_at = getattr(instance, 'created_at', None)
    if action_prefix == 'CREATE':
        ts = updated_at or created_at or timezone.now()
    else:
        # For models without updated_at, use current time for mutable operations
        # so idempotency keys don't collide across multiple updates/deletes.
        ts = updated_at or timezone.now()
    return f"{action_prefix}-{int(ts.timestamp() * 1000000)}"


def _run_ledger_shadow(posting_fn, source_type=None, source_id=None, action=None, payload=None):
    if not getattr(settings, 'LEDGER_WRITE_ENABLED', False):
        return
    try:
        posting_fn()
    except Exception as exc:
        error_code = getattr(exc, 'code', 'UNKNOWN_LEDGER_ERR')
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("error_code", error_code)
            logger.exception('Ledger shadow posting failed.')
            
        LedgerPostingFailure.objects.create(
            source_type=source_type or 'ADJUSTMENT',
            source_id=source_id or 0,
            action=action or 'UNKNOWN',
            payload=payload or {},
            error_message=str(exc),
            status='PENDING',
            next_retry_at=timezone.now(),
        )
        if getattr(settings, 'LEDGER_ENFORCE_BALANCED_WRITE', False):
            raise ValidationError(_('Unable to save transaction right now. Please try again.'))

class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)

class FinanceBaseManager(models.Manager):
    def get_monthly_summary(self, user, year, month):
        return self.filter(
            user=user, 
            date__year=year, 
            date__month=month
        ).aggregate(
            total=models.Sum('base_amount'),
            count=models.Count('id')
        )

class ExpenseManager(FinanceBaseManager):
    def get_queryset(self):
        return super().get_queryset().select_related('account')

    def get_category_breakdown(self, user, year, month):
        return self.filter(
            user=user, 
            date__year=year, 
            date__month=month
        ).values('category').annotate(
            total=models.Sum('base_amount')
        ).order_by('-total')

class IncomeManager(FinanceBaseManager):
    def get_queryset(self):
        return super().get_queryset().select_related('account')


class TransferManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related('from_account', 'to_account')


class GoalContributionManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related('goal', 'account')


class Account(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    # Grouped account types — imported from account_types.py (single source of truth).
    # Legacy codes (CASH, BANK, CREDIT_CARD, INVESTMENT, FIXED_DEPOSIT, OTHER) are
    # retained in the 'Legacy' group so existing rows validate and forms keep working.
    from .account_types import (
        ACCOUNT_TYPES,  # noqa: E402 (class-level import is intentional)
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='accounts')
    name = models.CharField(max_length=100, verbose_name=_('Account Name'))
    # max_length widened from 20 → 32 (longest new code is 15 chars; safe Postgres metadata-only alter)
    account_type = models.CharField(
        max_length=32,
        choices=ACCOUNT_TYPES,
        default='SAVINGS_ACCOUNT',
        verbose_name=_('Account Type'),
    )
    balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        verbose_name=_('Current Balance'),
    )
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Optional FK links for LOAN_OUTSTANDING / PHYSICAL_VALUATION / INSURANCE_SURRENDER ──
    # Nullable by default → falls back to ledger balance, so existing accounts are unaffected.
    linked_loan = models.ForeignKey(
        'Loan',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='linked_accounts',
        verbose_name=_('Linked Loan'),
        help_text=_('Link to a Loan record for LOAN_OUTSTANDING valuation strategy.'),
    )
    linked_physical_asset = models.ForeignKey(
        'PhysicalAsset',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='linked_accounts',
        verbose_name=_('Linked Physical Asset'),
        help_text=_('Link to a PhysicalAsset for PHYSICAL_VALUATION / INSURANCE_SURRENDER strategy.'),
    )

    # ── Optional DEPOSIT accrual fields (all nullable) ───────────────────────────────────────
    # When set, the DEPOSIT strategy computes principal*(1+r)^t-style accrual.
    # When unset (default), DEPOSIT == ledger balance (fully backward compatible).
    COMPOUNDING_CHOICES = [
        ('SIMPLE', _('Simple Interest')),
        ('QUARTERLY', _('Quarterly Compounding')),
        ('MONTHLY', _('Monthly Compounding')),
        ('ANNUAL', _('Annual Compounding')),
    ]
    deposit_principal = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True,
        verbose_name=_('Deposit Principal'),
    )
    deposit_rate = models.DecimalField(
        max_digits=7, decimal_places=4,
        null=True, blank=True,
        verbose_name=_('Annual Interest Rate (%)'),
    )
    deposit_start_date = models.DateField(
        null=True, blank=True,
        verbose_name=_('Deposit Start Date'),
    )
    deposit_compounding = models.CharField(
        max_length=10,
        choices=COMPOUNDING_CHOICES,
        null=True, blank=True,
        verbose_name=_('Compounding Frequency'),
    )
    deposit_maturity_date = models.DateField(
        null=True, blank=True,
        verbose_name=_('Deposit Maturity Date'),
    )
    show_accrued_balance = models.BooleanField(
        default=True,
        verbose_name=_('Show Accrued Balance'),
        help_text=_('If enabled, the dashboard and account lists will display the projected accrued balance instead of the ledger balance.')
    )
    rd_installment_amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True,
        verbose_name=_('RD Installment Amount'),
    )
    rd_installment_day = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(28)],
        verbose_name=_('RD Installment Day'),
    )
    deposit_closed_date = models.DateField(
        null=True, blank=True,
        verbose_name=_('Deposit Closed Date'),
    )
    record_maturity_income = models.BooleanField(
        default=False,
        verbose_name=_('Record Interest Earned as Income'),
        help_text=_('If enabled, accrued interest is recorded as Income under Investment Returns.')
    )
    credit_limit = models.DecimalField(
        max_digits=15, decimal_places=2,
        null=True, blank=True,
        verbose_name=_('Credit Limit'),
    )

    @property
    def account_type_display(self) -> str:
        return self.get_account_type_display()

    @property
    def has_credit_limit(self) -> bool:
        return self.credit_limit is not None and self.credit_limit > Decimal('0.00')

    @property
    def used_credit(self) -> Decimal:
        """Amount of credit used (positive decimal balance)."""
        bal = getattr(self, 'display_balance', self.balance)
        if bal and bal < Decimal('0.00'):
            return abs(bal)
        return Decimal('0.00')

    @property
    def available_credit(self) -> Decimal:
        """Remaining available credit limit."""
        if not self.has_credit_limit:
            return Decimal('0.00')
        return max(Decimal('0.00'), self.credit_limit - self.used_credit)

    @property
    def credit_utilization_pct(self) -> Decimal:
        """Credit limit utilization percentage (0-100+)."""
        if not self.has_credit_limit:
            return Decimal('0.00')
        return ((self.used_credit / self.credit_limit) * Decimal('100')).quantize(Decimal('0.1'))

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                condition=models.Q(is_active=True),
                name='unique_account_per_user'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'account_type', 'is_active'], name='acc_user_type_active_idx'),
        ]

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for expense in self.expenses.all():
                expense.delete()
            for income in self.incomes.all():
                income.delete()
            for transfer in self.transfers_out.all():
                transfer.delete()
            for transfer in self.transfers_in.all():
                transfer.delete()
            for contribution in self.goal_contributions.all():
                contribution.delete()
            for repayment in self.loan_repayments.all():
                repayment.delete()
            super().delete(*args, **kwargs)

    @property
    def formatted_balance(self) -> str:
        from .utils import format_indian_number
        if str(self.currency).upper() in ['INR', '₹']:
            return format_indian_number(self.balance)
        return f"{float(self.balance):,.2f}"

    def __str__(self):
        return f"{self.name} ({self.currency}{self.formatted_balance})"


class LedgerAccount(models.Model):
    ACCOUNT_TYPE_CHOICES = [
        ('ASSET', _('Asset')),
        ('LIABILITY', _('Liability')),
        ('INCOME', _('Income')),
        ('EXPENSE', _('Expense')),
        ('EQUITY', _('Equity')),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='ledger_accounts',
    )
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=150)
    account_type = models.CharField(max_length=20, choices=ACCOUNT_TYPE_CHOICES)
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, null=True, blank=True)
    cached_balance = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        verbose_name=_('Cached Balance'),
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'account_type']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class JournalEntry(models.Model):
    SOURCE_TYPE_CHOICES = [
        ('EXPENSE', _('Expense')),
        ('INCOME', _('Income')),
        ('TRANSFER', _('Transfer')),
        ('LOAN_REPAYMENT', _('Loan Repayment')),
        ('GOAL_CONTRIBUTION', _('Goal Contribution')),
        ('ADJUSTMENT', _('Adjustment')),
        ('CAPITAL_EVENT', _('Capital Event')),
    ]
    STATUS_CHOICES = [
        ('POSTED', _('Posted')),
        ('REVERSED', _('Reversed')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='journal_entries')
    posted_at = models.DateTimeField(default=timezone.now)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES)
    source_id = models.BigIntegerField()
    idempotency_key = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='POSTED')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'posted_at']),
            models.Index(fields=['source_type', 'source_id']),
            models.Index(fields=['status']),
            models.Index(fields=['user', 'source_type', 'status']),
            GinIndex(fields=['metadata'], name='journalentry_metadata_gin'),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} ({self.status})"


class JournalLine(models.Model):
    DIRECTION_CHOICES = [
        ('DEBIT', _('Debit')),
        ('CREDIT', _('Credit')),
    ]

    journal_entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    ledger_account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name='journal_lines')
    direction = models.CharField(max_length=10, choices=DIRECTION_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES)
    fx_rate_to_base = models.DecimalField(max_digits=15, decimal_places=6, default=Decimal('1.0'))
    base_amount = models.DecimalField(max_digits=15, decimal_places=2)
    account_ref = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='journal_lines',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['ledger_account', 'journal_entry']),
            models.Index(fields=['account_ref']),
        ]

    def __str__(self):
        return f"{self.direction} {self.amount} {self.currency}"


class LedgerPostingFailure(models.Model):
    STATUS_CHOICES = [
        ('PENDING', _('Pending')),
        ('RETRYING', _('Retrying')),
        ('RESOLVED', _('Resolved')),
        ('FAILED', _('Failed')),
    ]

    source_type = models.CharField(max_length=30, choices=JournalEntry.SOURCE_TYPE_CHOICES)
    source_id = models.BigIntegerField()
    action = models.CharField(max_length=30)
    payload = models.JSONField(default=dict, blank=True)
    error_message = models.TextField()
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=5)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'next_retry_at']),
            models.Index(fields=['source_type', 'source_id']),
        ]

    def __str__(self):
        return f"{self.source_type}:{self.source_id} {self.action} ({self.status})"


class LedgerReconciliationReport(models.Model):
    STATUS_CHOICES = [
        ('MATCH', _('Match')),
        ('DRIFT', _('Drift')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ledger_reconciliation_reports')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='ledger_reconciliation_reports')
    as_of_date = models.DateField()
    account_balance = models.DecimalField(max_digits=15, decimal_places=2)
    ledger_balance = models.DecimalField(max_digits=15, decimal_places=2)
    drift_amount = models.DecimalField(max_digits=15, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'as_of_date']),
            models.Index(fields=['account', 'as_of_date']),
            models.Index(fields=['status']),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.account_id}:{self.as_of_date} ({self.status})"

class Expense(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField(verbose_name=_('Date'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(verbose_name=_('Description'))
    category = models.CharField(max_length=255, verbose_name=_('Category'))
    category_fk = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses', verbose_name=_('Category FK'))
    client_dedup_key = models.CharField(max_length=255, null=True, blank=True, verbose_name=_('Client Deduplication Key'))

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
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses', verbose_name=_('Account'))
    linked_physical_asset = models.ForeignKey('PhysicalAsset', on_delete=models.SET_NULL, null=True, blank=True, related_name='expenses', verbose_name=_('Linked Physical Asset'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = ExpenseManager()
    active = SoftDeleteManager()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old_instance = None
            # Handle balance reversal for updates
            if self.pk:
                old_instance = Expense.objects.select_related(None).select_for_update().get(pk=self.pk)
                if old_instance.account_id:
                    old_account = Account.objects.select_for_update().get(pk=old_instance.account_id)
                    # Convert old amount to account currency for reversal
                    reversal_amount = old_instance.amount
                    if old_instance.currency != old_account.currency:
                        rate = get_exchange_rate(old_instance.currency, old_account.currency)
                        reversal_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                    
                    old_account.balance += reversal_amount
                    old_account.save(update_fields=['balance', 'updated_at'])

            if getattr(self, 'category_fk', None):
                self.category = self.category_fk.name
            elif self.category:
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
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert current amount to account currency
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
                locked_account.balance -= apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_expense_create(
                        expense=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_expense_update(
                        expense=self,
                        previous_expense=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='EXPENSE',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'expense_create' if old_instance is None else 'expense_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'expense': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'category': self.category,
                        'description': self.description,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                    'previous_expense': {
                        'user_id': old_instance.user_id,
                        'amount': str(old_instance.amount),
                        'currency': old_instance.currency,
                        'category': old_instance.category,
                        'description': old_instance.description,
                        'account_id': old_instance.account_id,
                        'source_id': old_instance.id,
                    } if old_instance else None,
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.user,
                model_name='Expense',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount), 'category': old_instance.category} if old_instance else None,
                    'after': {'amount': str(self.amount), 'category': self.category}
                }
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.account:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert to account currency for deletion reversal
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
                locked_account.balance += apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_expense_delete(
                    expense=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='EXPENSE',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'expense_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'expense': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'category': self.category,
                        'description': self.description,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                },
            )
            
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])
                
                FinancialAuditLog.objects.create(
                    user=self.user,
                    model_name='Expense',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'client_dedup_key'], name='idx_expense_client_dedup', condition=models.Q(client_dedup_key__isnull=False)),
        ]
        indexes = [
            models.Index(fields=['user', 'category']),
            models.Index(fields=['user', 'payment_method']),
            models.Index(fields=['user', 'date']),
            models.Index(fields=['user', 'account']),
            models.Index(fields=['linked_physical_asset']),
        ]

    def __str__(self):
        return f"{self.date} - {self.description} - {self.amount}"

class Category(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=255, verbose_name=_('Category Name'))
    icon = models.CharField(max_length=50, default='bi-tag', verbose_name=_('Icon'))
    limit = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name=_('Monthly Limit'))
    is_interest_category = models.BooleanField(default=False, verbose_name=_('Is Interest Category'))

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
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    SOURCE_TYPE_CHOICES = [
        ('Salary', _('Salary')),
        ('Freelance / Consulting', _('Freelance / Consulting')),
        ('Business', _('Business')),
        ('Investment Returns', _('Investment Returns')),
        ('Rental Income', _('Rental Income')),
        ('Cashback & Rewards', _('Cashback & Rewards')),
        ('Refund / Reimbursement', _('Refund / Reimbursement')),
        ('Other', _('Other')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField(verbose_name=_('Date'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(blank=True, null=True, verbose_name=_('Description'))
    source = models.CharField(max_length=255, verbose_name=_('Source')) # e.g. Salary, Freelance, Dividend
    source_type = models.CharField(
        max_length=50,
        choices=SOURCE_TYPE_CHOICES,
        default='Salary',
        verbose_name=_('Source Type')
    )
    source_fk = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='incomes', verbose_name=_('Source FK'))
    client_dedup_key = models.CharField(max_length=255, null=True, blank=True, verbose_name=_('Client Deduplication Key'))
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='incomes', verbose_name=_('Account'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = IncomeManager()
    active = SoftDeleteManager()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old_instance = None
            # Handle balance reversal for updates
            if self.pk:
                old_instance = Income.objects.select_related(None).select_for_update().get(pk=self.pk)
                if old_instance.account_id:
                    old_account = Account.objects.select_for_update().get(pk=old_instance.account_id)
                    # Convert to account currency for reversal
                    reversal_amount = old_instance.amount
                    if old_instance.currency != old_account.currency:
                        rate = get_exchange_rate(old_instance.currency, old_account.currency)
                        reversal_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                    
                    old_account.balance -= reversal_amount
                    old_account.save(update_fields=['balance', 'updated_at'])

            if not self.source:
                self.source = self.source_type or 'Salary'
            if getattr(self, 'source_fk', None):
                self.source = self.source_fk.name
            elif self.source:
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
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert to account currency
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                    
                locked_account.balance += apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_income_create(
                        income=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_income_update(
                        income=self,
                        previous_income=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='INCOME',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'income_create' if old_instance is None else 'income_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'income': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'source': self.source,
                        'description': self.description,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                    'previous_income': {
                        'user_id': old_instance.user_id,
                        'amount': str(old_instance.amount),
                        'currency': old_instance.currency,
                        'source': old_instance.source,
                        'description': old_instance.description,
                        'account_id': old_instance.account_id,
                        'source_id': old_instance.id,
                    } if old_instance else None,
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.user,
                model_name='Income',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount), 'source': old_instance.source} if old_instance else None,
                    'after': {'amount': str(self.amount), 'source': self.source}
                }
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.account:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert to account currency for deletion reversal
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
                locked_account.balance -= apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_income_delete(
                    income=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='INCOME',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'income_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'income': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'source': self.source,
                        'description': self.description,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                },
            )
            
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])
                
                FinancialAuditLog.objects.create(
                    user=self.user,
                    model_name='Income',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'client_dedup_key'], name='idx_income_client_dedup', condition=models.Q(client_dedup_key__isnull=False)),
        ]
        indexes = [
            models.Index(fields=['user', 'source']),
            models.Index(fields=['user', 'date']),
            models.Index(fields=['user', 'account']),
        ]

    def __str__(self):
        return f"{self.date} - {self.source} - {self.amount}"

class Transfer(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    from_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transfers_out', verbose_name=_('From Account'))
    to_account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='transfers_in', verbose_name=_('To Account'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Amount'))
    date = models.DateField(default=timezone.now, verbose_name=_('Date'))
    description = models.TextField(blank=True, null=True, verbose_name=_('Description'))
    client_dedup_key = models.CharField(max_length=255, null=True, blank=True, verbose_name=_('Client Deduplication Key'))
    
    objects = TransferManager()
    
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    converted_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    @property
    def base_amount(self):
        return self.converted_amount

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = TransferManager()
    active = SoftDeleteManager()

    def clean(self):
        if self.from_account_id and self.to_account_id and self.from_account_id == self.to_account_id:
            raise ValidationError({'to_account': _('Source and destination accounts must be different.')})

        if self.user_id and self.from_account and self.from_account.user_id != self.user_id:
            raise ValidationError({'from_account': _('From account must belong to the current user.')})

        if self.user_id and self.to_account and self.to_account.user_id != self.user_id:
            raise ValidationError({'to_account': _('To account must belong to the current user.')})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            old_instance = None
            # Revert-and-Apply pattern for updates
            if self.pk:
                old_instance = Transfer.objects.select_related('from_account', 'to_account').select_for_update().get(pk=self.pk)
                old_from_account = Account.objects.select_for_update().get(pk=old_instance.from_account_id)
                old_to_account = Account.objects.select_for_update().get(pk=old_instance.to_account_id)
                # Revert old
                old_from_account.balance += old_instance.amount
                old_from_account.save(update_fields=['balance', 'updated_at'])
                
                # Convert from_account's amount to to_account's currency for reversal
                reversal_to_amount = old_instance.amount
                if old_instance.from_account.currency != old_instance.to_account.currency:
                    rate = get_exchange_rate(old_instance.from_account.currency, old_instance.to_account.currency)
                    reversal_to_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                
                old_to_account.balance -= reversal_to_amount
                old_to_account.save(update_fields=['balance', 'updated_at'])

            # Multi-currency normalization (Transfers use the currency of the from_account usually)
            currency = self.from_account.currency
                
            base_currency = self.user.profile.currency
            if currency == base_currency:
                self.exchange_rate = Decimal('1.0')
                self.converted_amount = self.amount
            else:
                self.exchange_rate = get_exchange_rate(currency, base_currency)
                self.converted_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))

            super().save(*args, **kwargs)

            # Apply new
            from_account = Account.objects.select_for_update().get(pk=self.from_account_id)
            to_account = Account.objects.select_for_update().get(pk=self.to_account_id)
            
            from_account.balance -= self.amount # amount is in from_account currency
            from_account.save(update_fields=['balance', 'updated_at'])
            
            # Convert to to_account currency
            to_apply_amount = self.amount
            if from_account.currency != to_account.currency:
                rate = get_exchange_rate(from_account.currency, to_account.currency)
                to_apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
            to_account.balance += to_apply_amount
            to_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_transfer_create(
                        transfer=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_transfer_update(
                        transfer=self,
                        previous_transfer=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='TRANSFER',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'transfer_create' if old_instance is None else 'transfer_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'transfer': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'description': self.description,
                        'from_account_id': self.from_account_id,
                        'to_account_id': self.to_account_id,
                        'source_id': self.id,
                    },
                    'previous_transfer': {
                        'user_id': old_instance.user_id,
                        'amount': str(old_instance.amount),
                        'description': old_instance.description,
                        'from_account_id': old_instance.from_account_id,
                        'to_account_id': old_instance.to_account_id,
                        'source_id': old_instance.id,
                    } if old_instance else None,
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.user,
                model_name='Transfer',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount)} if old_instance else None,
                    'after': {'amount': str(self.amount)}
                }
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            from_account = Account.objects.select_for_update().get(pk=self.from_account_id)
            to_account = Account.objects.select_for_update().get(pk=self.to_account_id)
            from_account.balance += self.amount
            from_account.save(update_fields=['balance', 'updated_at'])
            
            # Convert for reversal
            to_revert_amount = self.amount
            if from_account.currency != to_account.currency:
                rate = get_exchange_rate(from_account.currency, to_account.currency)
                to_revert_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
            to_account.balance -= to_revert_amount
            to_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_transfer_delete(
                    transfer=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='TRANSFER',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'transfer_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'transfer': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'description': self.description,
                        'from_account_id': self.from_account_id,
                        'to_account_id': self.to_account_id,
                        'source_id': self.id,
                    },
                },
            )
            
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])
                
                FinancialAuditLog.objects.create(
                    user=self.user,
                    model_name='Transfer',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~models.Q(from_account=models.F('to_account')),
                name='transfer_accounts_must_differ',
            ),
            models.UniqueConstraint(fields=['user', 'client_dedup_key'], name='idx_transfer_client_dedup', condition=models.Q(client_dedup_key__isnull=False)),
        ]
        indexes = [
            models.Index(fields=['user', 'date']),
            models.Index(fields=['user', 'to_account']),
            models.Index(fields=['user', 'from_account']),
        ]

    def __str__(self):
        return f"{self.date} - Transfer {self.amount} from {self.from_account.name} to {self.to_account.name}"

class RecurringTransaction(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    FREQUENCY_CHOICES = [
        ('DAILY', _('Daily')),
        ('WEEKLY', _('Weekly')),
        ('BIWEEKLY', _('Bi-Weekly (Every 2 Weeks)')),
        ('MONTHLY', _('Monthly')),
        ('QUARTERLY', _('Quarterly (Every 3 Months)')),
        ('SEMIANNUALLY', _('Semi-Annually (Every 6 Months)')),
        ('YEARLY', _('Yearly')),
    ]
    TRANSACTION_TYPE_CHOICES = [
        ('EXPENSE', _('Expense')),
        ('INCOME', _('Income')),
        ('TRANSFER', _('Transfer')),
        ('LOAN', _('Loan Repayment')),
        ('CAPITAL', _('Capital Event')),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    transaction_type = models.CharField(max_length=10, choices=TRANSACTION_TYPE_CHOICES, verbose_name=_('Transaction Type'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Amount'))
    description = models.TextField(verbose_name=_('Description'))
    category = models.CharField(max_length=255, blank=True, null=True, verbose_name=_('Category'))
    category_fk = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transactions', verbose_name=_('Category FK'))
    source = models.CharField(max_length=255, blank=True, null=True, verbose_name=_('Source'))
    source_fk = models.ForeignKey('Category', on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transactions_as_source', verbose_name=_('Source FK'))
    
    payment_method = models.CharField(max_length=50, choices=Expense.PAYMENT_OPTIONS, default='Cash', verbose_name=_('Payment Method'))
    
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, verbose_name=_('Account'))
    loan = models.ForeignKey('Loan', on_delete=models.CASCADE, null=True, blank=True, related_name='recurring_schedules', verbose_name=_('Loan'))

    # Capital event-specific fields
    capital_subtype = models.CharField(
        max_length=30,
        choices=CAPITAL_SUBTYPE_CHOICES,
        blank=True,
        null=True,
        verbose_name=_('Capital Event Subtype'),
    )
    exclude_from_averages = models.BooleanField(default=True, verbose_name=_('Exclude from Averages & Trends'))
    exclude_from_budget = models.BooleanField(default=True, verbose_name=_('Exclude from Budget'))
    include_in_net_worth = models.BooleanField(default=True, verbose_name=_('Include in Cash Flow / Net Worth'))

    # Transfer-specific fields
    from_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transfers_out', verbose_name=_('From Account'))
    to_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='recurring_transfers_in', verbose_name=_('To Account'))

    frequency = models.CharField(max_length=15, choices=FREQUENCY_CHOICES, verbose_name=_('Frequency'))
    start_date = models.DateField(verbose_name=_('Start Date'))
    end_date = models.DateField(null=True, blank=True, verbose_name=_('End Date'))
    is_last_day_of_month = models.BooleanField(default=False, verbose_name=_('Repeat on last day of month'))
    is_last_working_day = models.BooleanField(default=False, verbose_name=_('Repeat on last working day of month'))
    last_processed_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if getattr(self, 'source_fk_id', None):
            qs = RecurringTransaction.objects.filter(source_fk_id=self.source_fk_id)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({'source_fk': _('Another recurring transaction already uses this source.')})

    @staticmethod
    def get_next_date(current_date, frequency, start_date=None, is_last_day_of_month=False, is_last_working_day=False):
        """
        Calculate the next occurrence date after current_date for a given frequency.
        Uses dateutil.rrule for RFC 5545 recurrence compliance.
        """
        from datetime import datetime, time

        from dateutil.rrule import (
            DAILY,
            FR,
            MO,
            MONTHLY,
            TH,
            TU,
            WE,
            WEEKLY,
            YEARLY,
            rrule,
        )

        if not start_date:
            start_date = current_date

        dt_start = datetime.combine(start_date, time.min)
        dt_current = datetime.combine(current_date, time.min)

        if frequency == 'DAILY':
            rule = rrule(DAILY, interval=1, dtstart=dt_start)
        elif frequency == 'WEEKLY':
            rule = rrule(WEEKLY, interval=1, dtstart=dt_start)
        elif frequency == 'BIWEEKLY':
            rule = rrule(WEEKLY, interval=2, dtstart=dt_start)
        elif frequency == 'MONTHLY':
            if is_last_working_day:
                rule = rrule(MONTHLY, interval=1, dtstart=dt_start, bymonthday=(-1, -2, -3), byweekday=(MO, TU, WE, TH, FR))
                next_dt = rule.after(dt_current)
                if next_dt:
                    return next_dt.date()
            elif is_last_day_of_month:
                rule = rrule(MONTHLY, interval=1, dtstart=dt_start, bymonthday=-1)
                next_dt = rule.after(dt_current)
                if next_dt:
                    return next_dt.date()
            else:
                import calendar
                max_days_this = calendar.monthrange(current_date.year, current_date.month)[1]
                this_month_occ = date(current_date.year, current_date.month, min(start_date.day, max_days_this))
                if current_date < this_month_occ:
                    return this_month_occ
                month = current_date.month % 12 + 1
                year = current_date.year + (current_date.month // 12)
                max_days_next = calendar.monthrange(year, month)[1]
                return date(year, month, min(start_date.day, max_days_next))
        elif frequency == 'QUARTERLY':
            import calendar
            max_days = calendar.monthrange(dt_start.year, dt_start.month)[1]
            target_day = min(dt_start.day, max_days)
            rule = rrule(MONTHLY, interval=3, dtstart=dt_start, bymonthday=target_day)
        elif frequency == 'SEMIANNUALLY':
            import calendar
            max_days = calendar.monthrange(dt_start.year, dt_start.month)[1]
            target_day = min(dt_start.day, max_days)
            rule = rrule(MONTHLY, interval=6, dtstart=dt_start, bymonthday=target_day)
        elif frequency == 'YEARLY':
            import calendar
            max_days_this = calendar.monthrange(current_date.year, start_date.month)[1]
            this_year_occ = date(current_date.year, start_date.month, min(start_date.day, max_days_this))
            if current_date < this_year_occ:
                return this_year_occ
            next_year = current_date.year + 1
            max_days_next = calendar.monthrange(next_year, start_date.month)[1]
            return date(next_year, start_date.month, min(start_date.day, max_days_next))
        else:
            return current_date + timedelta(days=365)

        next_dt = rule.after(dt_current)
        if next_dt:
            return next_dt.date()
        return current_date + timedelta(days=30)

    @property
    def next_due_date(self):
        due = self._calculate_next_due_date()
        if self.end_date and due and due > self.end_date:
            return None
        return due

    def _calculate_next_due_date(self):
        if not self.last_processed_date or self.last_processed_date < self.start_date:
            return self.start_date
        return self.get_next_date(
            self.last_processed_date,
            self.frequency,
            self.start_date,
            self.is_last_day_of_month,
            self.is_last_working_day,
        )

    def save(self, *args, **kwargs):
        # Multi-currency normalization
        base_currency = self.user.profile.currency
        if self.currency == base_currency:
            self.exchange_rate = Decimal('1.0')
            self.base_amount = self.amount
        else:
            self.exchange_rate = get_exchange_rate(self.currency, base_currency)
            self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))
            
        if getattr(self, 'category_fk', None):
            self.category = self.category_fk.name
        elif self.category:
            self.category = self.category.strip()
            
        if getattr(self, 'source_fk', None):
            self.source = self.source_fk.name
        elif self.source:
            self.source = self.source.strip()
            
        super().save(*args, **kwargs)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'transaction_type', 'amount', 'currency', 'description', 'frequency', 'start_date'],
                condition=models.Q(is_active=True),
                name='unique_recurring_transaction'
            )
        ]
        indexes = [
            models.Index(fields=['user', 'is_active']),
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
    
    PERSONA_CHOICES = [
        ('SALARIED', 'Salaried professional'),
        ('FREELANCER', 'Freelancer / Consultant'),
        ('FAMILY', 'Managing family finances'),
        ('STUDENT', 'Student / Just starting out'),
    ]
    persona = models.CharField(max_length=20, choices=PERSONA_CHOICES, blank=True, null=True)
    dismissed_onboarding_checklist = models.BooleanField(default=False)

    # DPDPA Consent Fields
    consent_granted = models.BooleanField(default=False)
    consent_timestamp = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=10, null=True, blank=True)

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
    razorpay_subscription_id = models.CharField(max_length=100, blank=True, null=True)
    razorpay_customer_id = models.CharField(max_length=100, blank=True, null=True)
    cancel_at_cycle_end = models.BooleanField(default=False)
    has_used_trial = models.BooleanField(default=False)

    # Lifecycle email drip tracking
    last_drip_email_day = models.IntegerField(default=0)
    expiry_reminder_sent = models.BooleanField(default=False)
    daily_reminder = models.BooleanField(default=True, verbose_name=_('Daily Expense Reminder'))
    
    # Salary settings
    salary_date = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(31)],
        help_text=_('Day of month when salary is received (1-31). Default is 1st of every month.')
    )

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
    def has_net_worth_access(self):
        """Net worth is a paid feature."""
        return get_limit(self.active_tier, 'net_worth')

    def can_add_account(self):
        """Checks account limit based on tier."""
        limit = get_limit(self.active_tier, 'accounts')
        if limit == -1: return True
        return self.user.accounts.filter(is_active=True).count() < limit

    def can_add_expense(self):
        """Checks monthly expense limit based on tier."""
        limit = get_limit(self.active_tier, 'expenses_per_month')
        if limit == -1: return True
        
        now = timezone.now()
        month_count = Expense.objects.filter(
            user=self.user, 
            date__year=now.year, 
            date__month=now.month
        ).count()
        return month_count < limit

    def can_add_recurring(self):
        """Checks recurring transaction limit based on tier."""
        limit = get_limit(self.active_tier, 'recurring_transactions')
        if limit == -1: return True
        return self.user.recurringtransaction_set.filter(is_active=True).count() < limit

    def can_add_category(self):
        """Checks category limit based on tier."""
        limit = get_limit(self.active_tier, 'budget_categories')
        if limit == -1: return True
        return self.user.category_set.count() < limit

    def can_add_goal(self):
        """Checks savings goal limit based on tier."""
        limit = get_limit(self.active_tier, 'savings_goals')
        if limit == -1: return True
        return self.user.savings_goals.count() < limit

    @property
    def can_track_loans(self):
        """Checks if loan tracking feature is enabled for the user's tier."""
        return get_limit(self.active_tier, 'loans') != 0

    def can_add_loan(self):
        """Checks loan limit based on tier."""
        limit = get_limit(self.active_tier, 'loans')
        if limit == -1:
            return True
        return self.user.loans.count() < limit

    def is_recurring_locked(self, obj):
        """Check if a specific recurring transaction is locked based on tier limits."""
        limit = get_limit(self.active_tier, 'recurring_transactions')
        if limit == -1: return False
        
        subs = list(self.user.recurringtransaction_set.all().order_by('created_at', 'id'))
        if obj in subs and subs.index(obj) >= limit:
            return True
        return False

    def is_account_locked(self, account):
        """Check if a specific account is locked based on tier limits."""
        limit = get_limit(self.active_tier, 'accounts')
        if limit == -1: return False
        
        # Order by created_at so the 'oldest' accounts stay unlocked
        accounts = list(self.user.accounts.filter(is_active=True).order_by('created_at', 'id'))
        if account in accounts and accounts.index(account) >= limit:
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
    def can_export_csv(self):
        """Checks if CSV export is allowed for the user's tier."""
        from finance_tracker.plans import get_limit
        return get_limit(self.active_tier, 'export_csv')

    @property
    def has_ai_access(self):
        """Checks if AI insights is allowed for the user's tier."""
        from finance_tracker.plans import get_limit
        return get_limit(self.active_tier, 'ai_insights')

    @property
    def net_worth_history_limit(self):
        """Returns the number of months of net worth history allowed for the user's tier."""
        from finance_tracker.plans import get_limit
        return get_limit(self.active_tier, 'net_worth_history')

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
    amount = models.DecimalField(max_digits=15, decimal_places=2)
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
    price = models.DecimalField(max_digits=15, decimal_places=2, help_text="Price in INR")
    razorpay_plan_id = models.CharField(max_length=100, blank=True, null=True)
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
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    NOTIFICATION_TYPES = [
        ('RECURRING', _('Recurring Transaction')),
        ('ANALYTICS', _('AI Analytics/Insights')),
        ('MILESTONE', _('Financial Milestone')),
        ('SYSTEM', _('System/Subscription Alert')),
    ]
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='SYSTEM')
    slug = models.CharField(max_length=255, null=True, blank=True, help_text=_("Used for deduplication (same slug shouldn't repeat in a month)"))
    link = models.CharField(max_length=500, null=True, blank=True, help_text=_("URL for redirection when clicked"))
    metadata = models.JSONField(null=True, blank=True)
    
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    # Optional link to the transaction that triggered it
    related_transaction = models.ForeignKey('RecurringTransaction', on_delete=models.SET_NULL, null=True, blank=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.title} ({self.notification_type})"

class SavingsGoal(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='savings_goals')
    name = models.CharField(max_length=255, verbose_name=_('Goal Name'))
    target_amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Target Amount'))
    current_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name=_('Current Amount'))
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

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for contribution in self.contributions.all():
                contribution.delete()
            super().delete(*args, **kwargs)

    @property
    def estimated_completion_date(self):
        if self.is_completed:
            return timezone.localdate()
            
        contributions = list(self.contributions.all())
        if not contributions or self.target_amount <= self.current_amount:
            return None
            
        first_contribution = min(contributions, key=lambda c: (c.date, c.id))
        today = timezone.localdate()
        days_elapsed = max((today - first_contribution.date).days, 1)
        
        total_contributed = sum(c.amount for c in contributions)
        avg_daily = total_contributed / Decimal(days_elapsed)
        
        remaining_amount = self.target_amount - self.current_amount
        if avg_daily <= 0 or remaining_amount <= 0:
            return None
            
        import math
        from datetime import timedelta
        days_left = max(math.ceil(float(remaining_amount / avg_daily)), 1)
        return today + timedelta(days=days_left)

    def __str__(self):
        return self.name

class GoalContribution(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    goal = models.ForeignKey(SavingsGoal, on_delete=models.CASCADE, related_name='contributions')
    account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='goal_contributions', verbose_name=_('From Account'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Contribution Amount'))
    date = models.DateField(default=timezone.now, verbose_name=_('Date'))
    
    created_at = models.DateTimeField(auto_now_add=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = GoalContributionManager()
    active = SoftDeleteManager()

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old_instance = None
            if self.pk:
                old_instance = GoalContribution.objects.select_related('goal', 'account').select_for_update().get(pk=self.pk)
                # Revert old balance and goal amount
                if old_instance.account_id:
                    old_account = Account.objects.select_for_update().get(pk=old_instance.account_id)
                    # Convert goal currency to account currency for reversal
                    reversal_amount = old_instance.amount
                    if old_instance.goal.currency != old_account.currency:
                        rate = get_exchange_rate(old_instance.goal.currency, old_account.currency)
                        reversal_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                        
                    old_account.balance += reversal_amount
                    old_account.save(update_fields=['balance', 'updated_at'])
                self.goal.current_amount -= old_instance.amount
            
            super().save(*args, **kwargs)
            
            # Apply new balance and goal amount
            if self.account:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert goal currency to account currency
                apply_amount = self.amount
                if self.goal.currency != locked_account.currency:
                    rate = get_exchange_rate(self.goal.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
                locked_account.balance -= apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])
            
            self.goal.current_amount += self.amount
            self.goal.save()

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_goal_contribution_create(
                        contribution=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_goal_contribution_update(
                        contribution=self,
                        previous_contribution=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='GOAL_CONTRIBUTION',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'goal_contribution_create' if old_instance is None else 'goal_contribution_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'goal_contribution': {
                        'source_id': self.id,
                        'goal_id': self.goal_id,
                        'account_id': self.account_id,
                        'amount': str(self.amount),
                        'goal_currency': self.goal.currency,
                        'user_id': self.goal.user_id,
                    },
                    'previous_goal_contribution': {
                        'source_id': old_instance.id,
                        'goal_id': old_instance.goal_id,
                        'account_id': old_instance.account_id,
                        'amount': str(old_instance.amount),
                        'goal_currency': old_instance.goal.currency,
                        'user_id': old_instance.goal.user_id,
                    } if old_instance else None,
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.goal.user,
                model_name='GoalContribution',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount)} if old_instance else None,
                    'after': {'amount': str(self.amount)}
                }
            )
            
    def delete(self, *args, **kwargs):
        with transaction.atomic():
            # Update account balance and goal's current amount when deleting a contribution
            if self.account:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                # Convert for deletion reversal
                apply_amount = self.amount
                if self.goal.currency != locked_account.currency:
                    rate = get_exchange_rate(self.goal.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                    
                locked_account.balance += apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])
                
            self.goal.current_amount -= self.amount
            self.goal.save()

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_goal_contribution_delete(
                    contribution=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='GOAL_CONTRIBUTION',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'goal_contribution_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'goal_contribution': {
                        'source_id': self.id,
                        'goal_id': self.goal_id,
                        'account_id': self.account_id,
                        'amount': str(self.amount),
                        'goal_currency': self.goal.currency,
                        'user_id': self.goal.user_id,
                    },
                },
            )
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])
                
                FinancialAuditLog.objects.create(
                    user=self.goal.user,
                    model_name='GoalContribution',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)
    def __str__(self):
        return f"+{self.amount} to {self.goal.name} on {self.date}"

class EmailLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='email_logs')
    to_email = models.EmailField()
    subject = models.CharField(max_length=255)
    body = models.TextField()
    html_body = models.TextField(blank=True, null=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='SENT') # SENT, FAILED
    error_message = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.to_email}: {self.subject}"

class Loan(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    LOAN_TYPES = [
        ('HOME', _('Home Loan')),
        ('CAR', _('Car Loan')),
        ('PERSONAL', _('Personal Loan')),
        ('EDUCATION', _('Education Loan')),
        ('BUSINESS', _('Business Loan')),
        ('OTHER', _('Other')),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='loans')
    name = models.CharField(max_length=100, verbose_name=_('Loan Name'))
    loan_type = models.CharField(max_length=20, choices=LOAN_TYPES, default='HOME', verbose_name=_('Loan Type'))
    REPAYMENT_TYPE_CHOICES = [
        ('EMI', _('EMI Amortizing')),
        ('BULLET', _('Bullet Repayment')),
        ('INTEREST_ONLY', _('Interest Only')),
    ]
    repayment_type = models.CharField(
        max_length=20, choices=REPAYMENT_TYPE_CHOICES, default='EMI',
        verbose_name=_('Repayment Type'),
    )
    initial_principal = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Initial Principal Amount'))
    duration_months = models.IntegerField(verbose_name=_('Duration (Months)'))
    start_date = models.DateField(default=timezone.now, verbose_name=_('Start Date'))
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    is_active = models.BooleanField(default=True, verbose_name=_('Is Active'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            for repayment in self.repayments.all():
                repayment.delete()
            super().delete(*args, **kwargs)

    @property
    def remaining_principal(self) -> Decimal:
        """
        Calculates remaining outstanding principal:
        initial_principal - sum(repayments.principal_portion) - sum(capital_prepaid)
        """
        
        principal_paid = getattr(self, 'paid_principal', None)
        if principal_paid is None:
            principal_paid = self.repayments.aggregate(
                total=Sum('principal_portion')
            )['total'] or Decimal('0.00')

        capital_prepaid = getattr(self, 'capital_prepaid', None)
        if capital_prepaid is None:
            capital_prepaid = self.capital_events.filter(
                subtype__in=['loan_down_payment', 'loan_prepayment']
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

        rem = Decimal(str(self.initial_principal)) - Decimal(str(principal_paid)) - Decimal(str(capital_prepaid))
        return max(rem, Decimal('0.00'))

    def __str__(self):
        return f"{self.name} - {self.initial_principal} {self.currency}"

class LoanInterestRate(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='interest_rates')
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, verbose_name=_('Annual Interest Rate (%)'))
    effective_date = models.DateField(default=timezone.now, verbose_name=_('Effective Date'))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_date']

    def __str__(self):
        return f"{self.loan.name} - {self.interest_rate}% from {self.effective_date}"

class LoanRepayment(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='repayments')
    from_account = models.ForeignKey(Account, on_delete=models.SET_NULL, null=True, blank=True, related_name='loan_repayments', verbose_name=_('Paid From Account'))
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Total Amount Paid (EMI)'))
    principal_portion = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Principal Portion'))
    interest_portion = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Interest Portion'))
    date = models.DateField(default=timezone.now, verbose_name=_('Payment Date'))
    
    # Multi-currency support
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=1.0, verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0.0, verbose_name=_('Amount in Base Currency'))

    created_at = models.DateTimeField(auto_now_add=True)
    
    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = SoftDeleteManager()

    class Meta:
        ordering = ['date']

    def __str__(self):
        loan_name = getattr(self, 'loan_id', None) and getattr(self.loan, 'name', _('Loan')) or _('Loan')
        return f"{loan_name} Repayment - {self.amount} on {self.date}"

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError({'amount': _('Repayment amount must be greater than zero.')})

        if self.principal_portion is None or self.principal_portion < 0:
            raise ValidationError({'principal_portion': _('Principal portion cannot be negative.')})

        if self.interest_portion is None or self.interest_portion < 0:
            raise ValidationError({'interest_portion': _('Interest portion cannot be negative.')})

        if (self.principal_portion + self.interest_portion).quantize(Decimal('0.01')) != self.amount.quantize(Decimal('0.01')):
            raise ValidationError(_('Repayment must equal principal portion plus interest portion.'))

        if self.from_account and self.from_account.user_id != self.loan.user_id:
            raise ValidationError({'from_account': _('Selected account does not belong to this user.')})

        prior_paid = self.loan.repayments.exclude(pk=self.pk).aggregate(
            total_principal=models.Sum('principal_portion')
        )['total_principal'] or Decimal('0.00')
        capital_prepaid = (
            self.loan.capital_events
            .filter(subtype__in=['loan_down_payment', 'loan_prepayment'])
            .aggregate(total=models.Sum('amount'))['total']
        ) or Decimal('0.00')
        remaining_principal = max(
            self.loan.initial_principal - prior_paid - capital_prepaid,
            Decimal('0.00'),
        )
        if self.principal_portion > remaining_principal:
            raise ValidationError({'principal_portion': _('Principal portion cannot exceed remaining principal.')})

    def save(self, *args, **kwargs):
        self.full_clean()
        with transaction.atomic():
            old_instance = None
            # Handle balance reversal for updates
            if self.pk:
                old_instance = LoanRepayment.objects.select_for_update().get(pk=self.pk)
                if old_instance.from_account_id:
                    old_account = Account.objects.select_for_update().get(pk=old_instance.from_account_id)
                    # Convert old amount to account currency for reversal
                    reversal_amount = old_instance.amount
                    if old_instance.loan.currency != old_account.currency:
                        rate = get_exchange_rate(old_instance.loan.currency, old_account.currency)
                        reversal_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                    
                    old_account.balance += reversal_amount
                    old_account.save(update_fields=['balance', 'updated_at'])

            # Multi-currency normalization (amount is in loan currency)
            base_currency = self.loan.user.profile.currency
            if self.loan.currency == base_currency:
                self.exchange_rate = Decimal('1.0')
                self.base_amount = self.amount
            else:
                self.exchange_rate = get_exchange_rate(self.loan.currency, base_currency)
                self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))

            super().save(*args, **kwargs)

            # Apply new balance
            if self.from_account:
                locked_account = Account.objects.select_for_update().get(pk=self.from_account_id)
                # Convert current amount to account currency
                apply_amount = self.amount
                if self.loan.currency != locked_account.currency:
                    rate = get_exchange_rate(self.loan.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                
                locked_account.balance -= apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_loan_repayment_create(
                        repayment=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_loan_repayment_update(
                        repayment=self,
                        previous_repayment=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='LOAN_REPAYMENT',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'loan_repayment_create' if old_instance is None else 'loan_repayment_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'loan_repayment': {
                        'loan_id': self.loan_id,
                        'amount': str(self.amount),
                        'principal_portion': str(self.principal_portion),
                        'interest_portion': str(self.interest_portion),
                        'from_account_id': self.from_account_id,
                        'source_id': self.id,
                    },
                    'previous_loan_repayment': {
                        'loan_id': old_instance.loan_id,
                        'amount': str(old_instance.amount),
                        'principal_portion': str(old_instance.principal_portion),
                        'interest_portion': str(old_instance.interest_portion),
                        'from_account_id': old_instance.from_account_id,
                        'source_id': old_instance.id,
                    } if old_instance else None,
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.loan.user,
                model_name='LoanRepayment',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount)} if old_instance else None,
                    'after': {'amount': str(self.amount)}
                }
            )

            from .services import LoanService
            LoanService.sync_loan_active_status(self.loan)

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.from_account:
                locked_account = Account.objects.select_for_update().get(pk=self.from_account_id)
                # Convert to account currency for deletion reversal
                apply_amount = self.amount
                if self.loan.currency != locked_account.currency:
                    rate = get_exchange_rate(self.loan.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))

                locked_account.balance += apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_loan_repayment_delete(
                    repayment=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='LOAN_REPAYMENT',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'loan_repayment_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'loan_repayment': {
                        'loan_id': self.loan_id,
                        'amount': str(self.amount),
                        'principal_portion': str(self.principal_portion),
                        'interest_portion': str(self.interest_portion),
                        'from_account_id': self.from_account_id,
                        'source_id': self.id,
                    },
                },
            )
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])

                FinancialAuditLog.objects.create(
                    user=self.loan.user,
                    model_name='LoanRepayment',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)

            from .services import LoanService
            LoanService.sync_loan_active_status(self.loan)


class DeletionRequestAuditLog(models.Model):
    email = models.EmailField()
    username = models.CharField(max_length=150)
    requested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Deletion request for {self.username} ({self.email}) at {self.requested_at}"

class CapitalEvent(models.Model):
    """
    A large, one-off financial event (e.g., home-loan down payment, big medical bill)
    that is intentionally excluded from regular analytics / budget tracking by default
    so it doesn't distort monthly spending trends, while still being reflected in
    cash flow and net-worth calculations.

    Schema decision: separate table (not a flag on Expense) so that:
      - analytics / budget exclusion logic stays clean without per-query branching;
      - distinct fields (subtype, linked_loan, exclude_* flags) never pollute Expense;
      - tier limits on Expense.can_add_expense() are unaffected;
      - type conversion between CapitalEvent <-> Expense is a clear data copy, not a flag flip.
    """
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    SUBTYPE_CHOICES = CAPITAL_SUBTYPE_CHOICES

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='capital_events')
    amount = models.DecimalField(max_digits=15, decimal_places=2, verbose_name=_('Amount'))
    date = models.DateField(verbose_name=_('Date'))
    subtype = models.CharField(
        max_length=30,
        choices=SUBTYPE_CHOICES,
        default='other',
        verbose_name=_('Subtype'),
    )
    note = models.TextField(blank=True, default='', verbose_name=_('Note'))

    # Optional link to an existing Loan record
    linked_loan = models.ForeignKey(
        Loan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='capital_events',
        verbose_name=_('Linked Loan'),
    )

    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹', verbose_name=_('Currency'))
    exchange_rate = models.DecimalField(max_digits=15, decimal_places=6, default=Decimal('1.0'), verbose_name=_('Exchange Rate'))
    base_amount = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'), verbose_name=_('Amount in Base Currency'))

    account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='capital_events',
        verbose_name=_('Account'),
    )

    # Analytical flags (default: excluded from averages / budget, included in cash flow)
    exclude_from_averages = models.BooleanField(default=True, verbose_name=_('Exclude from Averages & Trends'))
    exclude_from_budget = models.BooleanField(default=True, verbose_name=_('Exclude from Budget'))
    include_in_net_worth = models.BooleanField(default=True, verbose_name=_('Include in Cash Flow / Net Worth'))

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    objects = models.Manager()
    active = SoftDeleteManager()

    class Meta:
        indexes = [
            models.Index(fields=['user', 'date']),
            models.Index(fields=['user', 'subtype']),
            models.Index(fields=['linked_loan']),
        ]
        ordering = ['-date']

    def __str__(self):
        return f"{self.date} – {self.get_subtype_display()} – {self.currency}{self.amount}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            old_instance = None
            if self.pk:
                # Do not join nullable account relation under FOR UPDATE (unsupported on Postgres outer joins).
                old_instance = CapitalEvent.objects.select_related(None).select_for_update().get(pk=self.pk)
                if old_instance.account_id and old_instance.include_in_net_worth:
                    old_account = Account.objects.select_for_update().get(pk=old_instance.account_id)
                    reversal_amount = old_instance.amount
                    if old_instance.currency != old_account.currency:
                        rate = get_exchange_rate(old_instance.currency, old_account.currency)
                        reversal_amount = (old_instance.amount * rate).quantize(Decimal('0.01'))
                    old_account.balance += reversal_amount
                    old_account.save(update_fields=['balance', 'updated_at'])

            # Multi-currency normalisation
            base_currency = self.user.profile.currency
            if self.currency == base_currency:
                self.exchange_rate = Decimal('1.0')
                self.base_amount = self.amount
            else:
                self.exchange_rate = get_exchange_rate(self.currency, base_currency)
                self.base_amount = (self.amount * self.exchange_rate).quantize(Decimal('0.01'))

            super().save(*args, **kwargs)

            if self.account and self.include_in_net_worth:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                locked_account.balance -= apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                version_token = _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE')
                if old_instance is None:
                    LedgerPostingService.shadow_post_capital_event_create(
                        event=self,
                        version_token=version_token,
                    )
                else:
                    LedgerPostingService.shadow_post_capital_event_update(
                        event=self,
                        previous_event=old_instance,
                        version_token=version_token,
                    )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='CAPITAL_EVENT',
                source_id=self.id,
                action='CREATE' if old_instance is None else 'UPDATE',
                payload={
                    'handler': 'capital_event_create' if old_instance is None else 'capital_event_update',
                    'version_token': _build_ledger_version(self, 'CREATE' if old_instance is None else 'UPDATE'),
                    'capital_event': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'subtype': self.subtype,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                },
            )
            
            FinancialAuditLog.objects.create(
                user=self.user,
                model_name='CapitalEvent',
                object_id=self.id,
                object_uuid=self.uuid,
                action='CREATE' if old_instance is None else 'UPDATE',
                diff={
                    'before': {'amount': str(old_instance.amount)} if old_instance else None,
                    'after': {'amount': str(self.amount)}
                }
            )

    def delete(self, *args, **kwargs):
        with transaction.atomic():
            if self.account and self.include_in_net_worth:
                locked_account = Account.objects.select_for_update().get(pk=self.account_id)
                apply_amount = self.amount
                if self.currency != locked_account.currency:
                    rate = get_exchange_rate(self.currency, locked_account.currency)
                    apply_amount = (self.amount * rate).quantize(Decimal('0.01'))
                locked_account.balance += apply_amount
                locked_account.save(update_fields=['balance', 'updated_at'])

            def _post_shadow_entry():
                from .ledger_service import LedgerPostingService

                LedgerPostingService.shadow_post_capital_event_delete(
                    event=self,
                    version_token=_build_ledger_version(self, 'DELETE'),
                )

            _run_ledger_shadow(
                _post_shadow_entry,
                source_type='CAPITAL_EVENT',
                source_id=self.id,
                action='DELETE',
                payload={
                    'handler': 'capital_event_delete',
                    'version_token': _build_ledger_version(self, 'DELETE'),
                    'capital_event': {
                        'user_id': self.user_id,
                        'amount': str(self.amount),
                        'currency': self.currency,
                        'subtype': self.subtype,
                        'account_id': self.account_id,
                        'source_id': self.id,
                    },
                },
            )
            if getattr(settings, 'SOFT_DELETE_ENABLED', False):
                self.is_deleted = True
                self.deleted_at = timezone.now()
                self.save(update_fields=['is_deleted', 'deleted_at'])
                
                FinancialAuditLog.objects.create(
                    user=self.user,
                    model_name='CapitalEvent',
                    object_id=self.id,
                    object_uuid=self.uuid,
                    action='DELETE',
                    diff={'deleted': True}
                )
            else:
                super().delete(*args, **kwargs)


class Holding(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='holdings')
    instrument_name = models.CharField(max_length=255)
    INSTRUMENT_TYPES = [
        ('MF', 'Mutual Fund'),
        ('STOCK', 'Stock'),
        ('NPS', 'NPS'),
        ('PPF', 'PPF'),
        ('EPF', 'EPF'),
        ('RD', 'Recurring Deposit'),
        ('OTHER', 'Other'),
    ]
    instrument_type = models.CharField(max_length=20, choices=INSTRUMENT_TYPES, default='OTHER')
    units = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    avg_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹')
    is_active = models.BooleanField(default=True)
    scheme_code = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    isin = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            # Backs the filter on active holdings per account in net-worth computation
            models.Index(fields=['account', 'is_active'], name='holding_account_active_idx'),
            models.Index(fields=['scheme_code', 'is_active'], name='holding_scheme_active_idx'),
        ]

    @property
    def cost_basis(self):
        units = self.units or Decimal('0.00')
        avg_cost = self.avg_cost or Decimal('0.00')
        return (units * avg_cost).quantize(Decimal('0.01'))

    def __str__(self):
        return f"{self.instrument_name} in {self.account.name}"


class Valuation(models.Model):
    holding = models.ForeignKey(Holding, on_delete=models.CASCADE, related_name='valuations')
    value = models.DecimalField(max_digits=15, decimal_places=2)
    as_of_date = models.DateField(default=timezone.now)
    unit_nav = models.DecimalField(max_digits=15, decimal_places=6, null=True, blank=True)
    source = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-as_of_date', '-created_at']
        indexes = [
            # Backs the DISTINCT ON (holding_id) query in LedgerReadService.get_net_worth
            models.Index(fields=['holding', 'as_of_date'], name='val_holding_date_idx'),
        ]

    def __str__(self):
        return f"{self.holding.instrument_name} - {self.value} on {self.as_of_date}"


class FundNAVCache(models.Model):
    """
    SPEC §3a: Explicitly justified model to deduplicate NAV fetches across users.
    If 500 users hold the same scheme, the daily fetch job queries the provider ONCE.
    """
    scheme_code = models.CharField(max_length=50, unique=True, db_index=True)
    scheme_name = models.CharField(max_length=255, null=True, blank=True)
    isin = models.CharField(max_length=50, null=True, blank=True, db_index=True)
    latest_nav = models.DecimalField(max_digits=15, decimal_places=4, null=True, blank=True)
    nav_as_of_date = models.DateField(null=True, blank=True)
    last_fetch_attempt_at = models.DateTimeField(null=True, blank=True)
    last_fetch_error = models.TextField(null=True, blank=True)
    consecutive_failure_count = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Fund NAV Cache'
        verbose_name_plural = 'Fund NAV Caches'

    def __str__(self):
        return f"{self.scheme_code} - {self.scheme_name or 'Unknown'} ({self.latest_nav})"


class AMFIScheme(models.Model):
    """
    Local search mirror for AMFI scheme list (code + name + ISIN).
    Used purely for fund-search autocomplete UX when populating Holding.scheme_code.
    Distinct from FundNAVCache (which stores cached NAV values).
    """
    scheme_code = models.CharField(max_length=50, unique=True, db_index=True)
    scheme_name = models.CharField(max_length=255, db_index=True)
    isin = models.CharField(max_length=50, null=True, blank=True, db_index=True)

    class Meta:
        verbose_name = 'AMFI Scheme'
        verbose_name_plural = 'AMFI Schemes'
        indexes = [
            models.Index(fields=['scheme_name'], name='amfi_scheme_name_idx'),
        ]

    def __str__(self):
        return f"{self.scheme_code} - {self.scheme_name}"


class PhysicalAsset(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='physical_assets')
    name = models.CharField(max_length=255)
    ASSET_CLASSES = [
        ('REAL_ESTATE', 'Real Estate'),
        ('VEHICLE', 'Vehicle'),
        ('GOLD', 'Gold/Jewelry'),
        ('INSURANCE', 'Insurance Policy'),
        ('OTHER', 'Other'),
    ]
    asset_class = models.CharField(max_length=20, choices=ASSET_CLASSES, default='OTHER')
    acquisition_cost = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    acquisition_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES, default='₹')
    policy_number = models.CharField(max_length=100, null=True, blank=True, verbose_name=_('Policy Number'))
    premium_amount = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name=_('Premium Amount'))
    PREMIUM_FREQUENCY_CHOICES = [
        ('ANNUAL', _('Annual')),
        ('SEMI_ANNUAL', _('Semi-Annual')),
        ('QUARTERLY', _('Quarterly')),
        ('MONTHLY', _('Monthly')),
    ]
    premium_frequency = models.CharField(
        max_length=20, choices=PREMIUM_FREQUENCY_CHOICES, null=True, blank=True,
        verbose_name=_('Premium Frequency'),
    )
    policy_start_date = models.DateField(null=True, blank=True, verbose_name=_('Policy Start Date'))
    sum_assured = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True, verbose_name=_('Sum Assured'))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.asset_class})"


class AssetValuation(models.Model):
    asset = models.ForeignKey(PhysicalAsset, on_delete=models.CASCADE, related_name='valuations')
    value = models.DecimalField(max_digits=15, decimal_places=2)
    as_of_date = models.DateField(default=timezone.now)
    source = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-as_of_date', '-created_at']
        indexes = [
            # Backs the DISTINCT ON (asset_id) query in LedgerReadService.get_net_worth
            models.Index(fields=['asset', 'as_of_date'], name='assetval_asset_date_idx'),
        ]

    def __str__(self):
        return f"{self.asset.name} - {self.value} on {self.as_of_date}"


class NetWorthSnapshot(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='net_worth_snapshots')
    as_of_date = models.DateField(default=timezone.now)
    total_net_worth = models.DecimalField(max_digits=15, decimal_places=2)
    total_assets = models.DecimalField(max_digits=15, decimal_places=2)
    total_liabilities = models.DecimalField(max_digits=15, decimal_places=2)
    breakdown = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'as_of_date'], name='unique_net_worth_snapshot')
        ]
        indexes = [
            models.Index(fields=['user', 'as_of_date']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.total_net_worth} on {self.as_of_date}"


class FXRate(models.Model):
    from_currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES)
    to_currency = models.CharField(max_length=5, choices=CURRENCY_CHOICES)
    rate = models.DecimalField(max_digits=15, decimal_places=6)
    as_of_date = models.DateField(default=timezone.now)
    source = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['from_currency', 'to_currency', 'as_of_date'], name='unique_fx_rate')
        ]

    def __str__(self):
        return f"{self.from_currency} to {self.to_currency} - {self.rate} on {self.as_of_date}"


class ConsentEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='consent_events')
    action = models.CharField(max_length=20) # GRANTED, WITHDRAWN, UPDATED
    purpose = models.CharField(max_length=100, null=True, blank=True)
    consent_version = models.CharField(max_length=50)
    timestamp = models.DateTimeField(default=timezone.now)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} {self.action} on {self.timestamp}"


class LoanScheduleInstallment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name='schedule_installments')
    installment_no = models.PositiveIntegerField()
    due_date = models.DateField()
    scheduled_principal = models.DecimalField(max_digits=15, decimal_places=2)
    scheduled_interest = models.DecimalField(max_digits=15, decimal_places=2)
    scheduled_balance = models.DecimalField(max_digits=15, decimal_places=2)
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['loan', 'installment_no'], name='unique_loan_installment')
        ]
        ordering = ['due_date']

    def __str__(self):
        return f"Installment {self.installment_no} for {self.loan.name}"

class FinancialAuditLog(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='audit_logs')
    model_name = models.CharField(max_length=50)
    object_id = models.IntegerField()
    object_uuid = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=20) # CREATE, UPDATE, DELETE
    timestamp = models.DateTimeField(default=timezone.now)
    diff = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.user.username} - {self.action} on {self.model_name} {self.object_id}"
