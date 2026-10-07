from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext as _

from .models import (
    Account,
    Category,
    Expense,
    Income,
    OnboardingEvent,
    OnboardingState,
    RecurringTransaction,
    SavingsGoal,
    UserProfile,
)
from .posthog_utils import ph_capture

logger = logging.getLogger(__name__)


def record_onboarding_event(user, event_name: str, properties: Optional[Dict[str, Any]] = None):
    """
    Log privacy-safe onboarding events to the local DB table and PostHog.
    Ensures NO raw amounts, balances, descriptions, or account names are ever recorded.
    """
    safe_props = properties.copy() if properties else {}
    forbidden_keys = {'amount', 'salary', 'balance', 'description', 'account_name', 'account', 'text'}
    filtered_props = {k: v for k, v in safe_props.items() if k not in forbidden_keys}

    try:
        OnboardingEvent.objects.create(
            user=user,
            event=event_name,
            properties=filtered_props,
        )
    except Exception as e:
        logger.warning("Failed to record local OnboardingEvent: %s", e)

    try:
        ph_capture(user, event_name, filtered_props)
    except Exception as e:
        logger.debug("PostHog onboarding capture failed: %s", e)


def compute_cycle_range(payday: int, target_date: Optional[date] = None) -> Tuple[date, date, int]:
    """
    Calculates the current cycle's start date, end date, and days to next payday for a given payday (1-32).
    Payday 32 represents 'Last day of month'.
    Clamps days 29-31 to the final day of shorter months.
    """
    if target_date is None:
        target_date = timezone.localdate()

    year = target_date.year
    month = target_date.month

    def get_effective_day(y: int, m: int, day_choice: int) -> int:
        last_day = calendar.monthrange(y, m)[1]
        if day_choice >= 32 or day_choice < 1:
            return last_day
        return min(day_choice, last_day)

    eff_this_month = get_effective_day(year, month, payday)

    if target_date.day >= eff_this_month:
        # Cycle started in current month
        start_date = date(year, month, eff_this_month)
        next_year = year + 1 if month == 12 else year
        next_month = 1 if month == 12 else month + 1
        eff_next_month = get_effective_day(next_year, next_month, payday)
        next_payday_date = date(next_year, next_month, eff_next_month)
        end_date = next_payday_date - timedelta(days=1)
    else:
        # Cycle started in previous month
        prev_year = year - 1 if month == 1 else year
        prev_month = 12 if month == 1 else month - 1
        eff_prev_month = get_effective_day(prev_year, prev_month, payday)
        start_date = date(prev_year, prev_month, eff_prev_month)
        next_payday_date = date(year, month, eff_this_month)
        end_date = next_payday_date - timedelta(days=1)

    days_to_payday = max(0, (next_payday_date - target_date).days)
    return start_date, end_date, days_to_payday


def get_or_create_onboarding_state(user) -> OnboardingState:
    state, _ = OnboardingState.objects.get_or_create(user=user)
    return state


def handle_step1(user, persona: Optional[str], is_skip: bool = False) -> Dict[str, Any]:
    state = get_or_create_onboarding_state(user)
    profile = user.profile

    if is_skip or not persona:
        state.step1_skipped = True
        state.persona = None
        profile.persona = None
        record_onboarding_event(user, 'onboarding_step_skipped', {'step': 1})
    else:
        valid_personas = {'SALARIED', 'FREELANCER', 'FAMILY', 'STUDENT'}
        chosen_persona = persona.upper() if persona.upper() in valid_personas else 'SALARIED'
        state.persona = chosen_persona
        state.step1_skipped = False
        profile.persona = chosen_persona
        record_onboarding_event(user, 'onboarding_step_completed', {'step': 1, 'persona': chosen_persona})

    profile.save(update_fields=['persona'])
    state.current_step = 2
    state.save()
    return {'success': True, 'step': 2, 'persona': state.persona}


