import logging
import threading

from allauth.socialaccount.models import SocialAccount
from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.views.generic import DeleteView, TemplateView, UpdateView

from ..forms import LanguageUpdateForm, ProfileUpdateForm
from ..models import (
    Account,
    DeletionRequestAuditLog,
    Expense,
    Income,
    Notification,
    RecurringTransaction,
    UserProfile,
)
from ..posthog_utils import ph_capture
from ..signals import invalidate_dashboard_cache

logger = logging.getLogger(__name__)


def log_and_notify_deletion(user=None, username=None, email=None):
    if user is not None and hasattr(user, 'username'):
        username = user.username
        email = user.email
    elif isinstance(user, str):
        email = username
        username = user

    # 1. Log the deletion request
    DeletionRequestAuditLog.objects.create(email=email, username=username)

    # 2. Send confirmation email
    if email:
        try:
            from django.core.mail import send_mail
            from django.template.loader import render_to_string

            subject = _("Account Deleted - TrackMyRupee")
            html_message = render_to_string('email/account_deleted_email.html', {
                'username': username,
            })
            text_message = render_to_string('email/account_deleted_email.txt', {
                'username': username,
            })

            send_mail(
                subject=subject,
                message=text_message,
                from_email=django_settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                html_message=html_message,
            )
            logger.info(f"Account deletion confirmation email sent to {email}")
        except Exception as e:
            logger.error(f"Failed to send account deletion email to {email}: {e}")


def delete_user_account(user_id):
    """
    Deletes the user and all associated data inside an atomic transaction.
    Upon successful deletion, records an audit log entry and sends a confirmation email.
    If deletion fails, logs the exception and does not create an audit entry or send an email.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} does not exist for account deletion")
        return False

    username = user.username
    email = user.email

    try:
        with transaction.atomic():
            user.delete()
        log_and_notify_deletion(username=username, email=email)
        logger.info(f"Successfully deleted account for user {user_id} ({username})")
        return True
    except Exception as e:
        logger.exception(f"Account deletion failed for user {user_id} ({username}): {e}")
        return False
    finally:
        cache.delete(f'account_deletion_lock_{user_id}')


def dispatch_account_deletion(user_id, run_async=None):
    """
    Guarded by a user-scoped cache lock to prevent overlapping runs.
    Dispatches delete_user_account in a background thread by default.
    """
    lock_key = f'account_deletion_lock_{user_id}'
    if not cache.add(lock_key, 1, timeout=600):
        logger.warning(f"Account deletion already in progress for user {user_id}")
        return False

    if run_async is None:
        run_async = getattr(django_settings, 'ACCOUNT_DELETION_ASYNC', True)

    def _run():
        try:
            delete_user_account(user_id)
        finally:
            cache.delete(lock_key)

    if not run_async:
        _run()
        return True

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return True


class SettingsHomeView(LoginRequiredMixin, TemplateView):
    template_name = 'expenses/settings_home.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        cache_key = f'settings_summary_counts_{user.id}'
        cached_data = cache.get(cache_key)
        if cached_data is None:
            num_accounts = Account.objects.filter(user=user).count()
            num_expenses = Expense.objects.filter(user=user).count()
            num_incomes = Income.objects.filter(user=user).count()
            num_transactions = num_expenses + num_incomes
            cached_data = {'num_accounts': num_accounts, 'num_transactions': num_transactions}
            cache.set(cache_key, cached_data, 300)

        context['num_accounts'] = cached_data['num_accounts']
        context['num_transactions'] = cached_data['num_transactions']
        return context


class UserDeleteView(LoginRequiredMixin, DeleteView):
    model = django_settings.AUTH_USER_MODEL # Handled via get_object
    success_url = reverse_lazy('landing')
    template_name = 'expenses/account_confirm_delete.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        user = self.get_object()
        user_id = user.id
        dispatched = dispatch_account_deletion(user_id)
        logout(self.request)
        if dispatched:
            ph_capture(user, 'account_deleted', {})
            messages.success(self.request, _("Your account deletion has been initiated. You will no longer be able to log in."))
        else:
            messages.warning(self.request, _("Account deletion is already in progress."))
        return redirect(self.success_url)

class WithdrawConsentView(LoginRequiredMixin, DeleteView):
    model = django_settings.AUTH_USER_MODEL
    success_url = reverse_lazy('landing')
    template_name = 'expenses/withdraw_consent_confirm.html'

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        user = self.get_object()
        user_id = user.id
        dispatched = dispatch_account_deletion(user_id)
        logout(self.request)
        if dispatched:
            messages.success(self.request, _("Your consent has been withdrawn and your account deletion has been initiated as per DPDPA requirements. You will no longer be able to log in."))
        else:
            messages.warning(self.request, _("Account deletion is already in progress."))
        return redirect(self.success_url)


def recalculate_user_transactions(user_id, old_currency, new_currency):
    """
    Recalculates exchange_rate and base_amount for all user transactions (Expense, Income, RecurringTransaction)
    in a single atomic transaction. If any transaction fails, all changes roll back to prevent partial-completion
    data corruption, user profile currency is reverted to old_currency, and a failure Notification is created.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    try:
        user = User.objects.select_related('profile').get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"User {user_id} does not exist for currency recalculation")
        return False

    user.profile.refresh_from_db()
    if user.profile.currency != new_currency:
        user.profile.currency = new_currency
        user.profile.save(update_fields=['currency'])

    success = False
    try:
        with transaction.atomic():
            for model in [Expense, Income, RecurringTransaction]:
                transactions = list(model.objects.filter(user=user))
                for tx in transactions:
                    tx.user = user
                    tx.save()

        success = True
        invalidate_dashboard_cache(user_id=user.id)

        try:
            link = str(reverse_lazy('currency-settings'))
        except Exception:
            link = None

        Notification.objects.create(
            user=user,
            title=_("Currency Recalculation Completed"),
            message=_("All transactions have been recalculated to your new base currency (%(currency)s).") % {'currency': new_currency},
            notification_type='SYSTEM',
            link=link,
        )
        logger.info(f"Successfully recalculated transactions for user {user_id} to {new_currency}")
    except Exception as e:
        logger.exception(f"Currency recalculation failed for user {user_id}: {e}")
        try:
            UserProfile.objects.filter(user_id=user_id).update(currency=old_currency)
        except Exception:
            logger.exception(f"Failed to revert profile currency for user {user_id}")

        invalidate_dashboard_cache(user_id=user.id)

        try:
            link = str(reverse_lazy('currency-settings'))
        except Exception:
            link = None

        try:
            Notification.objects.create(
                user=user,
                title=_("Currency Recalculation Failed"),
                message=_("Failed to recalculate transactions to %(currency)s. Your base currency has been reverted to %(old_currency)s.") % {
                    'currency': new_currency,
                    'old_currency': old_currency,
                },
                notification_type='SYSTEM',
                link=link,
            )
        except Exception:
            logger.exception(f"Failed to create failure notification for user {user_id}")
    finally:
        cache.delete(f'currency_recalc_lock_{user_id}')

    return success


