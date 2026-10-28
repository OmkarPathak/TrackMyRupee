from django.contrib.auth.decorators import login_required
from django.urls import path, register_converter
from expenses.cache_utils import anonymous_cache_page
from django.views.generic import RedirectView, TemplateView

from . import views, views_payment
from .insights import views as insight_views
from .views.dashboard_networth import dashboard_networth
from .converters import UUIDOrIntConverter

register_converter(UUIDOrIntConverter, 'uuid_or_int')

urlpatterns = [
    path('health/', views.HealthCheckView.as_view(), name='health_check'),
    path('signup/', RedirectView.as_view(pattern_name='account_signup', permanent=True), name='signup'),
    path('', views.LandingPageView.as_view(), name='landing'),
    path('features/', views.FeaturesPageView.as_view(), name='features'),
    path('loan-emi-calculator/', views.LoanEMICalculatorPageView.as_view(), name='loan-emi-calculator'),
    path('dashboard/', views.home_view, name='home'),
    path('dashboard/networth/', dashboard_networth, name='dashboard-networth'),
    path('budget/', views.BudgetDashboardView.as_view(), name='budget'),
    path('api/budgets/', views.budgets_page, name='budgets-api'),
    path('api/budgets/limit/', views.budgets_set_limit, name='budgets-api-limit'),
    path('api/budgets/limits/', views.budgets_set_limits, name='budgets-api-limits'),
    path('api/budgets/rebalance/', views.budgets_rebalance, name='budgets-api-rebalance'),
    path('api/budgets/reset/', views.budgets_reset, name='budgets-api-reset'),
    path('api/budgets/suggestion/', views.budgets_suggestion_fragment, name='budgets-api-suggestion'),
    path('api/budgets/suggestion/dismiss/', views.budgets_dismiss_suggestion, name='budgets-api-dismiss'),
    path('analytics/', views.AnalyticsView.as_view(tab='spend'), name='analytics'),
    path('analytics/cashflow/', views.AnalyticsView.as_view(tab='cashflow'), name='analytics-cashflow'),
    path('analytics/net-worth/', views.AnalyticsView.as_view(tab='networth'), name='analytics-networth'),
    path('year-in-review/', views.YearInReviewView.as_view(), name='year_in_review_default'),
    path('year-in-review/<int:year>/', views.YearInReviewView.as_view(), name='year_in_review'),
    # the old Trends address, kept so bookmarks still work
    path('analytics/mom/', RedirectView.as_view(pattern_name='analytics-networth', query_string=True, permanent=True),
         name='analytics-mom'),
    path('demo/', views.demo_login, name='demo_login'),
    path('demo-signup/', views.demo_signup, name='demo_signup'),
    path('upload/', views.upload_view, name='upload'),
    path('upload/preview/', views.upload_preview_view, name='upload-preview'),
    path('imports/', views.import_history_view, name='import-history'),
    path('imports/<uuid:uuid>/undo/', views.import_undo_view, name='import-undo'),
    path('imports/<uuid:uuid>/skipped.csv', views.import_report_view, name='import-report'),
    path('partials/savings-guide/', login_required(TemplateView.as_view(template_name='partials/savings_modal.html')), name='savings-guide-partial'),
    path('export/', views.export_expenses, name='export-expenses'),
    path('transactions/', views.AllTransactionsListView.as_view(), name='all-transactions'),
    path('expenses/', views.ExpenseListView.as_view(), name='expense-list'),
    path('expenses/add/', views.ExpenseCreateView.as_view(), name='expense-create'),
    path('expenses/<uuid_or_int:pk>/edit/', views.ExpenseUpdateView.as_view(), name='expense-edit'),
    path('expenses/bulk-delete/', views.ExpenseBulkDeleteView.as_view(), name='expense-bulk-delete'),
    path('expenses/bulk-edit/', views.ExpenseBulkUpdateView.as_view(), name='expense-bulk-edit'),
    path('expenses/<uuid_or_int:pk>/delete/', views.ExpenseDeleteView.as_view(), name='expense-delete'),
    path('expenses/<uuid_or_int:pk>/convert-to-capital-event/', views.ExpenseConvertToCapitalEventView.as_view(), name='expense-convert'),
    path('category/create/ajax/', views.create_category_ajax, name='category-create-ajax'),
    path('category/list/', views.CategoryListView.as_view(), name='category-list'),
    path('category/add/', views.CategoryCreateView.as_view(), name='category-create'),
    path('category/bulk-delete/', views.CategoryBulkDeleteView.as_view(), name='category-bulk-delete'),
    path('category/<uuid_or_int:pk>/edit/', views.CategoryUpdateView.as_view(), name='category-edit'),
    path('category/<uuid_or_int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category-delete'),
    
    # Income
    path('income/list/', views.IncomeListView.as_view(), name='income-list'),
    path('income/add/', views.IncomeCreateView.as_view(), name='income-create'),
    path('income/<uuid_or_int:pk>/edit/', views.IncomeUpdateView.as_view(), name='income-edit'),
    path('income/<uuid_or_int:pk>/delete/', views.IncomeDeleteView.as_view(), name='income-delete'),
    
    # Accounts
    path('accounts/list/', views.AccountListView.as_view(), name='account-list'),
    path('accounts/add/', views.AccountCreateView.as_view(), name='account-create'),
    path('accounts/<uuid_or_int:pk>/edit/', views.AccountUpdateView.as_view(), name='account-edit'),
    path('accounts/<uuid_or_int:pk>/delete/', views.AccountDeleteView.as_view(), name='account-delete'),
    path('accounts/<uuid_or_int:pk>/restore/', views.AccountRestoreView.as_view(), name='account-restore'),
    path('accounts/<uuid_or_int:pk>/pin/', views.AccountPinToggleView.as_view(), name='account-toggle-pin'),
    path('accounts/<uuid_or_int:pk>/reconcile/', views.account_reconcile_view, name='account-reconcile'),
    path('accounts/<uuid_or_int:pk>/reconcile/confirm/', views.account_reconcile_confirm_view, name='account-reconcile-confirm'),
    path('accounts/<uuid_or_int:pk>/reconciliations/', views.account_reconciliation_history_view, name='account-reconciliation-history'),
    path('accounts/<uuid_or_int:pk>/', views.AccountDetailView.as_view(), name='account-detail'),
    path('accounts/<uuid_or_int:pk>/record-maturity-income/', views.RecordMaturityIncomeView.as_view(), name='account-record-maturity-income'),
    path('accounts/quick-add/', views.AccountQuickCreateView.as_view(), name='account-quick-create'),
    path('family/', views.family_home_view, name='family-home'),
    path('family/create/', views.family_create_view, name='family-create'),
    path('family/invite/', views.family_invite_view, name='family-invite'),
    path('family/invite/<uuid:pk>/cancel/', views.family_invite_cancel_view, name='family-invite-cancel'),
    path('family/invite/<uuid:pk>/renew/', views.family_invite_renew_view, name='family-invite-renew'),
    path('family/join/<str:token>/', views.family_join_view, name='family-join'),
    path('family/leave/', views.family_leave_view, name='family-leave'),
    path('family/net-worth/', views.family_networth_view, name='family-networth'),
    path('family/accounts/', views.family_inventory_view, name='family-inventory'),
    path('family/notes/dismiss/', views.family_note_dismiss_view, name='family-note-dismiss'),
    path('family/notes/toggle/', views.family_notes_toggle_view, name='family-notes-toggle'),
    path('family/share/', views.family_share_view, name='family-share'),
    path('family/share/notify/', views.family_share_notify_view, name='family-share-notify'),
    path('family/share/<uuid:pk>/', views.family_share_edit_view, name='family-share-edit'),
    path('family/share/<uuid:pk>/toggle/', views.family_share_toggle_view, name='family-share-toggle'),
    path('family/share/<uuid:pk>/stop/', views.family_share_stop_view, name='family-share-stop'),
    path('family/preview/', views.family_preview_view, name='family-preview'),
    path('family/preview/<int:pk>/', views.family_preview_view, name='family-preview-member'),
    path('family/members/<int:pk>/remove/', views.family_member_remove_view, name='family-member-remove'),
    path('api/amfi-schemes/search/', views.search_amfi_schemes, name='search-amfi-schemes'),
    path('api/filters/options/', views.FilterOptionsView.as_view(), name='filter-options-api'),
    path('holdings/', views.HoldingsListView.as_view(), name='holding-list'),
    path('holdings/add/', views.holding_create_view, name='holding-create-global'),
    path('holdings/<int:pk>/refresh-nav/', views.refresh_holding_nav, name='refresh-holding-nav'),
    path('accounts/<uuid_or_int:pk>/holdings/add/', views.holding_create_view, name='holding-create'),
    path('accounts/<uuid_or_int:pk>/refresh-nav/', views.refresh_account_navs, name='refresh-account-navs'),
    path('holdings/<int:pk>/delete/', views.holding_delete_view, name='holding-delete'),
    path('holdings/<int:pk>/valuation/', views.holding_valuation_view, name='holding-valuation'),
    path('holdings/<int:pk>/', views.HoldingDetailView.as_view(), name='holding-detail'),
    path('holdings/<int:pk>/buy/', views.holding_buy_view, name='holding-buy'),
    path('holdings/<int:pk>/rename/', views.holding_rename_view, name='holding-rename'),
    path('holdings/<int:pk>/sell/', views.holding_sell_view, name='holding-sell'),
    path('holdings/purchases/<int:pk>/edit/', views.holding_purchase_edit_view, name='holding-purchase-edit'),
    path('holdings/purchases/<int:pk>/delete/', views.holding_purchase_delete_view, name='holding-purchase-delete'),
    
    # Transfers
    path('transfers/', views.TransferListView.as_view(), name='transfer-list'), 
    path('transfers/add/', views.TransferCreateView.as_view(), name='transfer-create'),
    path('transfers/<uuid_or_int:pk>/edit/', views.TransferUpdateView.as_view(), name='transfer-edit'),
    path('transfers/<uuid_or_int:pk>/delete/', views.TransferDeleteView.as_view(), name='transfer-delete'),

    # Calendar
    path('calendar/', views.CalendarView.as_view(), name='calendar'),
    path('calendar/<int:year>/<int:month>/', views.CalendarView.as_view(), name='calendar-month'),
    path('calendar/day/<int:year>/<int:month>/<int:day>/', views.CalendarDayView.as_view(), name='calendar-day'),
    # Recurring Transactions
    path('recurring/', views.RecurringTransactionListView.as_view(), name='recurring-list'),
    path('pricing/', views.PricingView.as_view(), name='pricing'),
    path('onboarding/', views.OnboardingView.as_view(), name='onboarding'),
    path('recurring/create/', views.RecurringTransactionCreateView.as_view(), name='recurring-create'),
    path('recurring/<uuid_or_int:pk>/edit/', views.RecurringTransactionUpdateView.as_view(), name='recurring-edit'),
    path('recurring/<uuid_or_int:pk>/delete/', views.RecurringTransactionDeleteView.as_view(), name='recurring-delete'),
    path('settings/currency/', views.CurrencyUpdateView.as_view(), name='currency-settings'),
    path('settings/language/', views.LanguageUpdateView.as_view(), name='language-settings'),
    path('settings/profile/', views.ProfileUpdateView.as_view(), name='profile-settings'),
    path('settings/export/', views.DataExportView.as_view(), name='export-data'),
    path('settings/', views.SettingsHomeView.as_view(), name='settings-home'), # Settings Home
    path('account/delete/', views.UserDeleteView.as_view(), name='user-delete'),
    path('consent/', views.dpdp_consent_view, name='dpdp_consent'),
    path('settings/consent/withdraw/', views.WithdrawConsentView.as_view(), name='withdraw-consent'),
    path('tutorial/complete/', views.complete_tutorial, name='complete-tutorial'),

    # TMR Flows
    path('flows/', views.FlowLandingView.as_view(), name='tmr-flows'),
    path('flows/landing/', views.FlowLandingView.as_view(), name='flow-landing'),

    path('flows/<str:key>/', views.FlowDetailView.as_view(), name='flow-detail'),
    path('flows/<str:key>/preview/', views.FlowPreviewView.as_view(), name='flow-preview'),
    path('flows/<str:key>/commit/', views.FlowCommitView.as_view(), name='flow-commit'),
    
    # Savings Goals
    path('goals/', views.SavingsGoalListView.as_view(), name='goal-list'),
    path('goals/add/', views.SavingsGoalCreateView.as_view(), name='goal-create'),
    path('goals/<uuid_or_int:pk>/edit/', views.SavingsGoalUpdateView.as_view(), name='goal-edit'),
    path('goals/<uuid_or_int:pk>/delete/', views.SavingsGoalDeleteView.as_view(), name='goal-delete'),
    path('goals/<uuid_or_int:pk>/', views.SavingsGoalDetailView.as_view(), name='goal-detail'),
    path('goals/contribution/<uuid_or_int:pk>/edit/', views.GoalContributionUpdateView.as_view(), name='goal-contribution-edit'),
    path('goals/contribution/<uuid_or_int:pk>/delete/', views.GoalContributionDeleteView.as_view(), name='goal-contribution-delete'),
    
    # Static Pages
    path('tutorial/', RedirectView.as_view(url='https://docs.trackmyrupee.com', permanent=True), name='tutorial'),
    path('privacy-policy/', anonymous_cache_page(86400)(TemplateView.as_view(template_name='privacy_policy.html')), name='privacy-policy'),
    path('terms-of-service/', anonymous_cache_page(86400)(TemplateView.as_view(template_name='terms_of_service.html')), name='terms-of-service'),
    path('refund-policy/', anonymous_cache_page(86400)(TemplateView.as_view(template_name='refund_policy.html')), name='refund-policy'),
    path('security/', anonymous_cache_page(86400)(TemplateView.as_view(template_name='security.html')), name='security'),
    path('about/', anonymous_cache_page(86400)(TemplateView.as_view(template_name='about.html')), name='about'),
    path('offline/', TemplateView.as_view(template_name='offline.html'), name='offline'),
    path('contact/', views.ContactView.as_view(), name='contact'),

    # Alternatives Landing Pages
    path('alternatives/', views.AlternativesHubView.as_view(), name='alternatives'),
    path('alternatives/axio-alternative/', views.AlternativePageView.as_view(competitor='axio'), name='axio-alternative'),
    path('alternatives/walnut-alternative-no-sms/', views.AlternativePageView.as_view(competitor='walnut'), name='walnut-alternative-no-sms'),
    path('alternatives/indmoney-alternative-privacy/', views.AlternativePageView.as_view(competitor='indmoney'), name='indmoney-alternative-privacy'),


    # to keep alive on render
    path('ping/', views.ping, name='ping'),

    # Payments
    path('settings/payments/', views_payment.PaymentHistoryView.as_view(), name='payment-history'),
    path('api/create-order/', views_payment.create_order, name='create-order'),
    path('api/verify-payment/', views_payment.verify_payment, name='verify-payment'),
    path('api/razorpay-webhook/', views_payment.razorpay_webhook, name='razorpay-webhook'),
    path('api/cancel-subscription/', views_payment.cancel_subscription, name='cancel-subscription'),
    path('api/resend-verification/', views.resend_verification_email, name='resend-verification'),
    path('api/predict-category/', views.predict_category_view, name='predict-category'),
    path('api/parse-expense/', views.parse_expense_view, name='parse-expense'),
    path('api/expense-composer/', views.expense_composer_data, name='expense-composer-data'),
    path('api/expense-composer/save/', views.expense_composer_save, name='expense-composer-save'),
    path('api/expense-composer/event/', views.expense_composer_event, name='expense-composer-event'),
    path('expenses/<uuid:pk>/undo/', views.expense_composer_undo, name='expense-composer-undo'),
    path('api/start-trial/', views_payment.start_trial, name='start-trial'),
    path('api/update-pwa-login/', views.UpdatePWALoginView.as_view(), name='update-pwa-login'),
    
    # Notification URLs
    path('notifications/', views.NotificationListView.as_view(), name='notification-list'),
    path('notifications/mark-all-read/', views.mark_notifications_read, name='mark-all-read'),
    path('notifications/<uuid_or_int:pk>/read/', views.mark_single_notification_read, name='mark-single-notification-read'),
    path('notifications/<uuid_or_int:pk>/redirect/', views.notification_redirect, name='notification-redirect'),
    # Insights Engine
    path('insights/panel/', insight_views.panel, name='insights-panel'),
    path('insights/settings/', insight_views.settings_save, name='insights-settings'),
    path('insights/<uuid:state_uuid>/dismiss/', insight_views.dismiss, name='insights-dismiss'),
    path('insights/<uuid:state_uuid>/snooze/', insight_views.snooze, name='insights-snooze'),
    path('insights/<uuid:state_uuid>/helpful/<str:verdict>/', insight_views.helpful, name='insights-helpful'),
    path('insights/<uuid:state_uuid>/go/', insight_views.go, name='insights-go'),
    path('api/cron/generate-insights/', insight_views.trigger_generate, name='cron-generate-insights'),
    path('api/cron/deliver-insights/', insight_views.trigger_deliver, name='cron-deliver-insights'),
    path('api/cron/insight-digest/', insight_views.trigger_digest, name='cron-insight-digest'),
    path('insights/digest/unsubscribe/<str:token>/', insight_views.digest_unsubscribe, name='insights-digest-unsubscribe'),
    path('api/cron/send-notifications/', views.trigger_notifications, name='cron-send-notifications'),
    path('api/cron/send-lifecycle-emails/', views.trigger_lifecycle_emails, name='cron-send-lifecycle-emails'),
    path('api/cron/send-monthly-reports/', views.trigger_monthly_reports_view, name='cron-send-monthly-reports'),
    path('api/cron/send-daily-reminders/', views.trigger_daily_reminders_view, name='cron-send-daily-reminders'),
    path('api/cron/capture-net-worth-snapshots/', views.trigger_net_worth_snapshots_view, name='cron-capture-net-worth-snapshots'),
    path('api/cron/send-announcements/', views.trigger_announcements, name='cron-send-announcements'),
    path('api/cron/ledger/retry-failures/', views.trigger_ledger_retry_view, name='cron-ledger-retry-failures'),
    path('api/cron/ledger/reconcile/', views.trigger_ledger_reconcile_view, name='cron-ledger-reconcile'),
    path('api/cron/ledger/maintenance/', views.trigger_ledger_maintenance_view, name='cron-ledger-maintenance'),
    path('api/cron/sync-nav/', views.trigger_sync_nav_cron_view, name='cron-sync-nav'),
    path('api/cron/sync-funds/', views.trigger_sync_funds_cron_view, name='cron-sync-funds'),

    # Loans
    path('loans/', views.LoanListView.as_view(), name='loan-list'),
    path('loans/add/', views.LoanCreateView.as_view(), name='loan-create'),
    path('loans/<uuid_or_int:pk>/edit/', views.LoanUpdateView.as_view(), name='loan-edit'),
    path('loans/<uuid_or_int:pk>/delete/', views.LoanDeleteView.as_view(), name='loan-delete'),
    path('loans/<uuid_or_int:pk>/', views.LoanDetailView.as_view(), name='loan-detail'),
    path('loans/<uuid_or_int:pk>/tab/schedule/', views.LoanScheduleTabView.as_view(), name='loan-tab-schedule'),
    path('loans/<uuid_or_int:pk>/tab/history/', views.LoanHistoryTabView.as_view(), name='loan-tab-history'),
    path('loans/<uuid_or_int:pk>/repayment/', views.LoanRepaymentCreateView.as_view(), name='loan-repayment-create'),
    path('loans/repayment/<uuid_or_int:pk>/delete/', views.LoanRepaymentDeleteView.as_view(), name='loan-repayment-delete'),
    path('loans/<uuid_or_int:pk>/rate-update/', views.LoanInterestRateCreateView.as_view(), name='loan-rate-update'),

    # DNS-over-HTTPS (DoH) resolver endpoints for DNS-AID
    path('dns-query', views.doh_handler_view, name='dns-query-short'),
    path('dns-query/', views.doh_handler_view, name='dns-query-short-slash'),
    path('.well-known/dns-query', views.doh_handler_view, name='dns-query-wellknown'),
    path('.well-known/dns-query/', views.doh_handler_view, name='dns-query-wellknown-slash'),

    # Sentry Debug
    path('sentry-debug/', lambda request: 1 / 0),
    # Capital Events
    path('capital-events/', views.CapitalEventListView.as_view(), name='capital-event-list'),
    path('capital-events/add/', views.CapitalEventCreateView.as_view(), name='capital-event-create'),
    path('capital-events/<uuid_or_int:pk>/edit/', views.CapitalEventUpdateView.as_view(), name='capital-event-edit'),
    path('capital-events/<uuid_or_int:pk>/delete/', views.CapitalEventDeleteView.as_view(), name='capital-event-delete'),
    path('capital-events/<uuid_or_int:pk>/convert-to-expense/', views.CapitalEventConvertToExpenseView.as_view(), name='capital-event-convert'),
    path('api/capital-events/loans/', views.capital_event_loans_ajax, name='capital-event-loans-ajax'),
]

