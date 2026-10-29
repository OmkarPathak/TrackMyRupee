import json
import threading
import logging
from datetime import date
from decimal import Decimal, InvalidOperation


def _parse_decimal(val, default=Decimal('0.00')):
    """Safely parse decimal inputs, handling empty strings, formatted currency, and invalid syntax."""
    if val in (None, '', []):
        return default
    try:
        if isinstance(val, str):
            cleaned = val.replace('₹', '').replace('$', '').replace('€', '').replace(',', '').strip()
            if not cleaned:
                return default
            return Decimal(cleaned)
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return default


from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView

from ..cache_utils import anonymous_cache_page

logger = logging.getLogger(__name__)

from ..public_features import feature_categories
from ..models import (
    CURRENCY_CHOICES,
    Account,
    Category,
    Expense,
    Income,
    SubscriptionPlan,
    UserProfile,
)
from ..services import LoanService


class OnboardingView(LoginRequiredMixin, TemplateView):
    template_name = 'onboarding.html'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        if request.method == 'GET' and request.user.profile.has_seen_tutorial:
            if request.GET.get('force') == 'true' or request.GET.get('preview') == 'true':
                return super().dispatch(request, *args, **kwargs)
            return redirect('home')

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from ..onboarding_v2 import (
            compute_cycle_range,
            get_checklist_items,
            get_or_create_onboarding_state,
            record_onboarding_event,
        )

        context = super().get_context_data(**kwargs)
        user = self.request.user
        state = get_or_create_onboarding_state(user)

        today = timezone.localdate()
        payday = state.salary_day or user.profile.salary_date or 1
        cycle_start, cycle_end, days_to_payday = compute_cycle_range(payday, today)

        checklist, done_count, total_count = get_checklist_items(user)

        state_data = {
            'current_step': state.current_step,
            'persona': state.persona or getattr(user.profile, 'persona', 'SALARIED'),
            'salary_day': payday,
            'salary_amount': str(state.salary_amount) if state.salary_amount else '',
            'balance_now': str(state.balance_now) if state.balance_now is not None else '',
            'bank_chip': state.bank_chip or 'SBI',
            'auto_log_salary': state.auto_log_salary,
            'step1_skipped': state.step1_skipped,
            'step2_skipped': state.step2_skipped,
            'step3_skipped': state.step3_skipped,
            'cycle_start_str': cycle_start.strftime('%d %b'),
            'cycle_end_str': cycle_end.strftime('%d %b'),
            'days_to_payday': days_to_payday,
            'expense': {
                'id': state.expense.id if state.expense else None,
                'amount': str(state.expense.amount) if state.expense else '',
                'description': state.expense.description if state.expense else '',
                'category': state.expense.category if state.expense else 'Food & Dining',
                'account': state.expense.account.name if (state.expense and state.expense.account) else '',
                'date': state.expense.date.isoformat() if state.expense else today.isoformat(),
            } if state.expense else None,
        }

        context['state_json'] = json.dumps(state_data)
        context['checklist_json'] = json.dumps({
            'items': checklist,
            'done_count': done_count,
            'total_count': total_count,
        })
        context['currency_symbol'] = user.profile.currency or '₹'
        context['categories'] = list(Category.objects.filter(user=user).values_list('name', flat=True)) or [
            'Food & Dining', 'Groceries', 'Shopping', 'Bills & Utilities', 'Entertainment', 'Travel', 'Health', 'Miscellaneous'
        ]
        context['accounts'] = list(Account.objects.filter(user=user, is_active=True).values('id', 'name'))

        # Track event when onboarding opens
        record_onboarding_event(user, 'onboarding_started', {'current_step': state.current_step})
        record_onboarding_event(user, 'onboarding_step_viewed', {'step': state.current_step})

        return context

    def post(self, request, *args, **kwargs):
        from ..onboarding_v2 import (
            CLIENT_EVENTS,
            compute_cycle_range,
            get_checklist_items,
            get_or_create_onboarding_state,
            handle_step1,
            handle_step2,
            handle_step3_confirm,
            mark_onboarding_finished,
            record_onboarding_event,
            sanitize_client_properties,
        )

        try:
            data = json.loads(request.body)
            action = str(data.get('step') or data.get('action') or '').strip()

            # The shared demo account is read-only (the middleware lets this URL through for the tour's
            # analytics and checklist), so the steps that would create or change data do nothing for it.
            if request.user.username == 'demo' and action not in ('event', 'checklist', 'cycle_range'):
                return JsonResponse({'success': True, 'demo': True})

            if action == 'dismiss_checklist':
                profile = request.user.profile
                profile.dismissed_onboarding_checklist = True
                profile.save(update_fields=['dismissed_onboarding_checklist'])
                record_onboarding_event(request.user, 'checklist_dismissed')
                return JsonResponse({'success': True})

            elif action == 'cycle_range':
                payday = int(data.get('payday', 1))
                c_start, c_end, days_left = compute_cycle_range(payday)
                return JsonResponse({
                    'success': True,
                    'start_str': c_start.strftime('%d %b'),
                    'end_str': c_end.strftime('%d %b'),
                    'days_left': days_left,
                })

            elif action == 'step1':
                persona = data.get('persona')
                is_skip = bool(data.get('skip', False))
                res = handle_step1(request.user, persona, is_skip=is_skip)
                return JsonResponse(res)

            elif action == 'step2':
                is_skip = bool(data.get('skip', False))
                payday = data.get('payday')
                salary_raw = data.get('salary_amount')
                salary_amount = _parse_decimal(salary_raw, default=None) if salary_raw not in (None, '') else None
                balance_raw = data.get('balance_now')
                # blank means "not typed" (None), which is different from typing 0
                balance_now = _parse_decimal(balance_raw, default=Decimal('0.00')) if balance_raw not in (None, '') else None
                bank_choice = data.get('bank_choice')
                custom_bank_name = data.get('custom_bank_name')
                auto_log = bool(data.get('auto_log_salary', True))

                res = handle_step2(
                    user=request.user,
                    payday=payday,
                    salary_amount=salary_amount,
                    bank_choice=bank_choice,
                    custom_bank_name=custom_bank_name,
                    balance_now=balance_now,
                    auto_log_salary=auto_log,
                    is_skip=is_skip,
                )
                return JsonResponse(res)

            elif action == 'step3_confirm':
                is_skip = bool(data.get('skip', False))
                if is_skip:
                    res = handle_step3_confirm(
                        user=request.user,
                        amount=Decimal('0.00'),
                        description='',
                        category_name='',
                        account_id=None,
                        is_skip=True,
                    )
                    return JsonResponse(res)

                amount = _parse_decimal(data.get('amount'), default=Decimal('0.00'))
                if amount <= 0:
                    return JsonResponse({'success': False, 'error': _('Enter an amount greater than zero.')}, status=400)
                description = (data.get('description') or 'Expense').strip()
                category = (data.get('category') or 'Miscellaneous').strip()
                account_id = data.get('account_id')
                date_str = data.get('date')
                parsed_date = None
                if date_str:
                    try:
                        parsed_date = date.fromisoformat(date_str)
                    except Exception:
                        parsed_date = timezone.localdate()

                res = handle_step3_confirm(
                    user=request.user,
                    amount=amount,
                    description=description,
                    category_name=category,
                    account_id=account_id,
                    date_val=parsed_date,
                    is_skip=False,
                )
                return JsonResponse(res)

            elif action in ('finish', 'complete'):
                res = mark_onboarding_finished(request.user)
                return JsonResponse(res)

            elif action == 'skip_all':
                mark_onboarding_finished(request.user)
                return JsonResponse({'success': True})

            elif action == 'event':
                event_name = data.get('event')
                if event_name in CLIENT_EVENTS:
                    record_onboarding_event(request.user, event_name, sanitize_client_properties(data.get('properties')))
                return JsonResponse({'success': True})

            elif action == 'checklist':
                items, done, total = get_checklist_items(request.user)
                return JsonResponse({'success': True, 'items': items, 'done_count': done, 'total_count': total})

            return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)

        except (RuntimeError, ValidationError, InvalidOperation, ValueError) as e:
            logger.warning("Onboarding step validation error: %s", e)
            return JsonResponse({
                'success': False,
                'error': _('Unable to save onboarding data. Please check entered values.')
            }, status=400)
        except Exception as e:
            logger.error("Unexpected error during onboarding: %s", e, exc_info=True)
            return JsonResponse({
                'success': False,
                'error': _('Something went wrong, please try again.')
            }, status=400)