def handle_step2(
    user,
    payday: Optional[int],
    salary_amount: Optional[Decimal],
    bank_choice: Optional[str],
    custom_bank_name: Optional[str],
    balance_now: Optional[Decimal],
    auto_log_salary: bool,
    is_skip: bool = False,
) -> Dict[str, Any]:
    state = get_or_create_onboarding_state(user)
    profile = user.profile

    if is_skip:
        state.step2_skipped = True
        state.current_step = 3
        state.save()
        record_onboarding_event(user, 'onboarding_step_skipped', {'step': 2})
        return {'success': True, 'step': 3}

    today = timezone.localdate()
    chosen_payday = int(payday) if payday and 1 <= int(payday) <= 32 else 1
    actual_salary_date = 1
    if chosen_payday == 32:
        actual_salary_date = calendar.monthrange(today.year, today.month)[1]
    else:
        actual_salary_date = min(chosen_payday, 31)

    profile.salary_date = actual_salary_date
    profile.save(update_fields=['salary_date'])

    cycle_start, cycle_end, _ = compute_cycle_range(chosen_payday, today)

    bank_choice = (bank_choice or '').strip()
    account_name = None
    account_type = 'SAVINGS_ACCOUNT'

    if bank_choice:
        if bank_choice.upper() == 'CASH':
            account_name = 'Cash Wallet'
            account_type = 'CASH_WALLET'
        elif bank_choice.upper() == 'OTHER':
            account_name = (custom_bank_name or '').strip() or 'My Bank Account'
            account_type = 'SAVINGS_ACCOUNT'
        else:
            account_name = f"{bank_choice.upper()} Savings Account"
            account_type = 'SAVINGS_ACCOUNT'

    salary_dec = salary_amount if salary_amount and salary_amount > 0 else None
    balance_dec = balance_now if balance_now is not None else Decimal('0.00')

    with transaction.atomic():
        created_or_updated_account = None
        if account_name:
            if state.account and state.account.is_active:
                created_or_updated_account = state.account
                created_or_updated_account.name = account_name
                created_or_updated_account.account_type = account_type
                created_or_updated_account.currency = profile.currency
            else:
                existing = Account.objects.filter(user=user, name=account_name, is_active=True).first()
                if existing:
                    created_or_updated_account = existing
                else:
                    created_or_updated_account = Account(
                        user=user,
                        name=account_name,
                        account_type=account_type,
                        currency=profile.currency,
                    )

            created_or_updated_account.balance = Decimal('0.00')
            created_or_updated_account.save()
            state.account = created_or_updated_account

        created_or_updated_income = None
        if salary_dec and created_or_updated_account:
            income_source = 'Salary'
            if state.persona == 'FREELANCER':
                income_source = 'Client Income'
            elif state.persona == 'STUDENT':
                income_source = 'Pocket Money / Allowance'

            if state.income and not state.income.is_deleted:
                created_or_updated_income = state.income
                created_or_updated_income.amount = salary_dec
                created_or_updated_income.date = cycle_start
                created_or_updated_income.source = income_source
                created_or_updated_income.account = created_or_updated_account
                created_or_updated_income.currency = profile.currency
                created_or_updated_income.save()
            else:
                created_or_updated_income = Income.objects.create(
                    user=user,
                    date=cycle_start,
                    source=income_source,
                    source_type='Salary',
                    amount=salary_dec,
                    currency=profile.currency,
                    account=created_or_updated_account,
                )
            state.income = created_or_updated_income

        # CRITICAL Balance Correctness requirement:
        # After creating/updating Income, account.balance had `salary_dec` added to it by Income.save().
        # Set account.balance exactly equal to typed balance_dec so displayed balance matches user input.
        if created_or_updated_account:
            Account.objects.filter(pk=created_or_updated_account.pk).update(balance=balance_dec)
            created_or_updated_account.refresh_from_db()

        if auto_log_salary and salary_dec and created_or_updated_account:
            is_last_day = (chosen_payday >= 32)
            rule_defaults = {
                'amount': salary_dec,
                'frequency': 'MONTHLY',
                'start_date': cycle_start,
                'source': created_or_updated_income.source if created_or_updated_income else 'Salary',
                'currency': profile.currency,
                'account': created_or_updated_account,
                'is_last_day_of_month': is_last_day,
                'is_active': True,
            }
            if state.recurring_transaction:
                recurring_obj = state.recurring_transaction
                for k, v in rule_defaults.items():
                    setattr(recurring_obj, k, v)
                recurring_obj.save()
            else:
                recurring_obj = RecurringTransaction.objects.create(
                    user=user,
                    description=f"{rule_defaults['source']} (Auto)",
                    transaction_type='INCOME',
                    **rule_defaults,
                )
                state.recurring_transaction = recurring_obj
        elif not auto_log_salary and state.recurring_transaction:
            state.recurring_transaction.delete()
            state.recurring_transaction = None

        state.salary_day = chosen_payday
        state.salary_amount = salary_dec
        state.balance_now = balance_dec
        state.bank_chip = bank_choice
        state.auto_log_salary = auto_log_salary
        state.step2_skipped = False
        state.current_step = 3
        state.save()

    record_onboarding_event(
        user,
        'onboarding_step_completed',
        {
            'step': 2,
            'has_salary': bool(salary_dec),
            'has_account': bool(created_or_updated_account),
            'has_balance': bool(balance_dec),
            'auto_log': auto_log_salary,
        },
    )

    return {
        'success': True,
        'step': 3,
        'account_id': state.account_id,
        'income_id': state.income_id,
        'salary_amount': str(salary_dec) if salary_dec else None,
    }


