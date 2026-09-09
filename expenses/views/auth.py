import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views import View
from django.views.generic import TemplateView

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

        # Only redirect for GET requests to the onboarding page itself
        if request.method == 'GET' and request.user.profile.has_seen_tutorial:
            if request.GET.get('force') == 'true' or request.GET.get('preview') == 'true':
                return super().dispatch(request, *args, **kwargs)
            return redirect('home')
            
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['currency_choices'] = CURRENCY_CHOICES
        context['language_choices'] = UserProfile.LANGUAGE_CHOICES
        context['current_year'] = date.today().year
        context['current_month'] = date.today().month
        return context

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body)
            step = data.get('step')
            
            if step == 'persona':
                profile = request.user.profile
                profile.persona = data.get('persona')
                profile.save()
                return JsonResponse({'success': True})
            
            elif step == 'salary_setup':
                profile = request.user.profile
                day = data.get('salary_date')
                if day and profile.persona != 'FREELANCER':
                    profile.salary_date = int(day)
                    profile.save()
                return JsonResponse({'success': True})
                
            elif step == 'dismiss_checklist':
                profile = request.user.profile
                profile.dismissed_onboarding_checklist = True
                profile.save()
                return JsonResponse({'success': True})
            
            elif step == 'setup':
                profile = request.user.profile
                profile.currency = data.get('currency', profile.currency)
                profile.language = data.get('language', profile.language)
                # Don't set has_seen_tutorial here, move to final step or skip
                profile.save()
                return JsonResponse({'success': True})
            
            elif step == 'income':
                income_qs = Income.objects.filter(
                    user=request.user,
                    date=date.today(),
                    source=data.get('source', 'Initial Income')
                )
                if income_qs.exists():
                    income = income_qs.first()
                    income.amount = Decimal(data.get('amount', 0))
                    income.currency = request.user.profile.currency
                    if data.get('account_id'):
                        income.account = get_object_or_404(Account, id=data.get('account_id'), user=request.user)
                    income.save()
                else:
                    account = None
                    if data.get('account_id'):
                        account = get_object_or_404(Account, id=data.get('account_id'), user=request.user)
                    Income.objects.create(
                        user=request.user,
                        date=date.today(),
                        source=data.get('source', 'Initial Income'),
                        amount=Decimal(data.get('amount', 0)),
                        currency=request.user.profile.currency,
                        account=account
                    )
                return JsonResponse({'success': True})
            
            elif step == 'accounts':
                accounts_data = data.get('accounts', [])
                currency = request.user.profile.currency
                parsed_accounts = []
                for acc_data in accounts_data:
                    name = (acc_data.get('name') or '').strip()
                    if not name:
                        continue
                    parsed_accounts.append({
                        'name': name,
                        'account_type': acc_data.get('type', 'SAVINGS_ACCOUNT'),
                        'balance': Decimal(acc_data.get('balance', 0)),
                    })

                account_names = list(dict.fromkeys(acc['name'] for acc in parsed_accounts))
                existing_by_name = {
                    acc.name: acc
                    for acc in Account.objects.filter(user=request.user, name__in=account_names)
                }

                to_create = []
                to_update = []
                for acc_data in parsed_accounts:
                    existing = existing_by_name.get(acc_data['name'])
                    if existing:
                        existing.account_type = acc_data['account_type']
                        existing.balance = acc_data['balance']
                        existing.currency = currency
                        to_update.append(existing)
                    else:
                        to_create.append(
                            Account(
                                user=request.user,
                                name=acc_data['name'],
                                account_type=acc_data['account_type'],
                                balance=acc_data['balance'],
                                currency=currency,
                            )
                        )

                if to_create:
                    Account.objects.bulk_create(to_create)
                if to_update:
                    Account.objects.bulk_update(to_update, ['account_type', 'balance', 'currency'])

                refreshed_by_name = {
                    acc.name: acc
                    for acc in Account.objects.filter(user=request.user, name__in=account_names).only('id', 'name')
                }
                created_accounts = [
                    {'id': refreshed_by_name[acc_data['name']].id, 'name': acc_data['name']}
                    for acc_data in parsed_accounts
                    if acc_data['name'] in refreshed_by_name
                ]
                return JsonResponse({'success': True, 'accounts': created_accounts})
            
            elif step == 'budget':
                categories = data.get('categories', [])
                for cat_data in categories:
                    name = cat_data.get('name')
                    limit = cat_data.get('limit')
                    if name:
                        Category.objects.update_or_create(
                            user=request.user,
                            name=name,
                            defaults={'limit': Decimal(limit) if limit else None}
                        )
                return JsonResponse({'success': True})
            
            elif step == 'expense':
                expense_qs = Expense.objects.filter(
                    user=request.user,
                    date=date.today(),
                    description=data.get('description', 'Initial Expense'),
                    category=data.get('category', 'Miscellaneous')
                )
                if expense_qs.exists():
                    expense = expense_qs.first()
                    expense.amount = Decimal(data.get('amount', 0))
                    expense.currency = request.user.profile.currency
                    if data.get('account_id'):
                        expense.account = get_object_or_404(Account, id=data.get('account_id'), user=request.user)
                    expense.save()
                else:
                    account = None
                    if data.get('account_id'):
                        account = get_object_or_404(Account, id=data.get('account_id'), user=request.user)
                    Expense.objects.create(
                        user=request.user,
                        date=date.today(),
                        description=data.get('description', 'Initial Expense'),
                        category=data.get('category', 'Miscellaneous'),
                        amount=Decimal(data.get('amount', 0)),
                        currency=request.user.profile.currency,
                        account=account
                    )
                profile = request.user.profile
                profile.has_seen_tutorial = True
                profile.save()
                return JsonResponse({'success': True})

            elif step == 'recurring':
                recurring_data = data.get('recurring', [])
                for rec_data in recurring_data:
                    from .dashboard import RecurringTransaction
                    RecurringTransaction.objects.update_or_create(
                        user=request.user,
                        description=rec_data.get('description'),
                        transaction_type=rec_data.get('type', 'EXPENSE'),
                        defaults={
                            'amount': Decimal(rec_data.get('amount', 0)),
                            'frequency': rec_data.get('frequency', 'MONTHLY'),
                            'start_date': rec_data.get('start_date', date.today()),
                            'category': rec_data.get('category'),
                            'currency': request.user.profile.currency
                        }
                    )
                return JsonResponse({'success': True})

            elif step == 'finish':
                profile = request.user.profile
                profile.has_seen_tutorial = True
                profile.save()
                return JsonResponse({'success': True})

            elif step == 'skip':
                profile = request.user.profile
                profile.has_seen_tutorial = True
                profile.save()
                return JsonResponse({'success': True})
                
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        
        return JsonResponse({'success': False, 'error': 'Invalid step'}, status=400)

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
        cache.set(cache_key, result, timeout=900)
        return result

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = SubscriptionPlan.objects.filter(is_active=True)
        context['plans_monthly'] = {p.tier: p for p in plans.filter(duration='MONTHLY')}
        context['plans_yearly'] = {p.tier: p for p in plans.filter(duration='YEARLY')}
        context['plans'] = context['plans_yearly']
        context['total_users_count'] = User.objects.count()
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

