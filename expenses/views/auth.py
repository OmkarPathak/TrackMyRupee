import json
from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.core.management import call_command
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.translation import gettext as _
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
            
            if step == 'setup':
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
                        'account_type': acc_data.get('type', 'BANK'),
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = SubscriptionPlan.objects.filter(is_active=True)
        context['plans_monthly'] = {p.tier: p for p in plans.filter(duration='MONTHLY')}
        context['plans_yearly'] = {p.tier: p for p in plans.filter(duration='YEARLY')}
        context['plans'] = context['plans_yearly']
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
                        'title': 'Spot spending leaks before they become habits',
                        'desc': 'AI categorises your trends and shows exactly which buckets are growing month over month.'
                    },
                    {
                        'title': 'Your month starts when your salary hits',
                        'desc': 'Not on the 1st. TrackMyRupee follows your actual salary cycle - month-end crunches and all.'
                    },
                    {
                        'title': 'Watch your net worth grow',
                        'desc': 'One real-time number across savings, SIPs, and credit cards. Not a list of transactions - a trajectory.'
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
                'description': 'Your data is yours. Full privacy and data sovereignty.',
                'items': [
                    {
                        'title': 'Your data is never locked in',
                        'desc': 'Export your full transaction history to CSV anytime. No permission needed, no hoops to jump through.'
                    },
                    {
                        'title': 'Balanced Double-Entry Ledger',
                        'desc': 'Bank-grade accounting. Every transaction is recorded twice to ensure data integrity. Auto-reconciliation detects discrepancies and keeps your records spotless.'
                    },
                ]
            },
            {
                'id': 'experience',
                'title': 'Global & Mobile',
                'description': 'Works everywhere, feels natural in your language.',
                'items': [
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
                        'desc': 'Add to your home screen as a PWA on Android or iPhone. Works offline. Launches in under 2 seconds.'
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

    try:
        user = User.objects.get(username='demo')
        last_expense = Expense.objects.filter(user=user).order_by('-date').first()
        is_stale = False
        
        if not last_expense:
            is_stale = True
        else:
            today = date.today()
            if last_expense.date.month != today.month or last_expense.date.year != today.year:
                is_stale = True
        
        if is_stale:
            call_command('setup_demo_user')
            user = User.objects.get(username='demo')

    except User.DoesNotExist:
        call_command('setup_demo_user')
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
