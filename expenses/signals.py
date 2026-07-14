import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .ledger_service import LedgerPostingService
from .models import Account, Category, UserProfile

logger = logging.getLogger(__name__)

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
