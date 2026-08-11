from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (
    Account,
    AssetValuation,
    CapitalEvent,
    Category,
    ConsentEvent,
    DeletionRequestAuditLog,
    EmailLog,
    Expense,
    FinancialAuditLog,
    FXRate,
    GoalContribution,
    Holding,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerPostingFailure,
    LedgerReconciliationReport,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    NetWorthSnapshot,
    Notification,
    PaymentHistory,
    RecurringTransaction,
    SavingsGoal,
    SubscriptionPlan,
    Transfer,
    UserProfile,
)


class DemoExcludeMixin:
    """
    Mixin for ModelAdmin classes that have a direct 'user' FK.
    Excludes all records belonging to the demo account from the admin list.
    """
    def get_queryset(self, request):
        return super().get_queryset(request).exclude(user__username='demo')


@admin.register(Notification)
class NotificationAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('title', 'user', 'is_read', 'created_at', 'related_transaction')
    list_select_related = ('user', 'related_transaction')
    list_filter = ('is_read', 'created_at', 'user')
    search_fields = ('title', 'message', 'user__username')
    ordering = ('-created_at',)

@admin.register(EmailLog)
class EmailLogAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('to_email', 'subject', 'user', 'sent_at', 'status')
    list_select_related = ('user',)
    list_filter = ('sent_at', 'status')
    search_fields = ('to_email', 'subject', 'body', 'user__username')
    readonly_fields = ('to_email', 'subject', 'body', 'html_body', 'sent_at', 'status', 'error_message', 'user')
    ordering = ('-sent_at',)

@admin.register(Category)
class CategoryAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('name', 'limit', 'user')
    list_select_related = ('user',)
    list_filter = ('user',)
    search_fields = ('name',)


@admin.register(Expense)
class ExpenseAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('date', 'description', 'category', 'amount', 'user')
    list_select_related = ('user',)
    list_filter = ('date', 'user', 'category')
    search_fields = ('description', 'category', 'user__username')
    ordering = ('-date',)

@admin.register(Income)
class IncomeAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('date', 'source', 'amount', 'user')
    list_select_related = ('user',)
    list_filter = ('date', 'user', 'source')
    search_fields = ('source', 'description', 'user__username')
    ordering = ('-date',)

@admin.register(RecurringTransaction)
class RecurringTransactionAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('description', 'transaction_type', 'amount', 'frequency', 'next_due_date', 'user', 'is_active')
    list_select_related = ('user',)
    list_filter = ('transaction_type', 'frequency', 'is_active', 'user')
    search_fields = ('description', 'user__username')

@admin.register(Account)
class AccountAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('name', 'account_type', 'balance', 'currency', 'linked_loan', 'linked_physical_asset', 'is_active', 'user')
    list_select_related = ('user', 'linked_loan', 'linked_physical_asset')
    list_filter = ('account_type', 'currency', 'is_active', 'user')
    search_fields = ('name', 'user__username')
    raw_id_fields = ('linked_loan', 'linked_physical_asset')

@admin.register(Transfer)
class TransferAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('date', 'from_account', 'to_account', 'amount', 'user')
    list_select_related = ('user', 'from_account', 'to_account')
    list_filter = ('date', 'from_account', 'to_account', 'user')
    search_fields = ('description', 'user__username')
    ordering = ('-date',)


@admin.register(LedgerAccount)
class LedgerAccountAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('code', 'name', 'account_type', 'currency', 'user', 'is_active')
    list_select_related = ('user',)
    list_filter = ('account_type', 'currency', 'is_active')
    search_fields = ('code', 'name', 'user__username')


@admin.register(JournalEntry)
class JournalEntryAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('id', 'source_type', 'source_id', 'user', 'status', 'posted_at')
    list_select_related = ('user',)
    list_filter = ('source_type', 'status', 'posted_at')
    search_fields = ('idempotency_key', 'description', 'user__username')
    ordering = ('-posted_at',)


@admin.register(JournalLine)
class JournalLineAdmin(admin.ModelAdmin):
    list_display = ('journal_entry', 'ledger_account', 'direction', 'amount', 'currency', 'base_amount')
    list_select_related = ('journal_entry', 'ledger_account', 'account_ref')
    list_filter = ('direction', 'currency')
    search_fields = ('journal_entry__idempotency_key', 'ledger_account__code')


@admin.register(LedgerPostingFailure)
class LedgerPostingFailureAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_type', 'source_id', 'action', 'status', 'attempts', 'next_retry_at')
    list_filter = ('source_type', 'status', 'action')
    search_fields = ('source_id', 'error_message')
    ordering = ('-created_at',)


@admin.register(LedgerReconciliationReport)
class LedgerReconciliationReportAdmin(admin.ModelAdmin):
    list_display = ('as_of_date', 'user', 'account', 'account_balance', 'ledger_balance', 'drift_amount', 'status')
    list_select_related = ('user', 'account')
    list_filter = ('status', 'as_of_date')
    search_fields = ('user__username', 'account__name')
    ordering = ('-as_of_date', '-created_at')

@admin.register(UserProfile)
class UserProfileAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('user', 'tier', 'subscription_end_date', 'cancel_at_cycle_end', 'subscription_expired', 'is_lifetime', 'is_pro', 'razorpay_subscription_id', 'email_verified')
    list_select_related = ('user',)
    list_filter = ('tier', 'cancel_at_cycle_end', 'is_lifetime')
    search_fields = ('user__username', 'user__email')

    def email_verified(self, obj):
        try:
            email_address = EmailAddress.objects.get(user=obj.user, primary=True)
            return email_address.verified
        except EmailAddress.DoesNotExist:
            return False
    email_verified.boolean = True

    def subscription_expired(self, obj):
        return obj.subscription_expired
    subscription_expired.boolean = True
    