class LandingPageView(TemplateView):
    template_name = 'landing.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        response['Link'] = '</llms.txt>; rel="service-doc", </sitemap.xml>; rel="describedby"'
        # A shared cache (the CDN) may keep the English page for anonymous visitors for a few minutes. Anyone with a
        # session or a chosen language, or who reads another language, always gets their own copy.
        shareable = (response.status_code == 200 and (translation.get_language() or '').startswith('en')
                     and settings.SESSION_COOKIE_NAME not in request.COOKIES
                     and settings.LANGUAGE_COOKIE_NAME not in request.COOKIES)
        response['Cache-Control'] = ('public, max-age=0, s-maxage=300, stale-while-revalidate=3600'
                                     if shareable else 'private, no-cache')
        return response

    def get_context_data(self, **kwargs):
        from django.core.cache import cache
        context = super().get_context_data(**kwargs)
        plans = SubscriptionPlan.objects.filter(is_active=True)
        context['plans_monthly'] = {p.tier: p for p in plans.filter(duration='MONTHLY')}
        context['plans_yearly'] = {p.tier: p for p in plans.filter(duration='YEARLY')}
        context['plans'] = context['plans_yearly']

        total_users_count = cache.get('landing_total_users_count')
        if total_users_count is None:
            total_users_count = User.objects.count()
            cache.set('landing_total_users_count', total_users_count, timeout=3600)
        context['total_users_count'] = total_users_count

        return context

