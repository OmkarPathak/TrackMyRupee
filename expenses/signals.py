import logging

from allauth.account.signals import user_logged_in, user_signed_up
from django.conf import settings
from django.contrib.auth.models import User
from django.db.backends.signals import connection_created
from django.db.models.signals import post_save
from django.dispatch import receiver

from .ledger_service import LedgerPostingService
from .models import Account, Category, PhysicalAsset, RecurringTransaction, UserProfile
from .posthog_utils import ph_capture, ph_identify

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
    """Capture a login event and refresh PostHog person properties."""
    sociallogin = kwargs.get('sociallogin')
    method = 'google' if sociallogin else 'email'
    ph_identify(user)  # refreshes tier / email in case they changed
    ph_capture(user, 'user_logged_in', {'method': method})



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

                html_message = render_to_string('email/welcome_email.html', {
                    'user': instance,
                })

                send_mail(
                    subject='Welcome to TrackMyRupee! 🎉',
                    message='Welcome to TrackMyRupee! Start tracking your finances today.',
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[instance.email],
                    html_message=html_message,
                )
                logger.info(f"Welcome email sent to {instance.email}")
            except Exception as e:
                logger.error(f"Failed to send welcome email to {instance.email}: {e}")
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
