import logging

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from finance_tracker.plans import PLAN_DETAILS

logger = logging.getLogger(__name__)


def _count(value, noun):
    return f"Unlimited {noun}" if value == -1 else f"Up to {value} {noun}"


def plan_highlights(tier):
    """What the plan gives, worded from the live plan limits so the email cannot drift from the app."""
    limits = PLAN_DETAILS[tier]['limits']
    lines = [
        'Unlimited expenses every month',
        _count(limits['accounts'], 'accounts'),
        _count(limits['loans'], 'loans'),
        _count(limits['recurring_transactions'], 'recurring transactions'),
        _count(limits['budget_categories'], 'budget categories'),
        _count(limits['savings_goals'], 'savings goals'),
        'Net worth tracking with ' + (
            'full history' if limits['net_worth_history'] == -1 else f"{limits['net_worth_history']} months of history"
        ),
    ]
    if limits.get('email_notifications'):
        lines.append('Alert emails for bills, budgets and goals')
    if limits.get('year_in_review'):
        lines.append('Year in Review')
    return lines


def send_upgrade_thank_you(user, payment):
    """Thank a user for their first payment on a plan. Never raises: a mail problem must not undo a payment."""
    if not user.email or user.username == 'demo' or payment.tier not in ('PLUS', 'PRO'):
        return False
    try:
        profile = user.profile
        tier_name = str(PLAN_DETAILS[payment.tier]['name'])
        lifetime = payment.duration == 'LIFETIME' or profile.is_lifetime
        context = {
            'user': user,
            'tier_name': tier_name,
            'duration_label': {'MONTHLY': 'monthly', 'YEARLY': 'yearly', 'LIFETIME': 'lifetime'}.get(payment.duration, ''),
            'amount': payment.amount,
            'lifetime': lifetime,
            'valid_until': None if lifetime else profile.subscription_end_date,
            'auto_renews': bool(profile.razorpay_subscription_id) and payment.order_id == profile.razorpay_subscription_id,
            'highlights': plan_highlights(payment.tier),
            'base_url': getattr(settings, 'SITE_URL', 'https://trackmyrupee.com').rstrip('/'),
        }
        html_message = render_to_string('email/upgrade_thank_you.html', context)
        text_message = render_to_string('email/upgrade_thank_you.txt', context)
        delivered = send_mail(
            subject=f'Thank you for upgrading to {tier_name}',
            message=text_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            html_message=html_message,
        )
        return bool(delivered)
    except Exception as e:
        logger.error(f"Failed to send upgrade thank-you email to user {user.pk}: {e}")
        return False