class PricingView(TemplateView):
    template_name = 'expenses/pricing.html'

    def get_context_data(self, **kwargs):
        from django.conf import settings
        context = super().get_context_data(**kwargs)
        context['RAZORPAY_KEY_ID'] = settings.RAZORPAY_KEY_ID
        plans = SubscriptionPlan.objects.filter(is_active=True)
        context['plans_monthly'] = {p.tier: p for p in plans.filter(duration='MONTHLY')}
        context['plans_yearly'] = {p.tier: p for p in plans.filter(duration='YEARLY')}
        context['plans'] = context['plans_yearly']
        return context

def resend_verification_email(request):
    """
    AJAX view to resend verification email.
    """
    from allauth.account.models import EmailAddress
    from allauth.account.utils import send_email_confirmation
    
    if request.method == 'POST':
        email = request.user.email
        try:
            email_address = EmailAddress.objects.get(user=request.user, email=email)
            if not email_address.verified:
                send_email_confirmation(request, request.user)
                return JsonResponse({'success': True})
            return JsonResponse({'success': False, 'error': 'Already verified'})
        except EmailAddress.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Email not found'})
    return JsonResponse({'success': False}, status=400)

class UpdatePWALoginView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        request.user.last_login = timezone.now()
        request.user.save(update_fields=['last_login'])
        return JsonResponse({'status': 'success'})