def handle_step3_confirm(
    user,
    amount: Decimal,
    description: str,
    category_name: str,
    account_id: Optional[int],
    date_val: Optional[date] = None,
    is_skip: bool = False,
) -> Dict[str, Any]:
    state = get_or_create_onboarding_state(user)
    profile = user.profile

    if is_skip:
        state.step3_skipped = True
        state.current_step = 4
        state.save()
        record_onboarding_event(user, 'onboarding_step_skipped', {'step': 3})
        return {'success': True, 'step': 4}

    if not date_val:
        date_val = timezone.localdate()

    target_account = None
    if account_id:
        target_account = Account.objects.filter(user=user, id=account_id, is_active=True).first()
    if not target_account and state.account and state.account.is_active:
        target_account = state.account

    with transaction.atomic():
        if state.expense and not state.expense.is_deleted:
            expense_obj = state.expense
            expense_obj.amount = amount
            expense_obj.description = description or "Expense"
            expense_obj.category = category_name or "Miscellaneous"
            expense_obj.date = date_val
            expense_obj.account = target_account
            expense_obj.currency = profile.currency
            expense_obj.save()
        else:
            expense_obj = Expense.objects.create(
                user=user,
                amount=amount,
                description=description or "Expense",
                category=category_name or "Miscellaneous",
                date=date_val,
                account=target_account,
                currency=profile.currency,
                payment_method='Cash' if (target_account and target_account.account_type == 'CASH_WALLET') else 'Debit Card',
            )
            state.expense = expense_obj

        state.step3_skipped = False
        state.current_step = 4
        state.save()

    record_onboarding_event(user, 'quick_add_confirmed', {'step': 3, 'has_account': bool(target_account)})
    record_onboarding_event(user, 'onboarding_step_completed', {'step': 3})
    return {'success': True, 'step': 4, 'expense_id': state.expense_id}


def mark_onboarding_finished(user) -> Dict[str, Any]:
    state = get_or_create_onboarding_state(user)
    profile = user.profile
    now = timezone.now()

    profile.has_seen_tutorial = True
    profile.onboarding_completed_at = now
    profile.save(update_fields=['has_seen_tutorial', 'onboarding_completed_at'])

    state.completed_at = now
    state.current_step = 4
    state.save()

    record_onboarding_event(user, 'onboarding_completed', {'persona': state.persona or 'NONE'})
    return {'success': True}


