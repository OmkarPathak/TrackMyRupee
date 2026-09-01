import os
from email.mime.image import MIMEImage

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.templatetags.static import static
from django.utils.html import format_html

from .models import (
    Account,
    Announcement,
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
        qs = super().get_queryset(request)
        if any(f.name == 'user' for f in self.model._meta.get_fields()):
            return qs.exclude(user__username='demo')
        return qs


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


@admin.register(Announcement)
class AnnouncementAdmin(DemoExcludeMixin, admin.ModelAdmin):
    list_display = ('title', 'audience', 'status', 'send_push', 'send_email', 'show_modal', 'is_active_display', 'created_at', 'sent_at')
    list_filter = ('status', 'audience', 'send_push', 'send_email', 'show_modal')
    search_fields = ('title', 'body')
    readonly_fields = ('created_at', 'sent_at', 'image_preview')
    actions = ['send_test_to_self', 'queue_for_sending']

    def is_active_display(self, obj):
        if obj.expires_at and obj.expires_at <= timezone.now():
            return format_html('<span style="color: #dc3545; font-weight: bold;">Expired</span>')
        return format_html('<span style="color: #198754; font-weight: bold;">Active</span>')
    is_active_display.short_description = "Modal Status"

    def image_preview(self, obj):
        if obj and obj.image:
            return format_html('<img src="{}" style="max-height: 200px; max-width: 100%; border-radius: 8px; border: 1px solid #dee2e6;" />', obj.image.url)
        return "No image uploaded"
    image_preview.short_description = "Image Preview"

    @admin.action(description="Send test to myself")
    def send_test_to_self(self, request, queryset):
        sent_count = 0
        site_url = getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/')
        
        for announcement in queryset:
            user = request.user
            from blog.templatetags.blog_extras import markdown as render_markdown
            from expenses.utils import markdown_to_plain_text
            body_html = render_markdown(announcement.body)
            body_plain = markdown_to_plain_text(announcement.body)

            # WebPush Test (attempts send if send_push is checked and user has WebPush subscription)
            if announcement.send_push:
                from webpush import send_user_notification
                from webpush.models import PushInformation
                if PushInformation.objects.filter(user=user).exists():
                    icon_path = static('img/pwa-icon-512.png')
                    absolute_icon_url = f"{site_url}{icon_path}"
                    push_payload = {
                        "head": announcement.title,
                        "body": body_plain,
                        "icon": absolute_icon_url,
                        "url": announcement.cta_link or f"{site_url}/",
                    }
                    if announcement.image:
                        push_payload["image"] = f"{site_url}{announcement.image.url}"
                    try:
                        send_user_notification(user=user, payload=push_payload, ttl=3600)
                    except Exception as e:
                        self.message_user(request, f"Push test failed: {e}", level='warning')

            # Email Test (CID-embedded inline image for single admin recipient if send_email is checked)
            if announcement.send_email:
                if user.email:
                    image_cid = None
                    img_data = None
                    img_filename = None
                    
                    if announcement.image:
                        image_cid = "announcement_test_img"
                        img_filename = os.path.basename(announcement.image.name)
                        try:
                            with announcement.image.open('rb') as f:
                                img_data = f.read()
                        except Exception as e:
                            self.message_user(request, f"Could not read image file for email CID embedding: {e}", level='warning')

                    context = {
                        'user': user,
                        'announcement': announcement,
                        'subject': announcement.title,
                        'body_html': body_html,
                        'image_cid': image_cid,
                        'image_url': f"{site_url}{announcement.image.url}" if announcement.image else None,
                    }
                    
                    html_message = render_to_string('email/announcement.html', context)
                    plain_text = f"{announcement.title}\n\nHi {user.username},\n\n{body_plain}\n\nVisit: {announcement.cta_link or site_url}"

                    try:
                        msg = EmailMultiAlternatives(
                            subject=f"[TEST] {announcement.title}",
                            body=plain_text,
                            from_email=settings.DEFAULT_FROM_EMAIL,
                            to=[user.email]
                        )
                        msg.attach_alternative(html_message, "text/html")
                        
                        if image_cid and img_data:
                            msg_img = MIMEImage(img_data)
                            msg_img.add_header('Content-ID', f'<{image_cid}>')
                            msg_img.add_header('Content-Disposition', 'inline', filename=img_filename)
                            msg.attach(msg_img)
                            
                        msg.send()
                    except Exception as e:
                        self.message_user(request, f"Email test failed: {e}", level='error')
                else:
                    self.message_user(request, f"Email test skipped: Admin user '{user.username}' does not have an email address set.", level='warning')

            sent_count += 1

        self.message_user(request, f"Sent test announcement to {request.user.email} for {sent_count} item(s).")

    @admin.action(description="Queue for sending")
    def queue_for_sending(self, request, queryset):
        updated = queryset.update(status='QUEUED')
        self.message_user(request, f"Queued {updated} announcement(s) for background sending.")
