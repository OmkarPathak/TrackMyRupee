from allauth.account.models import EmailAddress
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (
    Account,
    Category,
    EmailLog,
    Expense,
    Income,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LedgerPostingFailure,
    LedgerReconciliationReport,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    Notification,
    PaymentHistory,
    RecurringTransaction,
    SubscriptionPlan,
    Transfer,
    UserProfile,
)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'is_read', 'created_at', 'related_transaction')
    list_select_related = ('user', 'related_transaction')
    list_filter = ('is_read', 'created_at', 'user')
    search_fields = ('title', 'message', 'user__username')
    ordering = ('-created_at',)

@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('to_email', 'subject', 'user', 'sent_at', 'status')
    list_select_related = ('user',)
    list_filter = ('sent_at', 'status')
    search_fields = ('to_email', 'subject', 'body', 'user__username')
    readonly_fields = ('to_email', 'subject', 'body', 'html_body', 'sent_at', 'status', 'error_message', 'user')
    ordering = ('-sent_at',)

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'limit', 'user')
    list_select_related = ('user',)
    list_filter = ('user',)
    search_fields = ('name',)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('date', 'description', 'category', 'amount', 'user')
    list_select_related = ('user',)
    list_filter = ('date', 'user', 'category')
    search_fields = ('description', 'category', 'user__username')
    ordering = ('-date',)

@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ('date', 'source', 'amount', 'user')
    list_select_related = ('user',)
    list_filter = ('date', 'user', 'source')
    search_fields = ('source', 'description', 'user__username')
    ordering = ('-date',)

@admin.register(RecurringTransaction)
class RecurringTransactionAdmin(admin.ModelAdmin):
    list_display = ('description', 'transaction_type', 'amount', 'frequency', 'next_due_date', 'user', 'is_active')
    list_select_related = ('user',)
    list_filter = ('transaction_type', 'frequency', 'is_active', 'user')
    search_fields = ('description', 'user__username')

@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_type', 'balance', 'currency', 'user')
    list_select_related = ('user',)
    list_filter = ('account_type', 'currency', 'user')
    search_fields = ('name', 'user__username')

@admin.register(Transfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ('date', 'from_account', 'to_account', 'amount', 'user')
    list_select_related = ('user', 'from_account', 'to_account')
    list_filter = ('date', 'from_account', 'to_account', 'user')
    search_fields = ('description', 'user__username')
    ordering = ('-date',)


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'account_type', 'currency', 'user', 'is_active')
    list_select_related = ('user',)
    list_filter = ('account_type', 'currency', 'is_active')
    search_fields = ('code', 'name', 'user__username')


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
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
class UserProfileAdmin(admin.ModelAdmin):
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
class PaymentHistoryAdmin(admin.ModelAdmin):
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
class LoanAdmin(admin.ModelAdmin):
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

admin.site.unregister(User)
admin.site.register(User, UserAdmin)
