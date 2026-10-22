import logging

from allauth.account.signals import user_logged_in, user_signed_up
from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.backends.signals import connection_created
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from django.utils import timezone

from .ledger_service import LedgerPostingService
from .models import (
    Account,
    BudgetLimitVersion,
    BudgetMonthOverride,
    CapitalEvent,
    Category,
    Expense,
    GoalContribution,
    Holding,
    HoldingTransaction,
    Income,
    Loan,
    LoanInterestRate,
    LoanRepayment,
    Notification,
    PhysicalAsset,
    RecurringTransaction,
    SavingsGoal,
    Transfer,
    UserProfile,
    Valuation,
)
from .posthog_utils import ph_capture, ph_identify
from .services import FinancialService, LoanService

logger = logging.getLogger(__name__)


@receiver(user_signed_up)
def on_user_signed_up(sender, request, user, **kwargs):
    """Identify the new user in PostHog and capture a signup event."""
    sociallogin = kwargs.get('sociallogin')
    method = 'google' if sociallogin else 'email'
    ph_identify(user, {'signup_method': method})
    ph_capture(user, 'user_signed_up', {'method': method})


@receiver(user_logged_in)
def on_user_logged_in(sender, request, user, **kwargs):
    """Capture a login event, refresh PostHog person properties, and pre-warm the dashboard cache."""
    sociallogin = kwargs.get('sociallogin')
    method = 'google' if sociallogin else 'email'
    ph_identify(user)  # refreshes tier / email in case they changed
    ph_capture(user, 'user_logged_in', {'method': method})

    # Pre-warm the monthly-history cache so it's already hot by the time the post-login
    # redirect lands on the dashboard.
    try:
        FinancialService.get_monthly_history(user, 6)
    except Exception:
        logger.warning("Failed to pre-warm dashboard cache for user %s", user.id, exc_info=True)



@receiver(connection_created)
def configure_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute('PRAGMA journal_mode=WAL;')
            cursor.execute('PRAGMA synchronous=NORMAL;')
            cursor.execute('PRAGMA busy_timeout=30000;')

@receiver(post_save, sender=User)
def handle_user_post_save(sender, instance, created, **kwargs):
    """Unified handler for User post_save to reduce redundant queries during signup."""
    if kwargs.get('raw', False):
        return

    if created:
        # 1. Create UserProfile
        profile, profile_created = UserProfile.objects.get_or_create(user=instance)
        import sys
        if 'test' in sys.argv:
            profile.consent_granted = True
            profile.save(update_fields=['consent_granted'])
        
        # 2. Create Default Categories using bulk_create to avoid N+1
        default_categories = [
            ('Food', 'bi-cup-hot'),
            ('Shopping', 'bi-cart3'),
            ('Bills', 'bi-receipt'),
        ]
        Category.objects.bulk_create([
            Category(user=instance, name=name, icon=icon) 
            for name, icon in default_categories
        ], ignore_conflicts=True)
        
        # 3. Send welcome email (skip demo user)
        if instance.email and instance.username != 'demo':
            try:
                from django.core.mail import send_mail
                from django.template.loader import render_to_string

                from finance_tracker.plans import PLAN_DETAILS
                context = {
                    'user': instance,
                    'free': PLAN_DETAILS['FREE']['limits'],
                    'plus_price': PLAN_DETAILS['PLUS']['price_monthly'],
                }
                html_message = render_to_string('email/welcome_email.html', context)
                text_message = render_to_string('email/welcome_email.txt', context)

                send_mail(
                    subject='Welcome to TrackMyRupee! 🎉',
                    message=text_message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[instance.email],
                    html_message=html_message,
                )
                logger.info(f"Welcome email sent to user {instance.pk}")
            except Exception as e:
                logger.error(f"Failed to send welcome email to user {instance.pk}: {e}")
    else:
        # Handle profile saving for existing users
        if hasattr(instance, 'profile'):
            instance.profile.save()
        else:
            UserProfile.objects.get_or_create(user=instance)