@admin.register(PaymentHistory)
class PaymentHistoryAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('user', 'amount', 'tier', 'status', 'created_at', 'subscription_end_date')
    list_select_related = ('user', 'user__profile')
    list_filter = ('status', 'tier', 'created_at')
    search_fields = ('user__username', 'order_id', 'payment_id')

    def subscription_end_date(self, obj):
        return obj.user.profile.subscription_end_date
    subscription_end_date.short_description = 'Sub End Date'
    subscription_end_date.admin_order_field = 'user__profile__subscription_end_date'


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'tier', 'duration', 'price', 'razorpay_plan_id', 'is_active')
    list_editable = ('price', 'is_active')
    list_filter = ('tier', 'duration')
    ordering = ('tier', 'price')


class LoanInterestRateInline(admin.TabularInline):
    model = LoanInterestRate
    extra = 1


class LoanRepaymentInline(admin.TabularInline):
    model = LoanRepayment
    extra = 0
    raw_id_fields = ('from_account',)
    readonly_fields = ('exchange_rate', 'base_amount')


@admin.register(Loan)
class LoanAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('name', 'loan_type', 'initial_principal', 'currency', 'duration_months', 'start_date', 'is_active', 'user')
    list_select_related = ('user',)
    list_filter = ('loan_type', 'currency', 'is_active', 'start_date')
    search_fields = ('name', 'user__username')
    inlines = [LoanInterestRateInline, LoanRepaymentInline]


@admin.register(LoanInterestRate)
class LoanInterestRateAdmin(admin.ModelAdmin):
    list_display = ('loan', 'interest_rate', 'effective_date')
    list_select_related = ('loan',)
    list_filter = ('effective_date',)
    search_fields = ('loan__name',)


@admin.register(LoanRepayment)
class LoanRepaymentAdmin(admin.ModelAdmin):
    list_display = ('date', 'loan', 'amount', 'principal_portion', 'interest_portion', 'from_account', 'base_amount')
    list_select_related = ('loan', 'from_account')
    list_filter = ('date', 'loan', 'from_account')
    search_fields = ('loan__name', 'from_account__name')
    ordering = ('-date',)


# Re-register User Admin to include Email Verification inline
class EmailAddressInline(admin.StackedInline):
    model = EmailAddress
    extra = 0

class UserAdmin(BaseUserAdmin):
    inlines = (EmailAddressInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'last_login', 'date_joined')

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(username='demo')

admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(DeletionRequestAuditLog)
class DeletionRequestAuditLogAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'requested_at')
    list_filter = ('requested_at',)
    search_fields = ('username', 'email')
    readonly_fields = ('username', 'email', 'requested_at')
    ordering = ('-requested_at',)


@admin.register(CapitalEvent)
class CapitalEventAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('date', 'get_subtype_display', 'amount', 'currency', 'linked_loan', 'exclude_from_averages', 'exclude_from_budget', 'user')
    list_select_related = ('user', 'account', 'linked_loan')
    list_filter = ('subtype', 'exclude_from_averages', 'exclude_from_budget', 'include_in_net_worth', 'date')
    search_fields = ('note', 'user__username')
    ordering = ('-date',)
    raw_id_fields = ('linked_loan',)

@admin.register(NetWorthSnapshot)
class NetWorthSnapshotAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('user', 'as_of_date', 'total_net_worth', 'total_assets', 'total_liabilities', 'created_at')
    list_select_related = ('user',)
    list_filter = ('as_of_date',)
    search_fields = ('user__username', 'user__email')
    ordering = ('-as_of_date', '-created_at')

@admin.register(FinancialAuditLog)
class FinancialAuditLogAdmin(admin.ModelAdmin):
    list_display = ('id', 'model_name', 'object_id', 'action', 'timestamp')
    list_filter = ('model_name', 'action', 'timestamp')
    search_fields = ('object_id', 'diff')

@admin.register(FXRate)
class FXRateAdmin(admin.ModelAdmin):
    list_display = ('from_currency', 'to_currency', 'rate', 'as_of_date')
    list_filter = ('as_of_date', 'from_currency', 'to_currency')

@admin.register(AssetValuation)
class AssetValuationAdmin(admin.ModelAdmin):
    list_display = ('asset', 'value', 'as_of_date', 'source')
    list_filter = ('as_of_date',)

@admin.register(Holding)
class HoldingAdmin(admin.ModelAdmin):
    list_display = ('account', 'instrument_name', 'instrument_type', 'units', 'avg_cost')
    search_fields = ('instrument_name', 'account__name')

@admin.register(ConsentEvent)
class ConsentEventAdmin(admin.ModelAdmin):
    list_display = ('user', 'purpose', 'action', 'timestamp')
    list_filter = ('purpose', 'action', 'timestamp')

@admin.register(SavingsGoal)
class SavingsGoalAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('user', 'name', 'target_amount', 'current_amount', 'target_date')
    search_fields = ('name', 'user__username')

@admin.register(GoalContribution)
class GoalContributionAdmin(admin.ModelAdmin):
    list_display = ('goal', 'amount', 'date')
    list_filter = ('date',)