class FeaturesPageView(TemplateView):
    template_name = 'features.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['features'] = feature_categories()
        return context


class LoanEMICalculatorPageView(TemplateView):
    template_name = 'loan_emi_calculator.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        default_principal = 1000000
        default_rate = 10.5
        default_months = 60
        default_emi = LoanService.calculate_emi(default_principal, default_rate, default_months)

        context.update({
            'default_principal': default_principal,
            'default_rate': default_rate,
            'default_months': default_months,
            'default_emi': default_emi,
            'default_total_payment': default_emi * default_months,
            'default_total_interest': (default_emi * default_months) - default_principal,
        })
        return context

_DEMO_SETUP_LOCK = threading.Lock()


def demo_login(request):
    """
    Logs in the read-only 'demo' user without password authentication.
    Ensures data is always fresh (current month).
    """
    list(messages.get_messages(request))

    def demo_is_stale():
        # With future demo data, last_expense may be in a future month; check current-month data instead.
        demo = User.objects.filter(username='demo').first()
        if demo is None:
            return True
        today = date.today()
        has_expense = Expense.objects.filter(user=demo, date__year=today.year, date__month=today.month).exists()
        has_income = Income.objects.filter(user=demo, date__year=today.year, date__month=today.month).exists()
        return not (has_expense and has_income)

    def setup_demo_user_serialized():
        """Serialize demo setup so visitors arriving together reseed once, not once each.

        Whoever waits on the lock re-checks afterwards: if the visitor ahead of them already
        refreshed the data there is nothing left to do (a second reseed would wipe the data the
        first one just created while other visitors are browsing it).
        """
        with _DEMO_SETUP_LOCK:
            if connection.vendor == 'postgresql':
                lock_key = 840202607  # Stable advisory lock key for demo setup (across workers).
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [lock_key])
                try:
                    if demo_is_stale():
                        call_command('setup_demo_user')
                finally:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])
            elif demo_is_stale():
                call_command('setup_demo_user')

    if demo_is_stale():
        setup_demo_user_serialized()
    user = User.objects.get(username='demo')

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    messages.success(request, _("🚀 Welcome to Demo Mode! Feel free to explore the app."))
    return redirect('home')

