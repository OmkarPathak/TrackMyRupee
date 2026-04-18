from django.urls import path
from django.views.generic import RedirectView, TemplateView

from . import views, views_payment
from .views.mom_view import mom_analysis_view

urlpatterns = [
    path('signup/', RedirectView.as_view(pattern_name='account_signup', permanent=True), name='signup'),
    path('', views.LandingPageView.as_view(), name='landing'),
    path('dashboard/', views.home_view, name='home'),
    path('budget/', views.BudgetDashboardView.as_view(), name='budget'),
    path('analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('year-in-review/', views.YearInReviewView.as_view(), name='year_in_review_default'),
    path('year-in-review/<int:year>/', views.YearInReviewView.as_view(), name='year_in_review'),
    path('analytics/mom/', mom_analysis_view, name='analytics-mom'),
    path('demo/', views.demo_login, name='demo_login'),
    path('demo-signup/', views.demo_signup, name='demo_signup'),
    path('upload/', views.upload_view, name='upload'),
    path('export/', views.export_expenses, name='export-expenses'),
    path('expenses/', views.ExpenseListView.as_view(), name='expense-list'),
    path('expenses/add/', views.ExpenseCreateView.as_view(), name='expense-create'),
    path('expenses/<int:pk>/edit/', views.ExpenseUpdateView.as_view(), name='expense-edit'),
    path('expenses/bulk-delete/', views.ExpenseBulkDeleteView.as_view(), name='expense-bulk-delete'),
    path('expenses/bulk-edit/', views.ExpenseBulkUpdateView.as_view(), name='expense-bulk-edit'),
    path('expenses/<int:pk>/delete/', views.ExpenseDeleteView.as_view(), name='expense-delete'),
    path('category/create/ajax/', views.create_category_ajax, name='category-create-ajax'),
    path('category/list/', views.CategoryListView.as_view(), name='category-list'),
    path('category/add/', views.CategoryCreateView.as_view(), name='category-create'),
    path('category/<int:pk>/edit/', views.CategoryUpdateView.as_view(), name='category-edit'),
    path('category/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category-delete'),
    
    # Income
    path('income/list/', views.IncomeListView.as_view(), name='income-list'),
    path('income/add/', views.IncomeCreateView.as_view(), name='income-create'),
    path('income/<int:pk>/edit/', views.IncomeUpdateView.as_view(), name='income-edit'),
    path('income/<int:pk>/delete/', views.IncomeDeleteView.as_view(), name='income-delete'),
    
    # Accounts
    path('accounts/list/', views.AccountListView.as_view(), name='account-list'),
    path('accounts/add/', views.AccountCreateView.as_view(), name='account-create'),
    path('accounts/<int:pk>/edit/', views.AccountUpdateView.as_view(), name='account-edit'),
    path('accounts/<int:pk>/delete/', views.AccountDeleteView.as_view(), name='account-delete'),
    path('accounts/<int:pk>/', views.AccountDetailView.as_view(), name='account-detail'),
    path('accounts/quick-add/', views.AccountQuickCreateView.as_view(), name='account-quick-create'),
    
    # Transfers
    path('transfers/', views.TransferListView.as_view(), name='transfer-list'), 
    path('transfers/add/', views.TransferCreateView.as_view(), name='transfer-create'),
    path('transfers/<int:pk>/edit/', views.TransferUpdateView.as_view(), name='transfer-edit'),
    path('transfers/<int:pk>/delete/', views.TransferDeleteView.as_view(), name='transfer-delete'),

    # Calendar
    path('calendar/', views.CalendarView.as_view(), name='calendar'),
    path('calendar/<int:year>/<int:month>/', views.CalendarView.as_view(), name='calendar-month'),
    # Recurring Transactions
    path('recurring/', views.RecurringTransactionListView.as_view(), name='recurring-list'),
    path('recurring/manage/', views.RecurringTransactionManageView.as_view(), name='recurring-manage'),
    path('pricing/', views.PricingView.as_view(), name='pricing'),
    path('onboarding/', views.OnboardingView.as_view(), name='onboarding'),
    path('recurring/create/', views.RecurringTransactionCreateView.as_view(), name='recurring-create'),
    path('recurring/<int:pk>/edit/', views.RecurringTransactionUpdateView.as_view(), name='recurring-edit'),
    path('recurring/<int:pk>/delete/', views.RecurringTransactionDeleteView.as_view(), name='recurring-delete'),
    path('settings/currency/', views.CurrencyUpdateView.as_view(), name='currency-settings'),
    path('settings/language/', views.LanguageUpdateView.as_view(), name='language-settings'),
    path('settings/profile/', views.ProfileUpdateView.as_view(), name='profile-settings'),
    path('settings/export/', views.DataExportView.as_view(), name='export-data'),
    path('settings/', views.SettingsHomeView.as_view(), name='settings-home'), # Settings Home
    path('family/', views.family_settings, name='family-settings'),
    path('family/invite/', views.family_invite, name='family-invite'),
    path('family/join/<code>/', views.family_join, name='family-join'),
    path('account/delete/', views.UserDeleteView.as_view(), name='user-delete'),
    path('tutorial/complete/', views.complete_tutorial, name='complete-tutorial'),
    
    # Savings Goals
    path('goals/', views.SavingsGoalListView.as_view(), name='goal-list'),
    path('goals/add/', views.SavingsGoalCreateView.as_view(), name='goal-create'),
    path('goals/<int:pk>/edit/', views.SavingsGoalUpdateView.as_view(), name='goal-edit'),
    path('goals/<int:pk>/delete/', views.SavingsGoalDeleteView.as_view(), name='goal-delete'),
    path('goals/<int:pk>/', views.SavingsGoalDetailView.as_view(), name='goal-detail'),
    path('goals/contribution/<int:pk>/edit/', views.GoalContributionUpdateView.as_view(), name='goal-contribution-edit'),
    path('goals/contribution/<int:pk>/delete/', views.GoalContributionDeleteView.as_view(), name='goal-contribution-delete'),
    
    # Static Pages
    path('privacy-policy/', TemplateView.as_view(template_name='privacy_policy.html'), name='privacy-policy'),
    path('terms-of-service/', TemplateView.as_view(template_name='terms_of_service.html'), name='terms-of-service'),
    path('refund-policy/', TemplateView.as_view(template_name='refund_policy.html'), name='refund-policy'),
    path('security/', TemplateView.as_view(template_name='security.html'), name='security'),
    path('about/', TemplateView.as_view(template_name='about.html'), name='about'),
    path('offline/', TemplateView.as_view(template_name='offline.html'), name='offline'),
    path('contact/', views.ContactView.as_view(), name='contact'),

    # to keep alive on render
    path('ping/', views.ping, name='ping'),

    # Payments
    path('api/create-order/', views_payment.create_order, name='create-order'),
    path('api/verify-payment/', views_payment.verify_payment, name='verify-payment'),
    path('api/razorpay-webhook/', views_payment.razorpay_webhook, name='razorpay-webhook'),
    path('api/cancel-subscription/', views_payment.cancel_subscription, name='cancel-subscription'),
    path('api/resend-verification/', views.resend_verification_email, name='resend-verification'),
    path('api/predict-category/', views.predict_category_view, name='predict-category'),
    path('api/parse-expense/', views.parse_expense_view, name='parse-expense'),
    path('api/start-trial/', views_payment.start_trial, name='start-trial'),
    
    # Notification URLs
    path('notifications/', views.NotificationListView.as_view(), name='notification-list'),
    path('notifications/mark-all-read/', views.mark_notifications_read, name='mark-all-read'),
    path('notifications/<int:pk>/read/', views.mark_single_notification_read, name='mark-single-notification-read'),
    path('notifications/<int:pk>/redirect/', views.notification_redirect, name='notification-redirect'),
    path('api/cron/send-notifications/', views.trigger_notifications, name='cron-send-notifications'),
    path('api/cron/send-lifecycle-emails/', views.trigger_lifecycle_emails, name='cron-send-lifecycle-emails'),
    path('api/cron/send-monthly-reports/', views.trigger_monthly_reports_view, name='cron-send-monthly-reports'),
    path('api/cron/send-daily-reminders/', views.trigger_daily_reminders_view, name='cron-send-daily-reminders'),

    # Sentry Debug
    path('sentry-debug/', lambda request: 1 / 0),
]
