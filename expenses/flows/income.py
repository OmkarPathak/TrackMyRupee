from __future__ import annotations

from datetime import date
from decimal import Decimal


from django import forms
from django.utils.translation import gettext_lazy as _

from django.db.models import Q
from django.urls import reverse

from ..models import Account, CURRENCY_CHOICES, Income, RecurringTransaction, UserProfile
from ..services_recurring import RecurringService
from .base import CreateStep, Flow, FlowSnapshot, FlowWizardStep
from .registry import register_flow


class SalaryFlowForm(forms.Form):
    amount = forms.DecimalField(min_value=Decimal('0.01'), max_digits=15, decimal_places=2, widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}), help_text=_('Monthly salary amount.'))
    currency = forms.ChoiceField(choices=CURRENCY_CHOICES, required=False, widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Currency used for salary tracking.'))
    account = forms.ModelChoiceField(queryset=Account.objects.none(), widget=forms.Select(attrs={'class': 'form-select'}), help_text=_('Account where salary is received.'))
    salary_date = forms.IntegerField(min_value=1, max_value=31, widget=forms.NumberInput(attrs={'class': 'form-control'}), help_text=_('Day of the month salary is usually received.'))
    start_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}), help_text=_('First date to start tracking salary income.'))
    create_historical_entries = forms.BooleanField(required=False, initial=False, widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}), help_text=_('If start date is in the past, create missed salary entries immediately during setup.'))

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        if user:
            accounts = Account.objects.filter(user=user, is_active=True).order_by('name')
            self.fields['account'].queryset = accounts
            self.fields['account'].initial = accounts.filter(name='Cash').first() or accounts.first()
            self.fields['currency'].initial = user.profile.currency


@register_flow
class SalaryFlow(Flow):
    key = 'salary'
    label = _('Salary')
    title = _('I started a new job')
    description = _('Salary, pay day and the salary cycle for your month view.')
    category = 'income'
    icon = 'bi-briefcase'
    tags = [_('Income source'), _('Monthly salary')]
    estimated_time = _('About 1 min')
    creates = [
        _('Recurring income entry'),
        _('Salary date on your profile'),
        _('Updated month-cycle view'),
    ]
    limit_map = {'recurring_transactions': RecurringTransaction}
    form_class = SalaryFlowForm
    wizard_steps = [
        FlowWizardStep('salary_basics', _('Salary Basics'), ['amount', 'currency', 'account'], _('Core salary and receiving-account details.')),
        FlowWizardStep('salary_schedule', _('Salary Schedule'), ['salary_date', 'start_date', 'create_historical_entries'], _('When salary is received, when to begin tracking, and whether to backfill missed entries.')),
    ]

    def is_configured(self, user, snapshot: FlowSnapshot | None = None) -> bool:
        return RecurringTransaction.objects.filter(
            user=user, is_active=True, transaction_type='INCOME'
        ).filter(Q(source='Salary') | Q(description__icontains='salary')).exists()

    def get_edit_url(self, user) -> str:
        return reverse('profile-settings')

    def derive(self, cleaned_data) -> dict:
        data = dict(cleaned_data)
        data['amount'] = Decimal(str(data.get('amount') or 0))
        data['start_date'] = data.get('start_date') or date.today()
        user = data.get('user')
        data['currency'] = data.get('currency') or (data['account'].currency if data.get('account') else (user.profile.currency if user and hasattr(user, 'profile') else '₹'))
        data['frequency'] = 'MONTHLY'
        data['create_historical_entries'] = bool(data.get('create_historical_entries'))
        return data


    def plan(self, data) -> list[CreateStep]:
        last_processed = None if data.get('create_historical_entries') else RecurringService.last_due_before_today(data['start_date'], 'MONTHLY')
        return [
            CreateStep(
                RecurringTransaction,
                {
                    'user': data['user'],
                    'transaction_type': 'INCOME',
                    'amount': data['amount'],
                    'currency': data['currency'],
                    'account': data['account'],
                    'source': 'Salary',
                    'frequency': 'MONTHLY',
                    'start_date': data['start_date'],
                    'last_processed_date': last_processed,
                    'description': _('Salary income'),
                    'is_active': True,
                },
                key='salary',
            )
        ]

    def preview(self, user, cleaned_data) -> dict:
        data = self.derive({**cleaned_data, 'user': user})
        steps = self.plan(data)
        warnings = self.check_limits(user, steps)
        amount = Decimal(str(data.get('amount') or 0))
        return {'headline': amount, 'bullets': [_('Creates a monthly income schedule')], 'warnings': warnings}

    def commit(self, user, cleaned_data, idempotency_key):
        result = super().commit(user, cleaned_data, idempotency_key)
        profile = user.profile
        profile.salary_date = int(cleaned_data.get('salary_date') or profile.salary_date)
        profile.save(update_fields=['salary_date'])

        if cleaned_data.get('create_historical_entries'):
            # Trigger immediate catch-up posting for missed salary occurrences.
            from ..views.mixins import process_user_recurring_transactions

            process_user_recurring_transactions(user, force=True)

        return result

