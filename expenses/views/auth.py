import json
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
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView

from ..cache_utils import anonymous_cache_page

logger = logging.getLogger(__name__)

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
            compute_cycle_range,
            get_checklist_items,
            get_or_create_onboarding_state,
            handle_step1,
            handle_step2,
            handle_step3_confirm,
            mark_onboarding_finished,
            record_onboarding_event,
        )

        try:
            data = json.loads(request.body)
            action = str(data.get('step') or data.get('action') or '').strip()

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
                balance_now = _parse_decimal(balance_raw, default=Decimal('0.00')) if balance_raw not in (None, '') else Decimal('0.00')
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
                properties = data.get('properties', {})
                if event_name:
                    record_onboarding_event(request.user, event_name, properties)
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
        response['Link'] = '</llms.txt>; rel="service-doc", </sitemap.xml>; rel="describedby", </.well-known/api-catalog>; rel="api-catalog"'
        return response

    def _get_demo_net_worth_breakdown(self):
        from django.core.cache import cache
        from expenses.account_types import ACCOUNT_TYPES, KIND, classify
        from expenses.ledger_read_service import LedgerReadService

        cache_key = 'landing_demo_networth_breakdown'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        demo_user = User.objects.filter(username='demo').first()
        if not demo_user:
            return []

        try:
            net_worth, base_balances = LedgerReadService.get_net_worth(demo_user)
        except Exception:
            return []

        if not base_balances:
            return []

        account_type_to_group = {}
        for group_name, types in ACCOUNT_TYPES:
            if group_name == 'Legacy':
                continue
            for code, label in types:
                if code not in account_type_to_group:
                    account_type_to_group[code] = group_name

        GROUP_META = {
            'Cash & Bank': {'category': 'cat-cash', 'icon': 'bi-bank', 'label': 'Cash & Bank'},
            'Fixed-Income': {'category': 'cat-fixed', 'icon': 'bi-safe', 'label': 'Fixed-Income'},
            'Investments': {'category': 'cat-invest', 'icon': 'bi-graph-up-arrow', 'label': 'Investments'},
            'Physical Assets': {'category': 'cat-physical', 'icon': 'bi-house-door', 'label': 'Physical Assets'},
            'Short-Term Credit': {'category': 'cat-credit', 'icon': 'bi-credit-card', 'label': 'Credit Card'},
            'Long-Term Loans': {'category': 'cat-loan', 'icon': 'bi-building-down', 'label': 'Loans'},
            'Insurance': {'category': 'cat-insurance', 'icon': 'bi-shield-check', 'label': 'Insurance'},
        }

        # Select representative account (largest absolute valuation) per category group
        accounts_by_id = {acc.id: acc for acc in Account.objects.filter(id__in=base_balances.keys())}
        grouped = {}
        for acc_id, val in base_balances.items():
            account = accounts_by_id.get(acc_id)
            if not account:
                continue
            kind, strat = classify(account.account_type)
            group_name = account_type_to_group.get(account.account_type, 'Cash & Bank')
            abs_val = abs(val)

            if group_name not in grouped or abs_val > grouped[group_name]['abs_value']:
                grouped[group_name] = {
                    'account': account,
                    'value': val,
                    'abs_value': abs_val,
                    'group_name': group_name,
                    'is_liability': (kind == KIND.LIABILITY),
                }

        asset_rows = []
        liability_rows = []

        def _fmt_inr(val):
            val_int = abs(int(round(val)))
            s = str(val_int)
            if len(s) <= 3:
                res = s
            else:
                res = s[-3:]
                s = s[:-3]
                groups = []
                while len(s) > 2:
                    groups.append(s[-2:])
                    s = s[:-2]
                if s:
                    groups.append(s)
                res = ",".join(reversed(groups)) + "," + res
            return res

        for grp_name, data in grouped.items():
            meta = GROUP_META.get(grp_name, {'category': 'cat-cash', 'icon': 'bi-cash', 'label': grp_name})
            row = {
                'name': data['account'].name,
                'category': meta['category'],
                'category_label': meta.get('label', grp_name),
                'icon': meta['icon'],
                'amount': float(data['abs_value']),
                'formatted_amount': _fmt_inr(data['abs_value']),
                'is_liability': data['is_liability'],
            }
            if data['is_liability']:
                liability_rows.append((data['abs_value'], row))
            else:
                asset_rows.append((data['abs_value'], row))

        asset_rows.sort(key=lambda x: x[0], reverse=True)
        liability_rows.sort(key=lambda x: x[0], reverse=True)

        result = [r[1] for r in asset_rows] + [r[1] for r in liability_rows]
        cache.set(cache_key, result, timeout=3600)
        return result

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

        context['demo_networth_rows'] = self._get_demo_net_worth_breakdown()
        return context