def dispatch_currency_recalculation(user_id, old_currency, new_currency, run_async=None):
    """
    Guarded by a user-scoped cache lock to prevent overlapping runs.
    Dispatches recalculate_user_transactions in a background thread by default.
    """
    lock_key = f'currency_recalc_lock_{user_id}'
    if not cache.add(lock_key, 1, timeout=600):
        logger.warning(f"Currency recalculation already in progress for user {user_id}")
        return False

    if run_async is None:
        run_async = getattr(django_settings, 'CURRENCY_RECALCULATION_ASYNC', True)

    def _run():
        try:
            recalculate_user_transactions(user_id, old_currency, new_currency)
        finally:
            cache.delete(lock_key)

    if not run_async:
        _run()
        return True

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return True


class CurrencyUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    fields = ['currency']
    template_name = 'expenses/currency_settings.html'
    success_url = reverse_lazy('currency-settings')

    def get_object(self, queryset=None):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        old_currency = self.get_object().currency
        new_currency = form.cleaned_data.get('currency')
        
        response = super().form_valid(form)
        ph_capture(self.request.user, 'currency_changed', {'old_currency': old_currency, 'new_currency': form.cleaned_data.get('currency', '')})
        
        if old_currency != new_currency:
            dispatched = dispatch_currency_recalculation(self.request.user.id, old_currency, new_currency)
            if dispatched:
                messages.info(
                    self.request,
                    _("Currency preference updated to %(currency)s. Historical transactions are being recalculated in the background.") % {'currency': new_currency}
                )
            else:
                messages.warning(
                    self.request,
                    _("Currency preference updated to %(currency)s, but a recalculation is already in progress.") % {'currency': new_currency}
                )
        else:
            messages.success(self.request, _('Currency preference updated successfully.'))
            
        return response

class LanguageUpdateView(LoginRequiredMixin, UpdateView):
    model = UserProfile
    form_class = LanguageUpdateForm
    template_name = 'expenses/language_settings.html'
    success_url = reverse_lazy('language-settings')

    def get_object(self, queryset=None):
        profile, created = UserProfile.objects.get_or_create(user=self.request.user)
        return profile

    def form_valid(self, form):
        lang = form.cleaned_data.get('language')
        translation.activate(lang)
        messages.success(self.request, _('Language preference updated successfully.'))
        
        response = super().form_valid(form)
        invalidate_dashboard_cache(user_id=self.request.user.id)
        ph_capture(self.request.user, 'language_changed', {'new_language': form.cleaned_data.get('language', '')})
        response.set_cookie(django_settings.LANGUAGE_COOKIE_NAME, lang)
        return response

class ProfileUpdateView(LoginRequiredMixin, UpdateView):
    model = django_settings.AUTH_USER_MODEL
    form_class = ProfileUpdateForm
    template_name = 'expenses/profile_settings.html'
    success_url = reverse_lazy('profile-settings')

    def get_object(self, queryset=None):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['page_title'] = _('Profile Settings')
        user = self.request.user

        # Rarely-changing flags, cached like SettingsHomeView's counts (5 min)
        cache_key = f'profile_settings_flags_{user.id}'
        flags = cache.get(cache_key)
        if flags is None:
            now = timezone.now()
            has_any_data = Expense.objects.filter(user=user).exists() or Income.objects.filter(user=user).exists()
            show_year_in_review = False
            year_in_review_year = None

            if has_any_data:
                # 1. From Nov 1st to Dec 31st, show CURRENT year's review (as it's coming to an end)
                # 2. From Jan 1st to Oct 31st, show PREVIOUS year's review
                year_in_review_year = now.year if now.month >= 11 else now.year - 1
                show_year_in_review = Expense.objects.filter(user=user, date__year=year_in_review_year).exists()

            flags = {
                'is_social_user': SocialAccount.objects.filter(user=user).exists(),
                'show_year_in_review': show_year_in_review,
                'year_in_review_year': year_in_review_year,
            }
            cache.set(cache_key, flags, 300)

        context['is_social_user'] = flags['is_social_user']
        context['show_year_in_review'] = flags['show_year_in_review']
        context['year_in_review_year'] = flags['year_in_review_year']
        
        return context

    def form_valid(self, form):
        messages.success(self.request, _("Profile updated successfully."))
        response = super().form_valid(form)
        invalidate_dashboard_cache(user_id=self.request.user.id)
        ph_capture(self.request.user, 'profile_updated', {})
        return response