@receiver(post_save, sender=Account)
def handle_account_post_save(sender, instance, created, **kwargs):
    """Post an opening balance ledger entry when a new Account is created.

    This ensures every account has an opening entry in the ledger from the
    moment it is created, so LedgerReadService.get_account_balance() and
    get_net_worth() never fall back to account.balance for newly created accounts.

    Only runs when LEDGER_WRITE_ENABLED=True to match the ledger shadow-write gate.
    The opening entry carries the marker metadata key 'opening_account_id' which
    is what LedgerReadService._get_opening_account_ids() looks for.
    """
    if kwargs.get('raw', False):
        return
    if not created:
        return
    import sys
    if 'test' in sys.argv:
        return
    if not getattr(settings, 'LEDGER_WRITE_ENABLED', False):
        return

    try:
        LedgerPostingService.post_opening_balance(account=instance)
    except Exception as exc:
        # Do not raise — the account was saved successfully; ledger failure is non-fatal
        # (LedgerPostingFailure will be logged via _run_ledger_shadow if needed)
        logger.warning(
            "Failed to post opening balance for account %s (%s): %s",
            instance.id, instance.name, exc,
        )


@receiver(post_save, sender=Account)
def handle_account_deactivation(sender, instance, **kwargs):
    """Deactivate linked RecurringTransaction schedules when an Account is deactivated."""
    if kwargs.get('raw', False):
        return
    if not instance.is_active:
        from django.db.models import Q
        RecurringTransaction.objects.filter(
            Q(account=instance) | Q(from_account=instance) | Q(to_account=instance),
            is_active=True
        ).update(is_active=False)
        if instance.linked_physical_asset:
            RecurringTransaction.objects.filter(physical_asset=instance.linked_physical_asset, is_active=True).update(is_active=False)


@receiver(post_save, sender=PhysicalAsset)
def handle_physical_asset_deactivation(sender, instance, **kwargs):
    """Deactivate linked RecurringTransaction schedules when a PhysicalAsset policy is deactivated."""
    if kwargs.get('raw', False):
        return
    if not instance.is_active:
        RecurringTransaction.objects.filter(physical_asset=instance, is_active=True).update(is_active=False)


@receiver(post_save, sender=LoanRepayment)
@receiver(post_delete, sender=LoanRepayment)
def handle_loan_repayment_active_status(sender, instance, **kwargs):
    """Sync loan active status when a LoanRepayment is saved or deleted."""
    if kwargs.get('raw', False):
        return
    if getattr(instance, 'loan_id', None):
        try:
            LoanService.sync_loan_active_status(instance.loan)
        except Loan.DoesNotExist:
            pass


@receiver(post_save, sender=CapitalEvent)
@receiver(post_delete, sender=CapitalEvent)
def handle_capital_event_loan_active_status(sender, instance, **kwargs):
    """Sync loan active status when a CapitalEvent with a linked loan is saved or deleted."""
    if kwargs.get('raw', False):
        return
    if getattr(instance, 'linked_loan_id', None):
        try:
            LoanService.sync_loan_active_status(instance.linked_loan)
        except Loan.DoesNotExist:
            pass


def _dashboard_cache_user_id(instance):
    """Resolve the owning user id for cache-invalidation, regardless of model shape."""
    if isinstance(instance, (LoanRepayment, LoanInterestRate)):
        try:
            return instance.loan.user_id
        except Exception:
            return None
    if isinstance(instance, Holding):
        try:
            return instance.account.user_id
        except Exception:
            return None
    if isinstance(instance, Valuation):
        try:
            return instance.holding.account.user_id
        except Exception:
            return None
    if isinstance(instance, GoalContribution):
        try:
            return instance.goal.user_id
        except Exception:
            return None
    if isinstance(instance, User):
        return instance.id
    if isinstance(instance, UserProfile):
        return instance.user_id
    return getattr(instance, 'user_id', None)


def invalidate_dashboard_cache(sender=None, instance=None, user_id=None, **kwargs):
    """Invalidate cached dashboard data whenever a transaction affecting it is saved/deleted.

    Keeps the LocMemCache-backed dashboard (home_default_data / monthly_summary_map /
    monthly_history) from ever serving stale numbers after the user logs an expense,
    salary, transfer, loan repayment, capital event, or account change.
    """
    if kwargs.get('raw', False):
        return
    from .cache_utils import dashboard_invalidation_suppressed
    if dashboard_invalidation_suppressed() and not kwargs.get('force'):
        return
    if user_id is None and instance is not None:
        try:
            user_id = _dashboard_cache_user_id(instance)
        except Exception:
            return
    if not user_id:
        return
    try:
        today = timezone.localdate()
        cache.delete_many([
            f'home_default_data_{user_id}',
            f'monthly_summary_map_{user_id}',
            f'monthly_history_{user_id}_6',
            f'recurring_processed_{user_id}_{today}',
            f'account_net_worth_{user_id}',
            f'budget_dashboard_{user_id}',
            f'budget_suggestion_rows_{user_id}',
            f'analytics_version_{user_id}',
            f'analytics_nw_points_{user_id}',
            f'category_icon_map_{user_id}',
        ])
    except Exception:
        pass
    _mark_insights_stale(user_id)