class FeaturesPageView(TemplateView):
    template_name = 'features.html'

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Feature categories for better navigation
        context['features'] = [
            {
                'id': 'insights',
                'title': 'Smart Tracking & Insights',
                'description': 'Understand your spending patterns with AI-powered analytics.',
                'items': [
                    {
                        'title': 'Beautiful Bento Dashboard',
                        'desc': 'Your financial life summarized in a modern, glanceable bento grid. Everything from net worth to monthly cash flow in one stunning view.'
                    },
                    {
                        'title': 'Spot spending leaks before they become habits',
                        'desc': 'AI categorises your trends and shows exactly which buckets are growing month over month.'
                    },
                    {
                        'title': 'Your month starts when your salary hits',
                        'desc': 'Not on the 1st. TrackMyRupee follows your actual salary cycle - month-end crunches and all, enabling smart salary-cycle budgeting that fits your real income pattern.'
                    },
                    {
                        'title': 'Watch your net worth grow',
                        'desc': 'One real-time number across savings, SIPs, and credit cards. Includes automatic SIP tracking to monitor your mutual fund investments and watch your trajectory grow.'
                    },
                ]
            },
            {
                'id': 'planning',
                'title': 'Planning & Goals',
                'description': 'Plan ahead and achieve your financial milestones.',
                'items': [
                    {
                        'title': 'Set recurring expenses once, forget them forever.',
                        'desc': 'Set up your SIPs, rent, and subscriptions once. Only log what changes, takes 30 seconds a day.'
                    },
                    {
                        'title': 'See exactly when you\'ll hit your goals',
                        'desc': 'Set a savings target and get a projected date based on your real saving pace - not guesswork.'
                    },
                    {
                        'title': 'Loan Manager with EMI Calculator',
                        'desc': 'Plan before you borrow. Preview your monthly EMI instantly. Track multiple loans, manage floating rates, and visualize your amortization schedule.'
                    },
                    {
                        'title': 'Smart Budgets & Spending Limits',
                        'desc': 'Set monthly category budgets and get real-time alerts when you\'re approaching limits. Visual progress bars show exactly how much you\'ve spent vs your plan.'
                    },
                ]
            },
            {
                'id': 'trust',
                'title': 'Trust & Control',
                'description': 'Your data is yours. Full privacy and data sovereignty with a secure manual expense tracker for India.',
                'items': [
                    {
                        'title': 'Your data is never locked in',
                        'desc': 'Export your full transaction history to CSV anytime. Enjoy the security of a no SMS access expense manager that keeps your financial details private.'
                    },
                    {
                        'title': 'Balanced Double-Entry Ledger',
                        'desc': 'Bank-grade accounting. Every transaction is recorded twice to ensure data integrity. Auto-reconciliation detects discrepancies and keeps your records spotless.'
                    },
                    {
                        'title': 'Extended Account Types',
                        'desc': 'Track Savings, Cash, Credit Cards, Loans, and Investments independently. Includes manual valuation updates for hard-to-track assets like real estate.'
                    }
                ]
            },
            {
                'id': 'experience',
                'title': 'Global & Mobile',
                'description': 'Works everywhere, feels natural in your language.',
                'items': [
                    {
                        'title': 'Advanced Transaction Filters',
                        'desc': 'Find any transaction instantly. Sift through your ledger by custom date ranges, multiple categories, specific accounts, and payment methods.'
                    },
                    {
                        'title': 'Track in your own language',
                        'desc': 'Fully translated in English, Hindi, and Marathi - finance that feels natural to read.'
                    },
                    {
                        'title': 'Multi-currency Ready',
                        'desc': 'Track USD, EUR, or any currency alongside ₹. Useful if you travel, freelance, or hold foreign investments.'
                    },
                    {
                        'title': 'Feels like an app. No install required.',
                        'desc': 'Save it to your home screen (PWA). Works offline for reading, feels as fast as a native app, takes 0MB storage.'
                    },
                    {
                        'title': 'Year in Review & Monthly Reports',
                        'desc': 'Automated financial storytelling. Get your annual spending wrap-up and monthly summaries delivered to your inbox. Understand your money habits at a glance.'
                    },
                ]
            },
        ]
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

def demo_login(request):
    """
    Logs in the read-only 'demo' user without password authentication.
    Ensures data is always fresh (current month).
    """
    list(messages.get_messages(request))

    def setup_demo_user_serialized():
        """Serialize demo setup across workers to avoid deadlocks during heavy reseeding."""
        if connection.vendor == 'postgresql':
            lock_key = 840202607  # Stable advisory lock key for demo setup.
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_lock(%s)", [lock_key])
            try:
                call_command('setup_demo_user')
            finally:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])
        else:
            call_command('setup_demo_user')

    try:
        user = User.objects.get(username='demo')
        today = date.today()

        # With future demo data, last_expense may be in a future month; check current-month data instead.
        has_current_month_expense = Expense.objects.filter(
            user=user,
            date__year=today.year,
            date__month=today.month,
        ).exists()
        has_current_month_income = Income.objects.filter(
            user=user,
            date__year=today.year,
            date__month=today.month,
        ).exists()

        is_stale = not (has_current_month_expense and has_current_month_income)
        
        if is_stale:
            setup_demo_user_serialized()
            user = User.objects.get(username='demo')

    except User.DoesNotExist:
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