def get_checklist_items(user) -> Tuple[List[Dict[str, Any]], int, int]:
    """
    Computes checklist items dynamically based on user persona and missing onboarding pieces.
    Queries use cheap .exists() calls.
    Returns (items, completed_count, total_count).
    """
    state = get_or_create_onboarding_state(user)
    profile = getattr(user, 'profile', None)
    persona = getattr(profile, 'persona', None) or 'SALARIED'

    missing_items = []
    has_payday = bool(getattr(profile, 'salary_date', None) and profile.salary_date > 1)
    has_salary = Income.objects.filter(user=user, is_deleted=False).exists()
    has_account = Account.objects.filter(user=user, is_active=True).exists()
    has_expense = Expense.objects.filter(user=user, is_deleted=False).exists()

    if state.step2_skipped or not has_payday:
        missing_items.append({
            'id': 'set_payday',
            'title': _('Set your payday'),
            'description': _('Align budget months to when money arrives'),
            'time': '20 sec',
            'flow_url': '/settings/profile/',
            'done': has_payday,
        })
    if state.step2_skipped or not has_salary:
        missing_items.append({
            'id': 'add_salary',
            'title': _('Add your salary'),
            'description': _('Track your monthly cash inflow'),
            'time': '30 sec',
            'flow_url': '/flows/salary/',
            'done': has_salary,
        })
    if state.step2_skipped or not has_account:
        missing_items.append({
            'id': 'add_account',
            'title': _('Add an account'),
            'description': _('Keep balances across bank and cash'),
            'time': '30 sec',
            'flow_url': '/accounts/add/',
            'done': has_account,
        })
    if state.step3_skipped or not has_expense:
        missing_items.append({
            'id': 'log_first_expense',
            'title': _('Log your first expense'),
            'description': _('Type any expense to see Left this month change'),
            'time': '15 sec',
            'flow_url': '/dashboard/',
            'done': has_expense,
        })

    standard_items = [
        {
            'id': 'setup_basics',
            'title': _('Set up your basics'),
            'description': _('Completed in onboarding'),
            'time': '',
            'flow_url': '',
            'done': True,
        }
    ]

    has_credit_card = Account.objects.filter(user=user, is_active=True, account_type='CREDIT_CARD').exists()
    has_investments = Account.objects.filter(
        user=user, is_active=True, account_type__in=['MUTUAL_FUND', 'DEMAT', 'SIP', 'FD', 'RD', 'PPF', 'EPF', 'NPS', 'GOLD']
    ).exists()
    from .models import Loan
    has_loan = Loan.objects.filter(user=user, is_active=True).exists()
    has_goal = SavingsGoal.objects.filter(user=user).exists()
    has_language_or_pwa = (profile.language != 'en') if profile else False
    has_recurring_bill = RecurringTransaction.objects.filter(user=user, transaction_type='EXPENSE', is_active=True).exists()

    if persona == 'FREELANCER':
        standard_items.extend([
            {
                'id': 'log_client_payment',
                'title': _('Log a client payment'),
                'description': _('Track client invoicing and variable income'),
                'time': '30 sec',
                'flow_url': '/flows/income/',
                'done': Income.objects.filter(user=user, source_type='Freelance / Consulting', is_deleted=False).exists(),
            },
            {
                'id': 'add_recurring_bills',
                'title': _('Add recurring bills'),
                'description': _('Automate software subscriptions and bills'),
                'time': '45 sec',
                'flow_url': '/flows/rentbill/',
                'done': has_recurring_bill,
            },
            {
                'id': 'add_credit_card',
                'title': _('Add a credit card'),
                'description': _('Track limits and due dates'),
                'time': '30 sec',
                'flow_url': '/flows/creditcard/',
                'done': has_credit_card,
            },
            {
                'id': 'set_savings_goal',
                'title': _('Set a savings goal'),
                'description': _('Get a projected target date'),
                'time': '30 sec',
                'flow_url': '/flows/savingsgoal/',
                'done': has_goal,
            },
            {
                'id': 'switch_language',
                'title': _('Switch language or install the app'),
                'description': _('Hindi, Marathi, offline-ready'),
                'time': '20 sec',
                'flow_url': '/settings/language/',
                'done': has_language_or_pwa,
            },
        ])
    elif persona == 'FAMILY':
        standard_items.extend([
            {
                'id': 'add_recurring_bills',
                'title': _('Add recurring bills'),
                'description': _('House rent, utilities, and school fees'),
                'time': '45 sec',
                'flow_url': '/flows/rentbill/',
                'done': has_recurring_bill,
            },
            {
                'id': 'add_loan_emi',
                'title': _('Add a loan or EMI'),
                'description': _('Repayments logged automatically'),
                'time': '1 min',
                'flow_url': '/flows/loan/',
                'done': has_loan,
            },
            {
                'id': 'set_savings_goal',
                'title': _('Set a shared savings goal'),
                'description': _('Family vacation, emergency fund, or home'),
                'time': '30 sec',
                'flow_url': '/flows/savingsgoal/',
                'done': has_goal,
            },
            {
                'id': 'add_credit_card',
                'title': _('Add a credit card'),
                'description': _('Track limits and billing cycles'),
                'time': '30 sec',
                'flow_url': '/flows/creditcard/',
                'done': has_credit_card,
            },
            {
                'id': 'switch_language',
                'title': _('Switch language or install the app'),
                'description': _('Hindi, Marathi, offline-ready'),
                'time': '20 sec',
                'flow_url': '/settings/language/',
                'done': has_language_or_pwa,
            },
        ])
    elif persona == 'STUDENT':
        standard_items.extend([
            {
                'id': 'add_recurring_bills',
                'title': _('Add recurring bills or subscriptions'),
                'description': _('Spotify, Netflix, hostel rent'),
                'time': '30 sec',
                'flow_url': '/flows/rentbill/',
                'done': has_recurring_bill,
            },
            {
                'id': 'set_savings_goal',
                'title': _('Set a savings goal'),
                'description': _('Save for a laptop, course, or trip'),
                'time': '30 sec',
                'flow_url': '/flows/savingsgoal/',
                'done': has_goal,
            },
            {
                'id': 'add_sips_investments',
                'title': _('Add SIPs and investments'),
                'description': _('Start building wealth early'),
                'time': '1 min',
                'flow_url': '/flows/sip/',
                'done': has_investments,
            },
            {
                'id': 'switch_language',
                'title': _('Switch language or install the app'),
                'description': _('Hindi, Marathi, offline-ready'),
                'time': '20 sec',
                'flow_url': '/settings/language/',
                'done': has_language_or_pwa,
            },
        ])
    else:
        standard_items.extend([
            {
                'id': 'add_credit_card',
                'title': _('Add a credit card'),
                'description': _('Track limits and due dates'),
                'time': '30 sec',
                'flow_url': '/flows/creditcard/',
                'done': has_credit_card,
            },
            {
                'id': 'add_sips_investments',
                'title': _('Add SIPs and investments'),
                'description': _('See maturity projections'),
                'time': '1 min',
                'flow_url': '/flows/sip/',
                'done': has_investments,
            },
            {
                'id': 'add_loan_emi',
                'title': _('Add a loan or EMI'),
                'description': _('Repayments logged automatically'),
                'time': '1 min',
                'flow_url': '/flows/loan/',
                'done': has_loan,
            },
            {
                'id': 'set_savings_goal',
                'title': _('Set a savings goal'),
                'description': _('Get a target date'),
                'time': '30 sec',
                'flow_url': '/flows/savingsgoal/',
                'done': has_goal,
            },
            {
                'id': 'switch_language',
                'title': _('Switch language or install the app'),
                'description': _('Hindi, Marathi, offline-ready'),
                'time': '20 sec',
                'flow_url': '/settings/language/',
                'done': has_language_or_pwa,
            },
        ])

    all_items = missing_items + standard_items
    total_count = len(all_items)
    done_count = sum(1 for item in all_items if item['done'])
    return all_items, done_count, total_count