def _mark_insights_stale(user_id):
    """The person's stored insights were worked out before this change: the next time they open the dashboard (or a
    page with an insights strip) the quick rules run again. A cache write, so a save costs no extra query."""
    try:
        from types import SimpleNamespace

        from .insights import is_engine_on
        if not user_id or not is_engine_on(SimpleNamespace(pk=user_id)):
            return          # the engine is not running for this person: a save costs nothing extra
        from .insights.engine import mark_stale
        mark_stale(user_id)
    except Exception:
        pass


_DASHBOARD_CACHE_MODELS = (
    Expense,
    Income,
    Transfer,
    LoanRepayment,
    CapitalEvent,
    Account,
    RecurringTransaction,
    Category,
    SavingsGoal,
    GoalContribution,
    Loan,
    LoanInterestRate,
    UserProfile,
    Holding,
    Valuation,
)
for _model in _DASHBOARD_CACHE_MODELS:
    post_save.connect(invalidate_dashboard_cache, sender=_model, dispatch_uid=f'dashboard_cache_invalidate_save_{_model.__name__}')
    post_delete.connect(invalidate_dashboard_cache, sender=_model, dispatch_uid=f'dashboard_cache_invalidate_delete_{_model.__name__}')


def _insights_stale_on_budget_change(sender=None, instance=None, **kwargs):
    if not kwargs.get('raw', False):
        _mark_insights_stale(getattr(instance, 'user_id', None))


for _model in (BudgetLimitVersion, BudgetMonthOverride):
    post_save.connect(_insights_stale_on_budget_change, sender=_model, dispatch_uid=f'insights_stale_save_{_model.__name__}')
    post_delete.connect(_insights_stale_on_budget_change, sender=_model, dispatch_uid=f'insights_stale_delete_{_model.__name__}')


# --- global_badge_data cache invalidation (see context_processors.global_badge_data) ---
def invalidate_global_badge_data(sender=None, instance=None, **kwargs):
    """Drop the combined navbar/sidebar badge cache when any model it reads changes."""
    if kwargs.get('raw', False):
        return
    try:
        user_id = _dashboard_cache_user_id(instance)
        if user_id:
            from .context_processors import global_badge_data_cache_key
            cache.delete(global_badge_data_cache_key(user_id))
    except Exception:
        pass


def _connect_global_badge_invalidation():
    from webpush.models import PushInformation

    for _model in (Notification, SavingsGoal, RecurringTransaction, Loan, Account, PushInformation):
        post_save.connect(invalidate_global_badge_data, sender=_model, dispatch_uid=f'global_badge_data_save_{_model.__name__}')
        post_delete.connect(invalidate_global_badge_data, sender=_model, dispatch_uid=f'global_badge_data_delete_{_model.__name__}')


_connect_global_badge_invalidation()


@receiver(post_save, sender=Income)
@receiver(post_delete, sender=Income)
@receiver(post_save, sender=Expense)
@receiver(post_delete, sender=Expense)
def clear_interest_summary_cache(sender, instance, **kwargs):
    """The accounts page shows interest earned and charged; recording or removing a transaction must be
    reflected at once, not after the summary's five-minute cache runs out."""
    if getattr(instance, 'user_id', None):
        cache.delete(f'interest_summary_{instance.user_id}_None_None')


@receiver(post_save, sender=HoldingTransaction)
def recompute_holding_after_purchase_saved(sender, instance, **kwargs):
    """A holding's units and average cost always follow its purchases."""
    if kwargs.get('raw', False):
        return
    from .holdings_service import recompute
    recompute(instance.holding_id)


@receiver(pre_delete, sender=HoldingTransaction)
def remove_sale_profit_before_delete(sender, instance, **kwargs):
    """The profit or loss a sale recorded in the ledger leaves with it. Done while the row still exists, because
    the copy being deleted may be an older one that does not know the links recompute has set since."""
    if instance.kind == 'SELL':
        from .holdings_service import remove_realized
        remove_realized(instance)


@receiver(post_delete, sender=HoldingTransaction)
def recompute_holding_after_purchase_deleted(sender, instance, **kwargs):
    from .holdings_service import recompute
    recompute(instance.holding_id, removed=True)