def demo_signup(request):
    """
    Logs out the demo user and redirects to the signup page.
    """
    logout(request)
    return redirect('account_signup')

@method_decorator(anonymous_cache_page(3600), name='dispatch')
class PricingView(TemplateView):
    template_name = 'expenses/pricing.html'

    def get_context_data(self, **kwargs):
        from django.conf import settings
        context = super().get_context_data(**kwargs)
        context['RAZORPAY_KEY_ID'] = settings.RAZORPAY_KEY_ID
        all_plans = list(SubscriptionPlan.objects.filter(is_active=True))
        context['plans_monthly'] = {p.tier: p for p in all_plans if p.duration == 'MONTHLY'}
        context['plans_yearly'] = {p.tier: p for p in all_plans if p.duration == 'YEARLY'}
        context['plans'] = context['plans_yearly']
        return context

@login_required
def resend_verification_email(request):
    """
    AJAX view to resend verification email.

    Rate-limited to prevent email bombing:
      - Active user check: Inactive users cannot send emails.
      - Per-user/email DB-backed cooldown: 15 minutes between consecutive sends.
      - Per-user/email DB-backed daily cap: 3 sends per 24 h window.
      - Secondary cache-backed cooldown (300 s) for fast non-DB rejection.
    """
    from django.core.cache import cache

    from allauth.account.internal.flows.email_verification import (
        send_verification_email_for_user,
    )
    from allauth.account.models import EmailAddress
    from expenses.adapters import CustomAccountAdapter

    if request.method != 'POST':
        return JsonResponse({'success': False}, status=400)

    user = request.user

    # 0. Active user check
    if not user.is_active:
        return JsonResponse(
            {'success': False, 'error': 'Account is inactive.'},
            status=403,
        )

    # 1. DB-backed rate limiting check (via CustomAccountAdapter)
    adapter = CustomAccountAdapter()
    is_blocked, error_message = adapter.is_email_bomb_risk(user.email, user=user)
    if is_blocked:
        return JsonResponse(
            {'success': False, 'error': str(error_message)},
            status=429,
        )

    # 2. Fast cache-backed cooldown check
    COOLDOWN_SECONDS = 900   # 15 minutes
    MAX_PER_DAY = 3          # hard daily cap

    cooldown_key = f'resend_verify_cooldown_{user.pk}'
    daily_key    = f'resend_verify_daily_{user.pk}'

    if cache.get(cooldown_key):
        return JsonResponse(
            {'success': False, 'error': 'Please wait 15 minutes before requesting another email.'},
            status=429,
        )

    daily_count = cache.get(daily_key, 0)
    if daily_count >= MAX_PER_DAY:
        return JsonResponse(
            {'success': False, 'error': 'You have reached the daily limit for verification emails. Please try again tomorrow.'},
            status=429,
        )

    email = user.email
    try:
        email_address = EmailAddress.objects.get(user=user, email=email)
        if not email_address.verified:
            send_verification_email_for_user(request, user)

            # Arm cooldown and increment daily counter
            cache.set(cooldown_key, True, timeout=COOLDOWN_SECONDS)
            cache.set(daily_key, daily_count + 1, timeout=86400)

            return JsonResponse({'success': True})
        return JsonResponse({'success': False, 'error': 'Already verified'})
    except EmailAddress.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Email not found'})

class UpdatePWALoginView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        request.user.last_login = timezone.now()
        request.user.save(update_fields=['last_login'])
        return JsonResponse({'status': 'success'})
